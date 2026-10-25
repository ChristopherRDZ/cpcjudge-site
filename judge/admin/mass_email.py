"""Emails to users: write one in the admin, see who will receive it, try it, and send it through Resend.

Only superusers and whoever has `judge.send_mass_email` see this section. Once an email is sent it cannot be
edited; "Duplicate" makes a new draft from it. See judge/mass_email.py for who receives what.
"""
import json
import uuid
from datetime import datetime, timedelta

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse, reverse_lazy
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext, gettext_lazy as _, ngettext
from django.views.decorators.http import require_POST

from judge.mass_email import FILTER_FIELDS, MANY_FIELDS, NAME_PLACEHOLDERS, filters_from_form, filters_of, \
    has_filters, preview_fragment, recipient_rows, render_email, reply_to, sender
from judge.models import MassEmail, MassEmailRecipient
from judge.utils.celery import task_status_url_by_id
from judge.utils.resend import ResendClient, ResendError, is_configured
from judge.widgets import AdminHeavySelect2MultipleWidget, AdminMartorWidget, AdminSelect2MultipleWidget

PERMISSION = 'judge.send_mass_email'
# The page of the email itself, and the frames of the preview, run no script and load nothing but images.
EMAIL_CSP = "default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'"
SCHEDULE_MIN = timedelta(minutes=5)
SCHEDULE_MAX = timedelta(days=30)
# Handing an email to Resend takes a few minutes at most. Longer than this, the task died on the way (Celery was
# restarted, the machine went down) and retrying is safe: it continues where it stopped.
STUCK_AFTER = timedelta(minutes=15)
# Delivery figures are counted page by page in Resend; above this they are left to its dashboard.
STATS_LIMIT = 3000
STATS_KINDS = ('delivered', 'bounced', 'complained', 'unsubscribed', 'suppressed')


def stuck():
    return Q(status=MassEmail.SENDING, updated__lt=timezone.now() - STUCK_AFTER)


def recipient_widgets():
    return {
        'contests': AdminHeavySelect2MultipleWidget(data_view='contest_select2'),
        'organizations': AdminHeavySelect2MultipleWidget(data_view='organization_select2'),
        'groups': AdminSelect2MultipleWidget,
        'include_users': AdminHeavySelect2MultipleWidget(data_view='profile_select2'),
        'exclude_users': AdminHeavySelect2MultipleWidget(data_view='profile_select2'),
    }


class RecipientForm(forms.ModelForm):
    """Only the filters: validates them for the live count and the list, without needing the rest."""
    class Meta:
        model = MassEmail
        fields = FILTER_FIELDS

    def clean(self):
        cleaned = super().clean()
        after, before = cleaned.get('joined_after'), cleaned.get('joined_before')
        if after and before and after > before:
            raise forms.ValidationError(gettext('The sign-up dates are reversed: the first one is after the second.'))
        return cleaned


class MassEmailForm(RecipientForm):
    class Meta:
        model = MassEmail
        fields = ('subject', 'preheader', 'heading', 'language', 'content') + FILTER_FIELDS
        widgets = dict(recipient_widgets(), content=AdminMartorWidget(
            attrs={'data-markdownfy-url': reverse_lazy('admin:judge_massemail_fragment')}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Choosing contests, organizations or groups here must not offer to create one. The admin wraps the widgets
        # after formfield_for_manytomany, so it is switched off here, as in BlogPostForm.
        for name in MANY_FIELDS:
            if name in self.fields and hasattr(self.fields[name].widget, 'can_add_related'):
                self.fields[name].widget.can_add_related = False

    def clean_subject(self):
        subject = self.cleaned_data['subject']
        if any(placeholder in subject for placeholder in NAME_PLACEHOLDERS):
            raise forms.ValidationError(gettext('The name of each person can only go in the content, not in the '
                                                'subject.'))
        return subject


class MassEmailAdmin(admin.ModelAdmin):
    form = MassEmailForm
    change_form_template = 'admin/judge/massemail/change_form.html'
    fieldsets = (
        (None, {'fields': ('subject', 'preheader', 'heading', 'language')}),
        (_('Content'), {'fields': ('content',)}),
        (_('Recipients'), {
            'fields': ('contests', 'include_virtual', 'organizations', 'groups', 'active_within_days',
                       ('joined_after', 'joined_before'), 'min_problems', 'include_users', 'exclude_users'),
            'description': _('Only accounts with a confirmed email receive it. Without filters, all of them do. '
                             'With several filters, people must meet all of them; inside one filter, one of the '
                             'choices is enough.'),
        }),
    )
    list_display = ('subject', 'status_label', 'recipient_column', 'when', 'created_by')
    list_display_links = ('subject',)
    list_filter = ('status',)
    search_fields = ('subject',)
    date_hierarchy = 'created'

    # --- permissions -------------------------------------------------------------------------------------------

    @staticmethod
    def allowed(request):
        return request.user.is_active and request.user.has_perm(PERMISSION)

    def has_module_permission(self, request):
        return self.allowed(request)

    def has_view_permission(self, request, obj=None):
        return self.allowed(request)

    def has_add_permission(self, request):
        return self.allowed(request)

    def has_change_permission(self, request, obj=None):
        return self.allowed(request) and (obj is None or obj.is_editable)

    def has_delete_permission(self, request, obj=None):
        # Sent emails stay as a record of what went out and to whom.
        return self.allowed(request) and (obj is None or obj.status in (MassEmail.DRAFT, MassEmail.CANCELLED))

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('created_by__user')

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        # The boxes are select2: the admin's «hold down Control» note does not apply to them.
        field = super().formfield_for_manytomany(db_field, request, **kwargs)
        field.help_text = db_field.help_text
        return field

    # --- list --------------------------------------------------------------------------------------------------

    @admin.display(description=_('status'), ordering='status')
    def status_label(self, obj):
        colors = {MassEmail.DRAFT: '#777777', MassEmail.SENDING: '#1958c1', MassEmail.SCHEDULED: '#b07800',
                  MassEmail.SENT: '#00a92a', MassEmail.FAILED: '#d52020', MassEmail.CANCELLED: '#777777'}
        return format_html('<span style="color:{};font-weight:bold;">{}</span>', colors.get(obj.status, '#333'),
                           obj.get_status_display())

    @admin.display(description=_('recipients'), ordering='recipient_count')
    def recipient_column(self, obj):
        return obj.recipient_count if obj.recipient_count is not None else '-'

    @admin.display(description=_('date'))
    def when(self, obj):
        moment = obj.scheduled_at if obj.status == MassEmail.SCHEDULED else obj.sent_at
        return timezone.localtime(moment).strftime('%Y-%m-%d %H:%M') if moment else '-'

    # --- form --------------------------------------------------------------------------------------------------

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.profile
        super().save_model(request, obj, form, change)

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        extra_context = extra_context or {}
        obj = self.get_object(request, object_id) if object_id else None
        extra_context.update({
            'resend_configured': is_configured(),
            'recipient_fields': list(FILTER_FIELDS),
            'mass_email': obj,
        })
        if obj is not None and not obj.is_editable:
            extra_context.update(self.status_context(obj))
        return super().changeform_view(request, object_id, form_url, extra_context)

    @staticmethod
    def status_context(mail):
        def moment(value):
            return timezone.localtime(value).strftime('%Y-%m-%d %H:%M') if value else '-'

        count = mail.recipient_count or 0
        lines = {
            MassEmail.SCHEDULED: gettext('Scheduled for %(when)s, to %(count)d people.') %
            {'when': moment(mail.scheduled_at), 'count': count},
            MassEmail.SENT: gettext('Handed to Resend on %(when)s, for %(count)d people.') %
            {'when': moment(mail.sent_at), 'count': count},
            MassEmail.FAILED: gettext('It could not be completed. The error is below.'),
            MassEmail.CANCELLED: gettext('Cancelled before it was sent.'),
        }
        skipped = mail.recipients.filter(state=MassEmailRecipient.UNSUBSCRIBED).count()
        line = lines.get(mail.status, '')
        if skipped and mail.status in (MassEmail.SCHEDULED, MassEmail.SENT):
            line += ' ' + ngettext('%(count)d person had unsubscribed and was left out.',
                                   '%(count)d people had unsubscribed and were left out.', skipped) % {'count': skipped}
        resend_labels = {'draft': gettext('draft'), 'scheduled': gettext('scheduled'), 'queued': gettext('queued'),
                         'sending': gettext('sending'), 'sent': gettext('sent'), 'canceled': gettext('cancelled')}
        stats = json.loads(mail.stats) if mail.stats else {}
        stat_labels = {'delivered': gettext('Delivered to the inbox'), 'bounced': gettext('Bounced'),
                       'complained': gettext('Marked as spam'), 'unsubscribed': gettext('Unsubscribed with this email'),
                       'suppressed': gettext('Not sent by Resend (suppressed)')}
        note = ''
        if mail.stats_updated:
            note = gettext('Figures read from Resend on %(when)s.') % {'when': moment(mail.stats_updated)}
            if not stats:
                note += ' ' + gettext('There are no figures yet, or the list is too long: see them in Resend.')
        return {
            'status_line': line,
            'resend_status_label': resend_labels.get(mail.resend_status, mail.resend_status),
            'stats': bool(stats),
            'stats_rows': [(stat_labels[kind], stats[kind]) for kind in STATS_KINDS if kind in stats],
            'stats_note': note,
            'can_retry': mail.status == MassEmail.FAILED or
            (mail.status == MassEmail.SENDING and mail.updated < timezone.now() - STUCK_AFTER),
        }

    def _after_save(self, request, obj):
        if '_send_test' in request.POST:
            self.send_test(request, obj)
            return HttpResponseRedirect(reverse('admin:judge_massemail_change', args=[obj.pk]))
        if '_to_send' in request.POST:
            return HttpResponseRedirect(reverse('admin:judge_massemail_send', args=[obj.pk]))
        return None

    def response_add(self, request, obj, post_url_continue=None):
        return self._after_save(request, obj) or super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        return self._after_save(request, obj) or super().response_change(request, obj)

    def send_test(self, request, obj):
        address = request.user.email
        if not is_configured():
            messages.error(request, gettext('The Resend key is not configured yet, so nothing can be sent.'))
            return
        if not address:
            messages.error(request, gettext('Your account has no email address to send the test to.'))
            return
        subject, html, text = render_email(obj, username=request.user.username)
        payload = {'from': sender(), 'to': [address], 'subject': '[%s] %s' % (gettext('Test'), subject),
                   'html': html, 'text': text}
        if reply_to():
            payload['reply_to'] = reply_to()
        try:
            ResendClient().send_email(payload)
        except ResendError as error:
            messages.error(request, gettext('The test could not be sent: %(error)s') % {'error': error})
        else:
            messages.success(request, gettext('A test was sent to %(email)s. It may take a minute to arrive.') %
                             {'email': address})

    # --- extra pages -------------------------------------------------------------------------------------------

    def get_urls(self):
        def wrap(view, post_only=False):
            def guarded(request, *args, **kwargs):
                if not self.allowed(request):
                    raise PermissionDenied()
                return view(request, *args, **kwargs)
            return self.admin_site.admin_view(require_POST(guarded) if post_only else guarded)

        return [
            path('fragment/', wrap(self.fragment_view, True), name='judge_massemail_fragment'),
            path('preview/', wrap(self.preview_view, True), name='judge_massemail_preview'),
            path('count/', wrap(self.count_view, True), name='judge_massemail_count'),
            path('recipients/', wrap(self.recipients_view, True), name='judge_massemail_recipients'),
            path('unsubscribed/', wrap(self.unsubscribed_view), name='judge_massemail_unsubscribed'),
            path('<int:pk>/email/', wrap(self.email_view), name='judge_massemail_email'),
            path('<int:pk>/sent-to/', wrap(self.sent_to_view), name='judge_massemail_sent_to'),
            path('<int:pk>/send/', wrap(self.send_view), name='judge_massemail_send'),
            path('<int:pk>/retry/', wrap(self.retry_view, True), name='judge_massemail_retry'),
            path('<int:pk>/duplicate/', wrap(self.duplicate_view, True), name='judge_massemail_duplicate'),
            path('<int:pk>/cancel/', wrap(self.cancel_view, True), name='judge_massemail_cancel'),
            path('<int:pk>/stats/', wrap(self.stats_view, True), name='judge_massemail_stats'),
        ] + super().get_urls()

    def context(self, request, title, **values):
        return dict(self.admin_site.each_context(request), title=title, opts=self.model._meta, **values)

    def change_url(self, obj):
        return reverse('admin:judge_massemail_change', args=[obj.pk])

    @staticmethod
    def unsaved(request):
        """The message part of a form, saved or not, as an object that render_email understands."""
        language = request.POST.get('language')
        return MassEmail(subject=request.POST.get('subject', ''), preheader=request.POST.get('preheader', ''),
                         heading=request.POST.get('heading', ''), content=request.POST.get('content', ''),
                         language=language if language in dict(MassEmail.LANGUAGES) else 'es')

    def fragment_view(self, request):
        return HttpResponse(preview_fragment(request.POST.get('content', '')))

    def preview_view(self, request):
        return self.render_preview(request, self.unsaved(request))

    def email_view(self, request, pk):
        mail = get_object_or_404(MassEmail, pk=pk)
        return self.render_preview(request, mail)

    def render_preview(self, request, mail):
        subject, html, text = render_email(mail, username=request.user.username)
        response = TemplateResponse(request, 'admin/judge/massemail/preview.html', {
            'subject': subject, 'preheader': mail.preheader, 'html': html, 'text': text, 'sender': sender(),
            'username': request.user.username,
        })
        response['Content-Security-Policy'] = EMAIL_CSP
        response['X-Frame-Options'] = 'DENY'
        return response

    def count_view(self, request):
        form = RecipientForm(request.POST)
        if not form.is_valid():
            return JsonResponse({'errors': [error for errors in form.errors.values() for error in errors]})
        filters = filters_from_form(form.cleaned_data)
        rows = recipient_rows(filters)
        return JsonResponse({'count': len(rows), 'filtered': has_filters(filters) or bool(filters['exclude_users']),
                             'sample': [row[1] for row in rows[:12]]})

    def recipients_view(self, request):
        form = RecipientForm(request.POST)
        rows = recipient_rows(filters_from_form(form.cleaned_data)) if form.is_valid() else []
        return TemplateResponse(request, 'admin/judge/massemail/recipients.html', self.context(
            request, gettext('Who will receive it'), rows=rows, errors=form.errors if form.errors else None,
            show_email=request.user.is_superuser))

    def sent_to_view(self, request, pk):
        mail = get_object_or_404(MassEmail, pk=pk)
        recipients = list(mail.recipients.all())
        counts = {state: sum(1 for item in recipients if item.state == state)
                  for state, label in MassEmailRecipient.STATES}
        return TemplateResponse(request, 'admin/judge/massemail/sent_to.html', self.context(
            request, gettext('Who it was sent to'), mail=mail, recipients=recipients, counts=counts,
            show_email=request.user.is_superuser))

    # --- sending -----------------------------------------------------------------------------------------------

    def audience(self, mail):
        """(people, of them unsubscribed in Resend, error): the list as it is right now."""
        rows = recipient_rows(filters_of(mail))
        try:
            contacts = ResendClient().contacts()
        except ResendError as error:
            return rows, set(), str(error)
        gone = {row[2].lower() for row in rows if contacts.get(row[2].lower(), {}).get('unsubscribed')}
        return rows, gone, None

    def send_view(self, request, pk):
        mail = get_object_or_404(MassEmail, pk=pk)
        if mail.status != MassEmail.DRAFT:
            messages.warning(request, gettext('This email is no longer a draft.'))
            return HttpResponseRedirect(self.change_url(mail))
        configured = is_configured()
        rows, gone, error = self.audience(mail) if configured else (recipient_rows(filters_of(mail)), set(), None)
        receive = len(rows) - len(gone)
        form_errors = []
        typed = request.POST.get('confirm', '').strip()
        when = request.POST.get('when', 'now')
        scheduled_text = request.POST.get('scheduled_at', '')

        if request.method == 'POST' and configured and not error:
            scheduled_at = None
            if when == 'later':
                try:
                    scheduled_at = timezone.make_aware(datetime.strptime(scheduled_text, '%Y-%m-%dT%H:%M'))
                except ValueError:
                    form_errors.append(gettext('Choose the date and time to send it.'))
                else:
                    now = timezone.now()
                    if not now + SCHEDULE_MIN <= scheduled_at <= now + SCHEDULE_MAX:
                        form_errors.append(gettext('It can be scheduled from 5 minutes to 30 days from now.'))
            if typed != str(receive):
                form_errors.append(gettext('Type the number of people who will receive it: %(count)d.') %
                                   {'count': receive})
            if not receive:
                form_errors.append(gettext('Nobody would receive it.'))
            if not form_errors:
                return self.start_sending(request, mail, rows, gone, scheduled_at)

        return TemplateResponse(request, 'admin/judge/massemail/send.html', self.context(
            request, gettext('Send: %(subject)s') % {'subject': mail.subject}, mail=mail, configured=configured,
            error=error, total=len(rows), unsubscribed=len(gone), receive=receive,
            unsubscribed_line=ngettext('%(count)d more person matches, but unsubscribed earlier and is left out.',
                                       '%(count)d more people match, but unsubscribed earlier and are left out.',
                                       len(gone)) % {'count': len(gone)},
            sample=[row[1] for row in rows if row[2].lower() not in gone][:40],
            sender=sender(), reply_to=reply_to(), form_errors=form_errors, typed=typed, when=when,
            scheduled_text=scheduled_text, timezone_name=timezone.get_current_timezone_name(),
            minimum=timezone.localtime(timezone.now() + SCHEDULE_MIN + timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M'),
            filtered=has_filters(filters_of(mail))))

    def start_sending(self, request, mail, rows, gone, scheduled_at):
        task_id = str(uuid.uuid4())
        with transaction.atomic():
            started = MassEmail.objects.filter(pk=mail.pk, status=MassEmail.DRAFT).update(
                status=MassEmail.SENDING, sent_by=request.profile, sent_at=timezone.now(), scheduled_at=scheduled_at,
                from_email=sender(), error='', task_id=task_id, updated=timezone.now())
            if not started:
                messages.warning(request, gettext('Somebody else is already sending this email.'))
                return HttpResponseRedirect(self.change_url(mail))
            MassEmailRecipient.objects.bulk_create([
                MassEmailRecipient(mass_email=mail, profile_id=profile_id, username=username, email=email,
                                   state=MassEmailRecipient.UNSUBSCRIBED if email.lower() in gone
                                   else MassEmailRecipient.PENDING)
                for profile_id, username, email in rows])
        count = len(rows) - len(gone)
        self.log_change(request, mail, ngettext('Sent to %(count)d person.', 'Sent to %(count)d people.', count) %
                        {'count': count} if not scheduled_at else
                        gettext('Scheduled for %(when)s, to %(count)d people.') %
                        {'when': timezone.localtime(scheduled_at).strftime('%Y-%m-%d %H:%M'), 'count': count})
        return self.enqueue(request, mail, task_id)

    def enqueue(self, request, mail, task_id):
        from judge.tasks import send_mass_email
        try:
            send_mass_email.apply_async(args=[mail.pk], task_id=task_id)
        except Exception as error:  # The queue (Redis) is down: say so instead of leaving it "sending" forever.
            MassEmail.objects.filter(pk=mail.pk).update(
                status=MassEmail.FAILED, error='%s: %s' % (type(error).__name__, error))
            messages.error(request, gettext('The email could not be queued. Try again in a moment.'))
            return HttpResponseRedirect(self.change_url(mail))
        return HttpResponseRedirect(task_status_url_by_id(
            task_id, message=gettext('Handing the email to Resend...'), redirect=self.change_url(mail)))

    def retry_view(self, request, pk):
        mail = get_object_or_404(MassEmail, pk=pk)
        task_id = str(uuid.uuid4())
        if not MassEmail.objects.filter(Q(status=MassEmail.FAILED) | stuck(), pk=pk).update(
                status=MassEmail.SENDING, task_id=task_id, error='', updated=timezone.now()):
            messages.warning(request, gettext('Only an email that failed, or that has been sending for more than '
                                              '15 minutes, can be retried.'))
            return HttpResponseRedirect(self.change_url(mail))
        self.log_change(request, mail, gettext('Retried after a failure.'))
        return self.enqueue(request, mail, task_id)

    def duplicate_view(self, request, pk):
        mail = get_object_or_404(MassEmail, pk=pk)
        copy = MassEmail(created_by=request.profile, status=MassEmail.DRAFT,
                         **{field: getattr(mail, field) for field in ('subject', 'preheader', 'heading', 'content',
                                                                      'language', 'include_virtual',
                                                                      'active_within_days', 'joined_after',
                                                                      'joined_before', 'min_problems')})
        copy.save()
        for field in MANY_FIELDS:
            getattr(copy, field).set(getattr(mail, field).all())
        self.log_addition(request, copy, gettext('Copied from email %(id)d.') % {'id': mail.pk})
        messages.success(request, gettext('This is a new draft copied from the previous email. Nothing has been '
                                          'sent yet.'))
        return HttpResponseRedirect(self.change_url(copy))

    def cancel_view(self, request, pk):
        mail = get_object_or_404(MassEmail, pk=pk)
        if mail.status != MassEmail.SCHEDULED or not mail.resend_broadcast_id:
            messages.warning(request, gettext('Only a scheduled email can be cancelled.'))
            return HttpResponseRedirect(self.change_url(mail))
        try:
            ResendClient().cancel_broadcast(mail.resend_broadcast_id)
        except ResendError as error:
            messages.error(request, gettext('Resend did not cancel it: %(error)s') % {'error': error})
            return HttpResponseRedirect(self.change_url(mail))
        MassEmail.objects.filter(pk=pk).update(status=MassEmail.CANCELLED, resend_status='canceled',
                                               updated=timezone.now())
        self.log_change(request, mail, gettext('Cancelled before it was sent.'))
        messages.success(request, gettext('Cancelled: nobody will receive it. Duplicate it to change and send it '
                                          'again.'))
        return HttpResponseRedirect(self.change_url(mail))

    def stats_view(self, request, pk):
        mail = get_object_or_404(MassEmail, pk=pk)
        if not mail.resend_broadcast_id:
            raise Http404()
        try:
            client = ResendClient()
            status = client.broadcast(mail.resend_broadcast_id).get('status') or ''
            stats = {}
            if (mail.recipient_count or 0) <= STATS_LIMIT and status not in ('draft', 'scheduled', 'canceled'):
                stats = {kind: client.count_recipients(mail.resend_broadcast_id, kind) for kind in STATS_KINDS}
        except ResendError as error:
            messages.error(request, gettext('Could not read it from Resend: %(error)s') % {'error': error})
            return HttpResponseRedirect(self.change_url(mail))
        MassEmail.objects.filter(pk=pk).update(resend_status=status[:20], stats=json.dumps(stats),
                                               stats_updated=timezone.now())
        return HttpResponseRedirect(self.change_url(mail))

    def unsubscribed_view(self, request):
        if not is_configured():
            return TemplateResponse(request, 'admin/judge/massemail/unsubscribed.html', self.context(
                request, gettext('Unsubscribed people'), configured=False))
        client = ResendClient()
        if request.method == 'POST':
            contact_id = request.POST.get('contact', '')
            try:
                client.resubscribe(contact_id)
            except ResendError as error:
                messages.error(request, gettext('Resend did not change it: %(error)s') % {'error': error})
            else:
                messages.success(request, gettext('Subscribed again: %(email)s will receive the next emails.') %
                                 {'email': request.POST.get('email', '')})
            return HttpResponseRedirect(reverse('admin:judge_massemail_unsubscribed'))
        try:
            contacts = [contact for contact in client.contacts().values() if contact.get('unsubscribed')]
        except ResendError as error:
            return TemplateResponse(request, 'admin/judge/massemail/unsubscribed.html', self.context(
                request, gettext('Unsubscribed people'), configured=True, error=str(error)))
        users = {}
        for username, email in User.objects.filter(email__contains='@').values_list('username', 'email'):
            users.setdefault(email.strip().lower(), []).append(username)
        rows = sorted(({'id': contact['id'], 'email': contact['email'],
                        'usernames': users.get(contact['email'].strip().lower(), [])} for contact in contacts),
                      key=lambda row: row['email'].lower())
        return TemplateResponse(request, 'admin/judge/massemail/unsubscribed.html', self.context(
            request, gettext('Unsubscribed people'), configured=True, rows=rows,
            confirm_question=gettext('Subscribe this person again? Do it only if they asked for it.')))

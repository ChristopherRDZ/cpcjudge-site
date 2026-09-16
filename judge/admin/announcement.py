from datetime import timedelta

from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext, gettext_lazy as _

from judge.models import Announcement, Contest


class AnnouncementForm(forms.ModelForm):
    # A duration is what an announcer actually has in mind —"half an hour", "as
    # long as the contest"— so the form takes that and works out the moment.
    minutes = forms.IntegerField(
        required=False, min_value=1, max_value=60 * 24 * 7,
        label=_('show for (minutes)'),
        help_text=_('Fill this in to stop showing it that many minutes from now; it overrides the field below. '
                    'Leave both empty on a contest announcement and it stops when the contest does.'),
    )

    class Meta:
        model = Announcement
        fields = ('contest', 'body', 'expires', 'is_visible')

    def clean(self):
        cleaned_data = super().clean()

        # The model allows an empty body because an announcement that carries a
        # clarification takes its text from there. Written by hand, it needs one.
        if not cleaned_data.get('body', '').strip() and self.instance.clarification_id is None:
            self.add_error('body', _('An announcement written here needs some text.'))

        minutes = cleaned_data.get('minutes')
        if minutes:
            cleaned_data['expires'] = timezone.now() + timedelta(minutes=minutes)
        elif not cleaned_data.get('expires'):
            contest = cleaned_data.get('contest')
            if contest is not None:
                # The usual case for a clarification: it matters while the
                # contest runs, and a two-hour contest should not leave a box
                # popping up all day.
                cleaned_data['expires'] = contest.end_time
            else:
                raise ValidationError(_('A site-wide announcement needs either a duration or a time to stop '
                                        'showing, so it does not stay up forever.'))
        return cleaned_data


class AnnouncementAdmin(admin.ModelAdmin):
    form = AnnouncementForm
    fields = ('contest', 'body', 'minutes', 'expires', 'is_visible')
    list_display = ('created', 'target', 'summary', 'expires', 'author', 'is_visible')
    list_filter = ('is_visible', 'contest')
    search_fields = ('body',)
    date_hierarchy = 'created'

    def target(self, obj):
        return obj.contest.name if obj.contest_id else format_html('<b>{}</b>', gettext('Whole site'))
    target.short_description = _('target')

    def summary(self, obj):
        return obj.body if len(obj.body) <= 80 else obj.body[:77] + '...'
    summary.short_description = _('announcement')

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('contest', 'author__user')
        if request.user.has_perm('judge.edit_all_contest'):
            return queryset
        # Otherwise you see the site-wide ones and the ones for your own contests.
        return queryset.filter(
            Q(contest__isnull=True) | Q(contest__authors=request.profile) | Q(contest__curators=request.profile),
        ).distinct()

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'contest' in form.base_fields:
            contests = Contest.objects.all()
            if not request.user.has_perm('judge.edit_all_contest'):
                contests = contests.filter(
                    Q(authors=request.profile) | Q(curators=request.profile),
                ).distinct()
            form.base_fields['contest'].queryset = contests
            # Leaving the contest empty means "everyone on the site", so that is
            # the case that needs the extra permission. Making the field required
            # is what enforces it: an empty contest then fails form validation,
            # which runs on the server whatever the browser was told.
            if not request.user.has_perm('judge.announce_site'):
                form.base_fields['contest'].required = True
                form.base_fields['contest'].help_text = _(
                    'You may only announce to a contest you run. Announcing to the whole site needs a '
                    'separate permission.',
                )
        return form

    def save_model(self, request, obj, form, change):
        if not change or obj.author_id is None:
            obj.author = request.profile
        super().save_model(request, obj, form, change)

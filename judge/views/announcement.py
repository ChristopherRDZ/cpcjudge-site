from datetime import timedelta

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import DetailView

from judge.models import Announcement, ContestClarification, ContestParticipation
from judge.models.clarification import MAX_PENDING_PER_USER, MAX_QUESTION, MIN_SECONDS_BETWEEN
from judge.utils.views import TitleMixin
from judge.views.contests import ContestMixin, _find_contest

__all__ = ['active_announcements', 'ContestAnnouncements', 'ask_clarification', 'answer_clarification']

MAX_ACTIVE = 20


def active_announcements(request):
    """Everything this caller should be shown in the pop-up box, newest first.

    This is deliberately the only place the text ever comes from. Nothing that
    reaches the event daemon's unauthenticated publishing port can put words on
    anybody's screen, because no text travels that way.
    """
    now = timezone.now()
    items = []

    contest_id = None
    profile = getattr(request, 'profile', None)
    if request.user.is_authenticated and profile is not None and profile.current_contest is not None:
        contest_id = profile.current_contest.contest_id

    # How long each announcement keeps popping up is decided when it is written:
    # thirty minutes for a notice, or the end of the contest for a clarification.
    queryset = Announcement.objects.filter(is_visible=True) \
                                   .filter(Q(expires__isnull=True) | Q(expires__gt=now))
    if contest_id is None:
        queryset = queryset.filter(contest__isnull=True)
    else:
        queryset = queryset.filter(Q(contest__isnull=True) | Q(contest_id=contest_id))

    for announcement in queryset.select_related('contest', 'clarification').order_by('-created')[:MAX_ACTIVE]:
        if announcement.clarification_id:
            # Laid out here rather than stored, so each reader gets the labels in
            # their own language.
            body = '%s: %s\n\n%s: %s' % (_('Question'), announcement.clarification.question,
                                         _('Answer'), announcement.clarification.answer)
        else:
            body = announcement.body
        items.append({
            'key': 'announcement:%d' % announcement.id,
            'body': body,
            'contest': announcement.contest.name if announcement.contest_id else None,
        })

    # Your own answered questions come back to you the same way, so you do not
    # have to sit refreshing the tab waiting for the jury.
    #
    # Only the private ones. A public answer already travels as an announcement
    # of its own, and returning it here as well showed it to the person who
    # asked twice over.
    if contest_id is not None:
        answered = ContestClarification.objects.filter(
            ContestClarification.ownership_filter(profile),
            contest_id=contest_id, answered__isnull=False, is_public=False,
            contest__end_time__gt=now,
        ).select_related('contest').order_by('-answered')[:MAX_ACTIVE]
        for clarification in answered:
            items.append({
                'key': 'clarification:%d' % clarification.id,
                'body': '%s\n\n%s' % (clarification.question, clarification.answer),
                'contest': clarification.contest.name,
            })

    # A badge in the navigation bar, so the jury notices a question arriving
    # without having to keep the tab open.
    pending_questions = 0
    participation = getattr(request, 'participation', None)
    if participation is not None and participation.contest.is_editable_by(request.user):
        pending_questions = ContestClarification.objects.filter(contest_id=contest_id, answer='').count()

    return JsonResponse({'announcements': items, 'pending_questions': pending_questions})


class ContestProblemChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.problem.name


class ClarificationForm(forms.ModelForm):
    problem = ContestProblemChoiceField(queryset=None, required=False, label=_('problem'),
                                        empty_label=_('About the contest in general'))

    class Meta:
        model = ContestClarification
        fields = ('problem', 'question')
        widgets = {
            'question': forms.Textarea(attrs={'rows': 4, 'maxlength': MAX_QUESTION,
                                              'placeholder': _('Ask the jury a question about this contest.')}),
        }

    def __init__(self, *args, **kwargs):
        contest = kwargs.pop('contest')
        super().__init__(*args, **kwargs)
        self.fields['problem'].queryset = contest.contest_problems.select_related('problem').order_by('order')

    def clean_question(self):
        # A TextField gives a form field with no length limit of its own, so the
        # cap has to be stated here as well as in the textarea attribute, which a
        # browser is free to ignore.
        question = self.cleaned_data['question'].strip()
        if not question:
            raise forms.ValidationError(_('The question is empty.'))
        if len(question) > MAX_QUESTION:
            raise forms.ValidationError(_('Questions are limited to %(limit)d characters.'),
                                        params={'limit': MAX_QUESTION})
        return question


def _visible_clarifications(contest, profile, can_edit):
    queryset = contest.clarifications.select_related('user__user', 'answered_by__user', 'problem__problem',
                                                     'team_participation')
    # An answer made public is shown in the announcements above, laid out as
    # question and answer. Leaving it here too put the same text on the page
    # twice, which is what the list is kept clear of.
    queryset = queryset.exclude(is_public=True, answered__isnull=False)
    if can_edit:
        return queryset
    if profile is None:
        return queryset.none()
    # What is left is your own: waiting, or answered just for you.
    return queryset.filter(ContestClarification.ownership_filter(profile)).distinct()


class ContestAnnouncements(ContestMixin, TitleMixin, DetailView):
    template_name = 'contest/announcements.html'
    tab = 'announcements'

    def get_title(self):
        return _('Clarifications for %s') % self.object.name

    def get_content_title(self):
        return self.object.name

    def can_ask(self):
        if not self.request.user.is_authenticated or self.object.ended:
            return False
        participation = self.request.profile.current_contest
        return participation is not None and participation.contest_id == self.object.id

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.profile if self.request.user.is_authenticated else None
        context['announcements'] = (self.object.announcements.filter(is_visible=True)
                                        .select_related('author__user', 'clarification')
                                        .order_by('-created'))
        context['clarifications'] = _visible_clarifications(self.object, profile, self.can_edit).order_by('-asked')
        context['can_ask'] = self.can_ask()
        context['ask_form'] = ClarificationForm(contest=self.object) if context['can_ask'] else None
        context['max_question'] = MAX_QUESTION
        context['clarification_errors'] = self.request.session.pop('clarification_errors', [])
        context['tab'] = self.tab
        return context


def _contest_or_404(request, contest):
    contest, exists = _find_contest(request, contest)
    if not exists:
        raise Http404()
    return contest


@login_required
@require_POST
@transaction.atomic
def ask_clarification(request, contest):
    contest = _contest_or_404(request, contest)
    profile = request.profile

    participation = profile.current_contest
    if contest.ended or participation is None or participation.contest_id != contest.id:
        raise PermissionDenied()

    # Teammates share the jury conversation and its limits. Serialize on the
    # participation so simultaneous questions do not bypass the shared cap.
    team_participation = None
    if participation.team_id:
        participation = ContestParticipation.objects.select_for_update().get(pk=participation.pk)
        if participation.ended or participation.is_disqualified or not participation.contains_profile(profile.pk):
            raise PermissionDenied()
        team_participation = participation
        questions = ContestClarification.objects.filter(team_participation=participation)
    else:
        questions = ContestClarification.objects.filter(user=profile, contest=contest, team_participation__isnull=True)
    now = timezone.now()
    too_soon = questions.filter(asked__gte=now - timedelta(seconds=MIN_SECONDS_BETWEEN)).exists()
    pending = questions.filter(answer='').count()

    form = ClarificationForm(request.POST, contest=contest)
    errors = []
    if too_soon:
        errors.append(_('Wait a few seconds before asking again.'))
    elif pending >= MAX_PENDING_PER_USER:
        errors.append(_('You already have %(limit)d questions waiting for an answer.')
                      % {'limit': MAX_PENDING_PER_USER})
    elif form.is_valid():
        clarification = form.save(commit=False)
        clarification.contest = contest
        clarification.user = profile
        clarification.team_participation = team_participation
        clarification.save()
    else:
        errors = [str(error) for field_errors in form.errors.values() for error in field_errors]

    if errors:
        # Carried in the session because the answer is a redirect, so the tab can
        # say what went wrong without the question being re-posted on refresh.
        request.session['clarification_errors'] = [str(error) for error in errors]
    return HttpResponseRedirect(reverse('contest_announcements', args=[contest.key]))


@login_required
@require_POST
def answer_clarification(request, contest, pk):
    contest = _contest_or_404(request, contest)
    if not contest.is_editable_by(request.user):
        raise PermissionDenied()

    clarification = get_object_or_404(ContestClarification, pk=pk, contest=contest)
    answer = request.POST.get('answer', '').strip()
    if not answer:
        raise Http404()

    clarification.answer = answer[:MAX_QUESTION]
    clarification.answered = timezone.now()
    clarification.answered_by = request.profile
    clarification.is_public = request.POST.get('is_public') == 'on'
    clarification.save()

    # A public answer is exactly what the announcement box is for, so it reuses
    # it instead of growing a second delivery path.
    if clarification.is_public:
        Announcement.objects.create(
            contest=contest,
            clarification=clarification,
            body='',
            author=request.profile,
            expires=contest.end_time,
        )

    return HttpResponseRedirect(reverse('contest_announcements', args=[contest.key]))

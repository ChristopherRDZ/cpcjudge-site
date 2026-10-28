from operator import itemgetter

from celery.result import AsyncResult
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseRedirect
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _, ngettext
from django.views.generic import DetailView
from django.views.generic.detail import BaseDetailView

from judge.models import Contest, Language, Submission
from judge.tasks import apply_submission_filter, rejudge_problem_filter, rescore_problem
from judge.utils.celery import redirect_to_task_status
from judge.utils.views import TitleMixin
from judge.views.problem import ProblemMixin


class ManageProblemSubmissionMixin(ProblemMixin):
    def get_object(self, queryset=None):
        problem = super().get_object(queryset)
        user = self.request.user
        if not problem.is_subs_manageable_by(user):
            raise Http404()
        return problem

    def visible_submissions(self):
        """The submissions of the problem, without those a frozen scoreboard hides from this user (never their own)."""
        queryset = self.object.submission_set.all()
        frozen = Contest.frozen_submission_filter(self.request.user)
        if frozen is not None:
            queryset = queryset.exclude(frozen & ~Submission.ownership_filter(self.request.profile))
        return queryset

    def frozen_out(self):
        """Whether a frozen scoreboard that this user does not organize covers this problem.

        Editing the problem is not editing the contest. Until the scoreboard is revealed, the counts and the result
        of rejudging or rescoring would tell what it hides, so that is left to whoever organizes the contest (it has
        its own rejudge button in the admin). Decided by the contest, not by its submissions, so the answer does not
        say whether anybody sent something in the frozen window.
        """
        windows = Contest.frozen_windows_for(self.request.user)
        if not windows:
            return False
        contest_ids = [contest_id for contest_id, _cutoff in windows]
        # The contests that use the problem now, and those its submissions still belong to: a slot whose problem was
        # swapped for another keeps them there (only deleting the slot takes them out, signals.contest_problem_delete).
        # Any date, so this does not say whether something arrived in the frozen window either.
        return (Contest.objects.filter(id__in=contest_ids, problems=self.object).exists() or
                self.object.submission_set.filter(contest_object_id__in=contest_ids).exists())


class ManageProblemSubmissionActionMixin(ManageProblemSubmissionMixin):
    def perform_action(self):
        raise NotImplementedError()

    def get(self, request, *args, **kwargs):
        raise Http404()

    def post(self, request, *args, **kwargs):
        try:
            self.object = self.get_object()
        except Http404:
            return self.no_such_problem()
        if self.frozen_out():
            return self.frozen_out_response()
        return self.perform_action()

    def frozen_out_response(self):
        # The same text as the notice on the page (problem/manage_submission.html).
        messages.error(self.request, _('This problem is in a contest with a frozen scoreboard that you do not '
                                       'organize. Until the scoreboard is revealed, only the organizers of that '
                                       'contest can rejudge or rescore these submissions.'))
        return HttpResponseRedirect(reverse('problem_manage_submissions', args=[self.object.code]))


class ManageProblemSubmissionView(TitleMixin, ManageProblemSubmissionMixin, DetailView):
    template_name = 'problem/manage_submission.html'

    def get_title(self):
        return _('Managing submissions for %s') % (self.object.name,)

    def get_content_title(self):
        return mark_safe(escape(_('Managing submissions for %s')) % (
            format_html('<a href="{1}">{0}</a>', self.object.name,
                        reverse('problem_detail', args=[self.object.code]))))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Frozen out, the page shows a notice instead of the panes that use the count.
        context['frozen_out'] = frozen_out = self.frozen_out()
        context['submission_count'] = 0 if frozen_out else self.visible_submissions().count()
        context['languages'] = [(lang_id, short_name or key) for lang_id, key, short_name in
                                Language.objects.values_list('id', 'key', 'short_name')]
        context['results'] = sorted(map(itemgetter(0), Submission.RESULT))
        return context


class BaseRejudgeSubmissionsView(PermissionRequiredMixin, ManageProblemSubmissionActionMixin, BaseDetailView):
    permission_required = 'judge.rejudge_submission_lot'

    def perform_action(self):
        if self.request.POST.get('use_range', 'off') == 'on':
            try:
                start = int(self.request.POST.get('start'))
                end = int(self.request.POST.get('end'))
            except (KeyError, ValueError):
                return HttpResponseBadRequest()
            id_range = (start, end)
        else:
            id_range = None

        try:
            languages = list(map(int, self.request.POST.getlist('language')))
        except ValueError:
            return HttpResponseBadRequest()

        return self.generate_response(id_range, languages, self.request.POST.getlist('result'))

    def generate_response(self, id_range, languages, results):
        raise NotImplementedError()


class RejudgeSubmissionsView(BaseRejudgeSubmissionsView):
    def generate_response(self, id_range, languages, results):
        status = rejudge_problem_filter.delay(self.object.id, id_range, languages, results,
                                              user_id=self.request.user.id)
        return redirect_to_task_status(
            status, message=_('Rejudging selected submissions for %s...') % (self.object.name,),
            redirect=reverse('problem_submissions_rejudge_success', args=[self.object.code, status.id]),
        )


class PreviewRejudgeSubmissionsView(BaseRejudgeSubmissionsView):
    def generate_response(self, id_range, languages, results):
        queryset = apply_submission_filter(self.visible_submissions(), id_range, languages, results)
        return HttpResponse(str(queryset.count()))

    def frozen_out_response(self):
        # Answered to the page's script, which then asks without a number.
        return HttpResponseForbidden()


class RescoreAllSubmissionsView(ManageProblemSubmissionActionMixin, BaseDetailView):
    def perform_action(self):
        status = rescore_problem.delay(self.object.id)
        return redirect_to_task_status(
            status, message=_('Rescoring all submissions for %s...') % (self.object.name,),
            redirect=reverse('problem_submissions_rescore_success', args=[self.object.code, status.id]),
        )


def rejudge_success(request, problem, task_id):
    count = AsyncResult(task_id).result
    if not isinstance(count, int):
        raise Http404()
    messages.success(request, ngettext('Successfully scheduled %d submission for rejudging.',
                                       'Successfully scheduled %d submissions for rejudging.', count) % (count,))
    return HttpResponseRedirect(reverse('problem_manage_submissions', args=[problem]))


def rescore_success(request, problem, task_id):
    count = AsyncResult(task_id).result
    if not isinstance(count, int):
        raise Http404()
    messages.success(request, ngettext('%d submission was successfully rescored.',
                                       '%d submissions were successfully rescored.', count) % (count,))
    return HttpResponseRedirect(reverse('problem_manage_submissions', args=[problem]))

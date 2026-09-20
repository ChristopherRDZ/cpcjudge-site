import logging
import os
import re
from datetime import timedelta
from operator import itemgetter
from random import randrange
from statistics import mean, median

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied
from django.db import transaction
from django.db.models import BooleanField, Case, CharField, Count, F, FilteredRelation, Prefetch, Q, When
from django.db.models.functions import Coalesce
from django.db.utils import ProgrammingError
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.functional import cached_property
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _, gettext_lazy
from django.views.generic import DetailView, ListView, View
from django.views.generic.detail import SingleObjectMixin
from reversion import revisions

from judge.comments import CommentedDetailView
from judge.forms import ProblemCloneForm, ProblemPointsVoteForm, ProblemSubmitForm
from judge.models import ContestSubmission, Judge, Language, Problem, ProblemGroup, ProblemPointsVote, \
    ProblemTranslation, ProblemType, RuntimeVersion, Solution, Submission, SubmissionSource
from judge.utils.diggpaginator import DiggPaginator
from judge.utils.opengraph import generate_opengraph
from judge.utils.pdfoid import PDF_RENDERING_ENABLED, render_pdf
from judge.utils.problems import contest_attempted_ids, contest_completed_ids, hot_problems, user_attempted_ids, \
    user_completed_ids
from judge.utils.strings import safe_float_or_none, safe_int_or_none
from judge.utils.tickets import own_ticket_filter
from judge.utils.views import QueryStringSortMixin, SingleObjectFormView, TitleMixin, add_file_response, generic_message

recjk = re.compile(r'[\u2E80-\u2E99\u2E9B-\u2EF3\u2F00-\u2FD5\u3005\u3007\u3021-\u3029\u3038-\u303A\u303B\u3400-\u4DB5'
                   r'\u4E00-\u9FC3\uF900-\uFA2D\uFA30-\uFA6A\uFA70-\uFAD9\U00020000-\U0002A6D6\U0002F800-\U0002FA1D]')


def get_contest_problem(problem, profile):
    try:
        return problem.contests.get(contest_id=profile.current_contest.contest_id)
    except ObjectDoesNotExist:
        return None


def get_contest_submission_count(problem, profile, virtual):
    return profile.current_contest.submissions.exclude(submission__status__in=['IE']) \
                  .filter(problem__problem=problem, participation__virtual=virtual).count()


class ProblemMixin(object):
    model = Problem
    slug_url_kwarg = 'problem'
    slug_field = 'code'

    def get_object(self, queryset=None):
        problem = super(ProblemMixin, self).get_object(queryset)
        if not problem.is_accessible_by(self.request.user):
            raise Http404()
        return problem

    def no_such_problem(self):
        code = self.kwargs.get(self.slug_url_kwarg, None)
        return generic_message(self.request, _('No such problem'),
                               _('Could not find a problem with the code "%s".') % code, status=404)

    def get(self, request, *args, **kwargs):
        try:
            return super(ProblemMixin, self).get(request, *args, **kwargs)
        except Http404:
            return self.no_such_problem()


class SolvedProblemMixin(object):
    def get_completed_problems(self):
        if self.in_contest:
            return contest_completed_ids(self.profile.current_contest)
        else:
            return user_completed_ids(self.profile) if self.profile is not None else ()

    def get_attempted_problems(self):
        if self.in_contest:
            return contest_attempted_ids(self.profile.current_contest)
        else:
            return user_attempted_ids(self.profile) if self.profile is not None else ()

    @cached_property
    def in_contest(self):
        return self.profile is not None and self.profile.current_contest is not None

    @cached_property
    def contest(self):
        return self.request.profile.current_contest.contest

    @cached_property
    def profile(self):
        if not self.request.user.is_authenticated:
            return None
        return self.request.profile


class ProblemSolution(SolvedProblemMixin, ProblemMixin, TitleMixin, CommentedDetailView):
    context_object_name = 'problem'
    template_name = 'problem/editorial.html'

    def get_title(self):
        return _('Editorial for {0}').format(self.object.name)

    def get_content_title(self):
        return mark_safe(escape(_('Editorial for {0}')).format(
            format_html('<a href="{1}">{0}</a>', self.object.name, reverse('problem_detail', args=[self.object.code])),
        ))

    def get_context_data(self, **kwargs):
        context = super(ProblemSolution, self).get_context_data(**kwargs)

        solution = get_object_or_404(Solution, problem=self.object)

        if not solution.is_accessible_by(self.request.user) or self.request.in_contest:
            raise Http404()
        context['solution'] = solution
        context['has_solved_problem'] = self.object.id in self.get_completed_problems()
        context['enable_comments'] = settings.DMOJ_ENABLE_COMMENTS
        return context

    def get_comment_page(self):
        return 's:' + self.object.code

    def no_such_problem(self):
        code = self.kwargs.get(self.slug_url_kwarg, None)
        return generic_message(self.request, _('No such editorial'),
                               _('Could not find an editorial with the code "%s".') % code, status=404)


class ProblemDetail(ProblemMixin, SolvedProblemMixin, CommentedDetailView):
    context_object_name = 'problem'
    template_name = 'problem/problem.html'

    def get_comment_page(self):
        return 'p:%s' % self.object.code

    def get_context_data(self, **kwargs):
        context = super(ProblemDetail, self).get_context_data(**kwargs)
        user = self.request.user
        authed = user.is_authenticated
        context['has_submissions'] = authed and Submission.objects.filter(user=user.profile,
                                                                          problem=self.object).exists()
        contest_problem = (None if not authed or user.profile.current_contest is None else
                           get_contest_problem(self.object, user.profile))
        context['contest_problem'] = contest_problem
        if contest_problem:
            clarifications = self.object.clarifications
            context['has_clarifications'] = clarifications.count() > 0
            context['clarifications'] = clarifications.order_by('-date')
            context['submission_limit'] = contest_problem.max_submissions
            if contest_problem.max_submissions:
                context['submissions_left'] = max(contest_problem.max_submissions -
                                                  get_contest_submission_count(self.object, user.profile,
                                                                               user.profile.current_contest.virtual), 0)

        context['available_judges'] = Judge.objects.filter(online=True, problems=self.object)
        context['show_languages'] = self.object.allowed_languages.count() != Language.objects.count()
        context['has_pdf_render'] = PDF_RENDERING_ENABLED
        context['completed_problem_ids'] = self.get_completed_problems()
        context['attempted_problems'] = self.get_attempted_problems()

        can_edit = self.object.is_editable_by(user)
        context['can_edit_problem'] = can_edit
        if user.is_authenticated:
            tickets = self.object.tickets
            if not can_edit:
                tickets = tickets.filter(own_ticket_filter(user.profile.id))
            context['has_tickets'] = tickets.exists()
            context['num_open_tickets'] = tickets.filter(is_open=True).values('id').distinct().count()

        try:
            context['editorial'] = Solution.objects.get(problem=self.object)
        except ObjectDoesNotExist:
            pass
        try:
            translation = self.object.translations.get(language=self.request.LANGUAGE_CODE)
        except ProblemTranslation.DoesNotExist:
            context['title'] = self.object.name
            context['language'] = settings.LANGUAGE_CODE
            context['description'] = self.object.description
            context['translated'] = False
        else:
            context['title'] = translation.name
            context['language'] = self.request.LANGUAGE_CODE
            context['description'] = translation.description
            context['translated'] = True

        if not self.object.og_image or not self.object.summary:
            metadata = generate_opengraph('generated-meta-problem:%s:%d' % (context['language'], self.object.id),
                                          context['description'], 'problem')
        context['meta_description'] = self.object.summary or metadata[0]
        context['og_image'] = self.object.og_image or metadata[1]
        context['enable_comments'] = settings.DMOJ_ENABLE_COMMENTS

        context['vote_perm'] = self.object.vote_permission_for_user(user)
        if context['vote_perm'].can_vote():
            try:
                context['vote'] = ProblemPointsVote.objects.get(voter=user.profile, problem=self.object)
            except ObjectDoesNotExist:
                context['vote'] = None
        else:
            context['vote'] = None

        return context


class ProblemVote(ProblemMixin, DetailView):
    context_object_name = 'problem'
    template_name = 'problem/vote-ajax.html'

    def get_context_data(self, **kwargs):
        if not self.object.vote_permission_for_user(self.request.user).can_vote():
            raise Http404()

        context = super().get_context_data(**kwargs)

        try:
            context['vote'] = ProblemPointsVote.objects.get(voter=self.request.profile, problem=self.object)
        except ObjectDoesNotExist:
            context['vote'] = None

        context['max_possible_vote'] = settings.DMOJ_PROBLEM_MAX_USER_POINTS_VOTE
        context['min_possible_vote'] = settings.DMOJ_PROBLEM_MIN_USER_POINTS_VOTE
        return context

    def post(self, request, *args, **kwargs):
        problem = self.get_object()
        if not problem.vote_permission_for_user(request.user).can_vote():
            return JsonResponse({'message': _('Not allowed to vote on this problem.')}, status=403)

        form = ProblemPointsVoteForm(request.POST)
        if not form.is_valid():
            return JsonResponse(form.errors, status=400)

        with transaction.atomic():
            # Delete any pre-existing votes.
            ProblemPointsVote.objects.filter(voter=request.profile, problem=problem).delete()
            vote = form.save(commit=False)
            vote.voter = request.profile
            vote.problem = problem
            vote.save()

        return JsonResponse({'points': vote.points})


class DeleteProblemVote(ProblemMixin, SingleObjectMixin, View):
    http_method_names = ['options', 'post']  # This disables GET requests, even though ProblemMixin.get exists.

    def post(self, request, *args, **kwargs):
        problem = self.get_object()
        if not problem.vote_permission_for_user(request.user).can_vote():
            return JsonResponse({'message': _('Not allowed to delete votes on this problem.')}, status=403)

        ProblemPointsVote.objects.filter(voter=request.profile, problem=problem).delete()
        return JsonResponse({'message': _('success')})


class ProblemVoteStats(ProblemMixin, DetailView):
    context_object_name = 'problem'
    template_name = 'problem/vote-stats-ajax.html'

    def get_context_data(self, **kwargs):
        if not self.object.vote_permission_for_user(self.request.user).can_view():
            raise Http404()

        context = super().get_context_data(**kwargs)

        votes = list(self.object.problem_points_votes.order_by('points').values_list('points', flat=True))
        context['votes'] = votes

        if votes:
            context['mean'] = mean(votes)
            context['median'] = median(votes)

        context['max_possible_vote'] = settings.DMOJ_PROBLEM_MAX_USER_POINTS_VOTE
        context['min_possible_vote'] = settings.DMOJ_PROBLEM_MIN_USER_POINTS_VOTE
        return context


class LatexError(Exception):
    pass


class ProblemPdfView(ProblemMixin, SingleObjectMixin, View):
    logger = logging.getLogger('judge.problem.pdf')
    languages = set(map(itemgetter(0), settings.LANGUAGES))

    def get(self, request, *args, **kwargs):
        if not PDF_RENDERING_ENABLED:
            raise Http404()

        language = kwargs.get('language', self.request.LANGUAGE_CODE)
        if language not in self.languages:
            raise Http404()

        problem = self.get_object()
        pdf_basename = '%s.%s.pdf' % (problem.code, language)

        def render_problem_pdf():
            self.logger.info('Rendering PDF in %s: %s', language, problem.code)

            with translation.override(language):
                try:
                    trans = problem.translations.get(language=language)
                except ProblemTranslation.DoesNotExist:
                    trans = None

                problem_name = trans.name if trans else problem.name
                return render_pdf(
                    html=get_template('problem/raw.html').render({
                        'problem': problem,
                        'problem_name': problem_name,
                        'description': trans.description if trans else problem.description,
                        'url': request.build_absolute_uri(),
                    }).replace('"//', '"https://').replace("'//", "'https://"),
                    title=problem_name,
                )

        response = HttpResponse()
        response['Content-Type'] = 'application/pdf'
        response['Content-Disposition'] = f'inline; filename={pdf_basename}'

        if settings.DMOJ_PDF_PROBLEM_CACHE:
            pdf_filename = os.path.join(settings.DMOJ_PDF_PROBLEM_CACHE, pdf_basename)
            if not os.path.exists(pdf_filename):
                with open(pdf_filename, 'wb') as f:
                    f.write(render_problem_pdf())

            if settings.DMOJ_PDF_PROBLEM_INTERNAL:
                url_path = f'{settings.DMOJ_PDF_PROBLEM_INTERNAL}/{pdf_basename}'
            else:
                url_path = None

            add_file_response(request, response, url_path, pdf_filename)
        else:
            response.content = render_problem_pdf()

        return response


class ProblemList(QueryStringSortMixin, TitleMixin, SolvedProblemMixin, ListView):
    model = Problem
    title = gettext_lazy('Problems')
    context_object_name = 'problems'
    template_name = 'problem/list.html'
    paginate_by = 50
    sql_sort = frozenset(('points', 'ac_rate', 'user_count', 'code'))
    manual_sort = frozenset(('name', 'group', 'solved', 'type', 'editorial'))
    all_sorts = sql_sort | manual_sort
    default_desc = frozenset(('points', 'ac_rate', 'user_count'))
    default_sort = 'code'

    def get_paginator(self, queryset, per_page, orphans=0,
                      allow_empty_first_page=True, **kwargs):
        paginator = DiggPaginator(queryset, per_page, body=6, padding=2, orphans=orphans,
                                  count=queryset.values('pk').count() if not self.in_contest else None,
                                  allow_empty_first_page=allow_empty_first_page, **kwargs)
        if not self.in_contest:
            queryset = queryset.add_i18n_name(self.request.LANGUAGE_CODE)
            sort_key = self.order.lstrip('-')
            if sort_key in self.sql_sort:
                queryset = queryset.order_by(self.order, 'id')
            elif sort_key == 'name':
                queryset = queryset.order_by(self.order.replace('name', 'i18n_name'), 'id')
            elif sort_key == 'group':
                queryset = queryset.order_by(self.order + '__name', 'id')
            elif sort_key == 'editorial':
                queryset = queryset.order_by(self.order.replace('editorial', 'has_public_editorial'), 'id')
            elif sort_key == 'solved':
                if self.request.user.is_authenticated:
                    profile = self.request.profile
                    solved = user_completed_ids(profile)
                    attempted = user_attempted_ids(profile)

                    def _solved_sort_order(problem):
                        if problem.id in solved:
                            return 1
                        if problem.id in attempted:
                            return 0
                        return -1

                    queryset = list(queryset)
                    queryset.sort(key=_solved_sort_order, reverse=self.order.startswith('-'))
            elif sort_key == 'type':
                if self.show_types:
                    queryset = list(queryset)
                    queryset.sort(key=lambda problem: problem.types_list[0] if problem.types_list else '',
                                  reverse=self.order.startswith('-'))
            paginator.object_list = queryset
        return paginator

    @cached_property
    def profile(self):
        if not self.request.user.is_authenticated:
            return None
        return self.request.profile

    def get_contest_queryset(self):
        queryset = self.profile.current_contest.contest.contest_problems.select_related('problem__group') \
            .defer('problem__description').order_by('problem__code') \
            .annotate(user_count=Count('submission__participation', distinct=True)) \
            .annotate(i18n_translation=FilteredRelation(
                'problem__translations', condition=Q(problem__translations__language=self.request.LANGUAGE_CODE),
            )).annotate(i18n_name=Coalesce(
                F('i18n_translation__name'), F('problem__name'), output_field=CharField(),
            )).order_by('order')
        return [{
            'id': p['problem_id'],
            'code': p['problem__code'],
            'name': p['problem__name'],
            'i18n_name': p['i18n_name'],
            'group': {'full_name': p['problem__group__full_name']},
            'points': p['points'],
            'partial': p['partial'],
            'user_count': p['user_count'],
        } for p in queryset.values('problem_id', 'problem__code', 'problem__name', 'i18n_name',
                                   'problem__group__full_name', 'points', 'partial', 'user_count')]

    @staticmethod
    def apply_full_text(queryset, query):
        if recjk.search(query):
            # MariaDB can't tokenize CJK properly, fallback to LIKE '%term%' for each term.
            for term in query.split():
                queryset = queryset.filter(Q(code__icontains=term) | Q(name__icontains=term) |
                                           Q(description__icontains=term))
            return queryset
        return queryset.search(query, queryset.BOOLEAN).extra(order_by=['-relevance'])

    def get_normal_queryset(self):
        filter = Q(is_public=True)
        if not self.request.user.has_perm('see_organization_problem'):
            org_filter = Q(is_organization_private=False)
            if self.profile is not None:
                org_filter |= Q(organizations__in=self.profile.organizations.all())
            filter &= org_filter
        if self.profile is not None:
            filter = Problem.q_add_author_curator_tester(filter, self.profile)
        queryset = Problem.objects.filter(filter).select_related('group').defer('description', 'summary')
        if self.profile is not None and self.hide_solved:
            queryset = queryset.exclude(id__in=Submission.objects
                                        .filter(user=self.profile, result='AC', case_points__gte=F('case_total'))
                                        .values_list('problem_id', flat=True))
        if self.show_types:
            queryset = queryset.prefetch_related('types')
        queryset = queryset.annotate(has_public_editorial=Case(
            When(solution__is_public=True, solution__publish_on__lte=timezone.now(), then=True),
            default=False,
            output_field=BooleanField(),
        ))
        if self.has_public_editorial:
            queryset = queryset.filter(has_public_editorial=True)
        if self.category is not None:
            queryset = queryset.filter(group__id=self.category)
        if self.selected_types:
            queryset = queryset.filter(types__in=self.selected_types)
        if 'search' in self.request.GET:
            self.search_query = query = ' '.join(self.request.GET.getlist('search')).strip()
            if query:
                if settings.ENABLE_FTS and self.full_text:
                    queryset = self.apply_full_text(queryset, query)
                else:
                    queryset = queryset.filter(
                        Q(code__icontains=query) | Q(name__icontains=query) |
                        Q(translations__name__icontains=query, translations__language=self.request.LANGUAGE_CODE))
        self.prepoint_queryset = queryset
        if self.point_start is not None:
            queryset = queryset.filter(points__gte=self.point_start)
        if self.point_end is not None:
            queryset = queryset.filter(points__lte=self.point_end)
        return queryset.distinct()

    def get_queryset(self):
        if self.in_contest:
            return self.get_contest_queryset()
        else:
            return self.get_normal_queryset()

    def get_context_data(self, **kwargs):
        context = super(ProblemList, self).get_context_data(**kwargs)
        context['hide_solved'] = 0 if self.in_contest else int(self.hide_solved)
        context['show_types'] = 0 if self.in_contest else int(self.show_types)
        context['has_public_editorial'] = 0 if self.in_contest else int(self.has_public_editorial)
        context['full_text'] = 0 if self.in_contest else int(self.full_text)
        context['category'] = self.category
        context['categories'] = ProblemGroup.objects.all()
        if self.show_types:
            context['selected_types'] = self.selected_types
            context['problem_types'] = ProblemType.objects.all()
        context['has_fts'] = settings.ENABLE_FTS
        context['search_query'] = self.search_query
        context['completed_problem_ids'] = self.get_completed_problems()
        context['attempted_problems'] = self.get_attempted_problems()

        context.update(self.get_sort_paginate_context())
        if not self.in_contest:
            context.update(self.get_sort_context())
            context['hot_problems'] = hot_problems(timedelta(days=1), settings.DMOJ_PROBLEM_HOT_PROBLEM_COUNT)
            context['point_start'], context['point_end'], context['point_values'] = self.get_noui_slider_points()
        else:
            context['hot_problems'] = None
            context['point_start'], context['point_end'], context['point_values'] = 0, 0, {}
            context['hide_contest_scoreboard'] = self.contest.scoreboard_visibility in (
                self.contest.SCOREBOARD_AFTER_CONTEST,
                self.contest.SCOREBOARD_AFTER_PARTICIPATION,
                self.contest.SCOREBOARD_HIDDEN,
            )
        return context

    def get_noui_slider_points(self):
        points = sorted(self.prepoint_queryset.values_list('points', flat=True).distinct())
        if not points:
            return 0, 0, {}
        if len(points) == 1:
            return points[0] - 1, points[0] + 1, {
                'min': points[0] - 1,
                '50%': points[0],
                'max': points[0] + 1,
            }

        start, end = points[0], points[-1]
        if self.point_start is not None:
            start = self.point_start
        if self.point_end is not None:
            end = self.point_end
        points_map = {0.0: 'min', 1.0: 'max'}
        size = len(points) - 1
        return start, end, {points_map.get(i / size, '%.2f%%' % (100 * i / size,)): j for i, j in enumerate(points)}

    def GET_with_session(self, request, key):
        if not request.GET:
            return request.session.get(key, False)
        return request.GET.get(key, None) == '1'

    def setup_problem_list(self, request):
        self.hide_solved = self.GET_with_session(request, 'hide_solved')
        self.show_types = self.GET_with_session(request, 'show_types')
        self.full_text = self.GET_with_session(request, 'full_text')
        self.has_public_editorial = self.GET_with_session(request, 'has_public_editorial')

        self.search_query = None
        self.category = None
        self.selected_types = []

        # This actually copies into the instance dictionary...
        self.all_sorts = set(self.all_sorts)
        if not self.show_types:
            self.all_sorts.discard('type')

        self.category = safe_int_or_none(request.GET.get('category'))
        if 'type' in request.GET:
            try:
                self.selected_types = list(map(int, request.GET.getlist('type')))
            except ValueError:
                pass

        self.point_start = safe_float_or_none(request.GET.get('point_start'))
        self.point_end = safe_float_or_none(request.GET.get('point_end'))

    def get(self, request, *args, **kwargs):
        self.setup_problem_list(request)

        try:
            return super(ProblemList, self).get(request, *args, **kwargs)
        except ProgrammingError as e:
            return generic_message(request, 'FTS syntax error', e.args[1], status=400)

    def post(self, request, *args, **kwargs):
        to_update = ('hide_solved', 'show_types', 'has_public_editorial', 'full_text')
        for key in to_update:
            if key in request.GET:
                val = request.GET.get(key) == '1'
                request.session[key] = val
            else:
                request.session.pop(key, None)
        return HttpResponseRedirect(request.get_full_path())


class LanguageTemplateAjax(View):
    def get(self, request, *args, **kwargs):
        try:
            language = get_object_or_404(Language, id=int(request.GET.get('id', 0)))
        except ValueError:
            raise Http404()
        return HttpResponse(language.template, content_type='text/plain')


class RandomProblem(ProblemList):
    def get(self, request, *args, **kwargs):
        self.setup_problem_list(request)
        if self.in_contest:
            raise Http404()

        queryset = self.get_normal_queryset()
        count = queryset.count()
        if not count:
            return HttpResponseRedirect('%s%s%s' % (reverse('problem_list'), request.META['QUERY_STRING'] and '?',
                                                    request.META['QUERY_STRING']))
        return HttpResponseRedirect(queryset[randrange(count)].get_absolute_url())


user_logger = logging.getLogger('judge.user')


class ProblemSubmit(LoginRequiredMixin, ProblemMixin, TitleMixin, SingleObjectFormView):
    template_name = 'problem/submit.html'
    form_class = ProblemSubmitForm

    @cached_property
    def contest_problem(self):
        if self.request.profile.current_contest is None:
            return None
        return get_contest_problem(self.object, self.request.profile)

    @cached_property
    def remaining_submission_count(self):
        max_subs = self.contest_problem and self.contest_problem.max_submissions
        if max_subs is None:
            return None
        # When an IE submission is rejudged into a non-IE status, it will count towards the
        # submission limit. We max with 0 to ensure that `remaining_submission_count` returns
        # a non-negative integer, which is required for future checks in this view.
        return max(
            0,
            max_subs - get_contest_submission_count(
                self.object, self.request.profile, self.request.profile.current_contest.virtual,
            ),
        )

    @cached_property
    def default_language(self):
        # If the old submission exists, use its language, otherwise use the user's default language.
        if self.old_submission is not None:
            return self.old_submission.language
        return self.request.profile.language

    def get_content_title(self):
        return mark_safe(
            escape(_('Submit to %s')) % format_html(
                '<a href="{0}">{1}</a>',
                reverse('problem_detail', args=[self.object.code]),
                self.object.translated_name(self.request.LANGUAGE_CODE),
            ),
        )

    def get_title(self):
        return _('Submit to %s') % self.object.translated_name(self.request.LANGUAGE_CODE)

    def get_initial(self):
        initial = {'language': self.default_language}
        if self.old_submission is not None:
            initial['source'] = self.old_submission.source.source
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = Submission(user=self.request.profile, problem=self.object)

        if self.object.is_editable_by(self.request.user):
            kwargs['judge_choices'] = tuple(
                Judge.objects.filter(online=True, problems=self.object).values_list('name', 'name'),
            )
        else:
            kwargs['judge_choices'] = ()

        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        form.fields['language'].queryset = (
            self.object.usable_languages.order_by('name', 'key')
            .prefetch_related(Prefetch('runtimeversion_set', RuntimeVersion.objects.order_by('priority')))
        )

        form_data = getattr(form, 'cleaned_data', form.initial)
        if 'language' in form_data:
            form.fields['source'].widget.mode = form_data['language'].ace
        form.fields['source'].widget.theme = self.request.profile.resolved_ace_theme

        return form

    def get_success_url(self):
        return reverse('submission_status', args=(self.new_submission.id,))

    def form_valid(self, form):
        if (
            not self.request.user.has_perm('judge.spam_submission') and
            Submission.objects.filter(user=self.request.profile, rejudged_date__isnull=True)
                              .exclude(status__in=['D', 'IE', 'CE', 'AB']).count() >= settings.DMOJ_SUBMISSION_LIMIT
        ):
            return HttpResponse(format_html('<h1>{0}</h1>', _('You submitted too many submissions.')), status=429)
        if not self.object.allowed_languages.filter(id=form.cleaned_data['language'].id).exists():
            raise PermissionDenied()
        if not self.request.user.is_superuser and self.object.banned_users.filter(id=self.request.profile.id).exists():
            return generic_message(self.request, _('Banned from submitting'),
                                   _('You have been declared persona non grata for this problem. '
                                     'You are permanently barred from submitting to this problem.'))
        # Must check for zero and not None. None means infinite submissions remaining.
        if self.remaining_submission_count == 0:
            return generic_message(self.request, _('Too many submissions'),
                                   _('You have exceeded the submission limit for this problem.'))

        with transaction.atomic():
            participation = self.request.profile.current_contest
            if participation is not None and participation.team_id:
                from judge.models import ContestParticipation
                participation = ContestParticipation.objects.select_for_update().get(pk=participation.pk)
                if (participation.ended or participation.is_disqualified or
                        not participation.contains_profile(self.request.profile.pk)):
                    return generic_message(self.request, _('Cannot enter'), _('Esta participación ya no admite envíos.'), status=403)
                if (self.contest_problem is not None and self.contest_problem.max_submissions and
                        get_contest_submission_count(self.object, self.request.profile, participation.virtual) >= self.contest_problem.max_submissions):
                    return generic_message(self.request, _('Too many submissions'),
                                           _('You have exceeded the submission limit for this problem.'), status=400)
            self.new_submission = form.save(commit=False)

            contest_problem = self.contest_problem
            if contest_problem is not None:
                # Use the contest object from current_contest.contest because we already use it
                # in profile.update_contest().
                self.new_submission.contest_object = self.request.profile.current_contest.contest
                if self.request.profile.current_contest.live:
                    self.new_submission.locked_after = self.new_submission.contest_object.locked_after
                self.new_submission.save()
                ContestSubmission(
                    submission=self.new_submission,
                    problem=contest_problem,
                    participation=self.request.profile.current_contest,
                ).save()
            else:
                self.new_submission.save()

            source = SubmissionSource(submission=self.new_submission, source=form.cleaned_data['source'])
            source.save()

        # Save a query.
        self.new_submission.source = source
        self.new_submission.judge(force_judge=True, judge_id=form.cleaned_data['judge'])

        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['langs'] = Language.objects.all()
        context['no_judges'] = not context['form'].fields['language'].queryset
        context['submission_limit'] = self.contest_problem and self.contest_problem.max_submissions
        context['submissions_left'] = self.remaining_submission_count
        context['ACE_URL'] = settings.ACE_URL
        context['default_lang'] = self.default_language
        return context

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except Http404:
            # Is this really necessary? This entire post() method could be removed if we don't log this.
            user_logger.info(
                'Naughty user %s wants to submit to %s without permission',
                request.user.username,
                kwargs.get(self.slug_url_kwarg),
            )
            return HttpResponseForbidden(format_html('<h1>{0}</h1>', _('Do you want me to ban you?')))

    def dispatch(self, request, *args, **kwargs):
        submission_id = kwargs.get('submission')
        if submission_id is not None:
            self.old_submission = get_object_or_404(
                Submission.objects.select_related('source', 'language'),
                id=submission_id,
            )
            if not request.user.has_perm('judge.resubmit_other') and not self.old_submission.is_owned_by(request.user):
                raise PermissionDenied()
        else:
            self.old_submission = None

        return super().dispatch(request, *args, **kwargs)


class ProblemClone(ProblemMixin, PermissionRequiredMixin, TitleMixin, SingleObjectFormView):
    title = gettext_lazy('Clone Problem')
    template_name = 'problem/clone.html'
    form_class = ProblemCloneForm
    permission_required = 'judge.clone_problem'

    def form_valid(self, form):
        problem = self.object

        languages = problem.allowed_languages.all()
        language_limits = problem.language_limits.all()
        organizations = problem.organizations.all()
        types = problem.types.all()
        old_code = problem.code

        problem.pk = None
        problem.is_public = False
        problem.ac_rate = 0
        problem.user_count = 0
        problem.code = form.cleaned_data['code']
        with revisions.create_revision(atomic=True):
            problem.save()
            problem.authors.add(self.request.profile)
            problem.allowed_languages.set(languages)
            problem.language_limits.set(language_limits)
            problem.organizations.set(organizations)
            problem.types.set(types)
            revisions.set_user(self.request.user)
            revisions.set_comment(_('Cloned problem from %s') % old_code)

        return HttpResponseRedirect(reverse('admin:judge_problem_change', args=(problem.id,)))


import shutil
import stat
import uuid
from django.core import signing
from django.core.cache import cache
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from judge.models import SubmissionTestCase

CUSTOM_TEST_TIME_LIMIT = 2.0
CUSTOM_TEST_MEMORY_LIMIT = 524288
CUSTOM_TEST_MARKER = 'CPC custom test v1:'
CUSTOM_TEST_SIGNING_SALT = 'judge.custom-test.problem.v1'
# Cuántos bytes de la salida del programa devuelve el juez. Sin esto el juez
# aplica su valor por omisión, 64 bytes, que corta la salida a media línea y
# vuelve la herramienta inútil para depurar. No es el límite de lo que el
# programa puede imprimir: eso lo rige `output_limit_length` del juez (24 MiB),
# que no se toca. Se queda en el mismo archivo de la prueba, así que se afloja
# desde la configuración privada con CPC_CUSTOM_TEST_OUTPUT_PREFIX.
CUSTOM_TEST_OUTPUT_PREFIX = 65536


# Per-user limits for the custom test tool. The site tree is a read-only bind
# mount for dmoj-web, so every ceiling is overridable from the private settings
# file (CPC_CUSTOM_TEST_*) without editing this tree. Zero or less disables one.
CUSTOM_TEST_MAX_IN_FLIGHT = 2
CUSTOM_TEST_MAX_PER_MINUTE = 12
CUSTOM_TEST_MAX_PER_HOUR = 200
# A test still queued after this long stops counting against the in-flight
# limit, so an offline judge cannot lock a user out for good.
CUSTOM_TEST_IN_FLIGHT_MAX_AGE = timedelta(minutes=10)


def _custom_test_limit(name, fallback):
    value = getattr(settings, 'CPC_CUSTOM_TEST_' + name, fallback)
    return value if isinstance(value, int) and not isinstance(value, bool) else fallback


def _custom_test_output_prefix():
    # Un valor absurdo en la configuración privada rompería el init.yml del juez,
    # así que se acota en vez de confiar en él.
    value = _custom_test_limit('OUTPUT_PREFIX', CUSTOM_TEST_OUTPUT_PREFIX)
    return min(max(value, 1024), 1048576)


def _custom_test_window_refusal(profile):
    # Fixed windows held in the shared cache, so all uWSGI workers count once.
    # A cache outage must not take the tool down: it degrades to the in-flight
    # check below, which reads the database.
    now = int(timezone.now().timestamp())
    for label, span, fallback, message in (
            ('MINUTE', 60, CUSTOM_TEST_MAX_PER_MINUTE,
             'Demasiadas pruebas seguidas. Espera un momento antes de volver a ejecutar.'),
            ('HOUR', 3600, CUSTOM_TEST_MAX_PER_HOUR,
             'Alcanzaste el máximo de pruebas personalizadas por hora. Inténtalo más tarde.')):
        ceiling = _custom_test_limit('MAX_PER_' + label, fallback)
        if ceiling <= 0:
            continue
        key = 'custom-test:%s:%d:%d' % (label.lower(), profile.pk, now // span)
        try:
            cache.add(key, 0, span * 2)
            used = cache.incr(key)
        except Exception:
            logging.getLogger(__name__).warning('Custom test rate counter unavailable; window not enforced.')
            return None
        if used > ceiling:
            return message
    return None


def _custom_test_in_flight_refusal(profile):
    ceiling = _custom_test_limit('MAX_IN_FLIGHT', CUSTOM_TEST_MAX_IN_FLIGHT)
    if ceiling <= 0:
        return None
    cutoff = timezone.now() - CUSTOM_TEST_IN_FLIGHT_MAX_AGE
    running = Submission.objects.filter(
        user=profile, status__in=('QU', 'P', 'G'), date__gte=cutoff,
        problem__summary__startswith=CUSTOM_TEST_MARKER,
    ).count()
    if running >= ceiling:
        return ('Ya tienes %d prueba(s) personalizada(s) en ejecución. '
                'Espera a que terminen antes de enviar otra.') % running
    return None


def _custom_test_refusal(profile):
    # Cheapest control first: the cache counter before the database query.
    return _custom_test_window_refusal(profile) or _custom_test_in_flight_refusal(profile)


def _is_owned_custom_test(submission, profile):
    problem = submission.problem
    if (submission.user_id != profile.pk or submission.contest_object_id is not None or
            not re.fullmatch(r'ct_[0-9a-f]{12}', problem.code) or
            problem.is_public or not problem.is_organization_private or problem.points != 0 or
            not problem.summary.startswith(CUSTOM_TEST_MARKER)):
        return False
    try:
        identity = signing.loads(problem.summary[len(CUSTOM_TEST_MARKER):], salt=CUSTOM_TEST_SIGNING_SALT)
    except (signing.BadSignature, ValueError, TypeError):
        return False
    return identity == [problem.pk, problem.code, profile.pk]


def _custom_test_directory(code):
    # Only direct children of the configured root are eligible; never symlinks.
    if not re.fullmatch(r'ct_[0-9a-f]{12}', code):
        raise ValueError('Invalid custom test directory')
    root = os.path.realpath(settings.DMOJ_PROBLEM_DATA_ROOT)
    path = os.path.join(root, code)
    if os.path.realpath(path) != path:
        raise ValueError('Unsafe custom test directory')
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return path, None
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Invalid custom test directory type')
    return path, (info.st_dev, info.st_ino)


def _remove_custom_test_directory(code, expected_identity):
    path, identity = _custom_test_directory(code)
    if identity is not None and identity == expected_identity and shutil.rmtree.avoids_symlink_attacks:
        shutil.rmtree(path)


def _cleanup_owned_custom_tests(profile):
    # Run only from a valid POST. Preserve legacy/unverified data and active jobs.
    cutoff = timezone.now() - timedelta(minutes=5)
    candidates = list(Submission.objects.filter(
        user=profile, date__lt=cutoff, problem__summary__startswith=CUSTOM_TEST_MARKER,
    ).exclude(status__in=('QU', 'P', 'G')).order_by('id').values_list('id', flat=True)[:10])
    for submission_id in candidates:
        try:
            with transaction.atomic():
                submission = Submission.objects.select_for_update().select_related('problem').get(
                    pk=submission_id, user=profile)
                if (submission.status in ('QU', 'P', 'G') or submission.date >= cutoff or
                        not _is_owned_custom_test(submission, profile)):
                    continue
                problem = submission.problem
                if (problem.authors.exists() or problem.curators.exists() or problem.contests.exists() or
                        Submission.objects.filter(problem=problem).exclude(pk=submission.pk).exists()):
                    continue
                _, identity = _custom_test_directory(problem.code)
                code = problem.code
                problem.delete()
                # Also respect an outer transaction (e.g. ATOMIC_REQUESTS).
                transaction.on_commit(lambda code=code, identity=identity:
                                      _remove_custom_test_directory(code, identity))
        except Exception:
            logging.getLogger(__name__).warning('Custom test cleanup skipped; data may be retained.')


class CustomTestView(LoginRequiredMixin, TitleMixin, View):
    def get_title(self):
        return _('Custom Test')

    def get(self, request, *args, **kwargs):
        languages = Language.objects.filter(runtimeversion__judge__online=True).distinct().order_by('name', 'key')
        if not languages.exists():
            languages = Language.objects.all().order_by('name', 'key')

        default_lang = request.profile.language or languages.first()
        return render(request, 'problem/custom_test.html', {
            'languages': languages,
            'default_lang': default_lang,
            'ACE_URL': settings.ACE_URL,
            # Con el tema del sitio en «auto» esto es None y el tema del editor se
            # resuelve en el navegador, igual que hace django_ace/widget.js.
            'ace_theme': request.profile.resolved_ace_theme,
            'ace_light_theme': settings.ACE_DEFAULT_LIGHT_THEME,
            'ace_dark_theme': settings.ACE_DEFAULT_DARK_THEME,
            'time_limit': CUSTOM_TEST_TIME_LIMIT,
            'memory_limit': CUSTOM_TEST_MEMORY_LIMIT,
            'output_prefix': _custom_test_output_prefix(),
        })


@login_required
@require_http_methods(['GET', 'POST'])
def custom_test_run(request):
    if request.method == 'POST':
        refusal = _custom_test_refusal(request.profile)
        if refusal is not None:
            return JsonResponse({'error': refusal}, status=429)

        import json
        try:
            data = json.loads(request.body)
        except ValueError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
        if not isinstance(data, dict) or any(
                not isinstance(data.get(key, ''), str) for key in ('source', 'language', 'input')):
            return JsonResponse({'error': 'Invalid test data'}, status=400)

        source_code = data.get('source', '')
        lang_key = data.get('language', '')
        custom_input = data.get('input', '')
        try:
            language = Language.objects.get(key=lang_key)
        except Language.DoesNotExist:
            return JsonResponse({'error': 'Invalid language'}, status=400)

        try:
            _cleanup_owned_custom_tests(request.profile)
        except Exception:
            logging.getLogger(__name__).warning('Custom test cleanup unavailable; data retained.')

        problem_code = 'ct_' + uuid.uuid4().hex[:12]
        directory_identity = None
        try:
            problem_dir, _ = _custom_test_directory(problem_code)
            # Exclusive creation prevents overwriting an existing problem directory.
            os.mkdir(problem_dir)
            info = os.lstat(problem_dir)
            directory_identity = (info.st_dev, info.st_ino)
            with open(os.path.join(problem_dir, 'input.txt'), 'x', encoding='utf-8') as stream:
                stream.write(custom_input)
            with open(os.path.join(problem_dir, 'output.txt'), 'x', encoding='utf-8') as stream:
                stream.write('')
            with open(os.path.join(problem_dir, 'init.yml'), 'x', encoding='utf-8') as stream:
                stream.write('wall_time_factor: 1\noutput_prefix_length: %d\n'
                             'test_cases:\n- in: input.txt\n  out: output.txt\n  points: 0\n'
                             % _custom_test_output_prefix())

            # Roll back only rows created by this request if preparation fails.
            with transaction.atomic():
                group = ProblemGroup.objects.first()
                if not group:
                    group = ProblemGroup.objects.create(name='default', full_name='Default')
                problem = Problem.objects.create(
                    code=problem_code, name='Prueba Personalizada',
                    is_public=False, is_organization_private=True, group=group,
                    time_limit=CUSTOM_TEST_TIME_LIMIT, memory_limit=CUSTOM_TEST_MEMORY_LIMIT,
                    points=0.0, summary='', description='Temporary problem for Custom Test',
                )
                problem.summary = CUSTOM_TEST_MARKER + signing.dumps(
                    [problem.pk, problem.code, request.profile.pk], salt=CUSTOM_TEST_SIGNING_SALT)
                problem.save(update_fields=['summary'])
                problem.allowed_languages.add(language)
                submission = Submission.objects.create(
                    user=request.profile, problem=problem, language=language, status='QU')
                source = SubmissionSource.objects.create(submission=submission, source=source_code)
                submission.source = source
        except Exception:
            if directory_identity is not None:
                try:
                    _remove_custom_test_directory(problem_code, directory_identity)
                except Exception:
                    pass
            return JsonResponse({'error': 'Unable to prepare the custom test. Please try again.'}, status=500)

        try:
            online_dedicated = Judge.objects.filter(name__startswith='cpc-custom-', online=True)
            target_judge = online_dedicated.first().name if online_dedicated.exists() else None
            submission.judge(force_judge=True, judge_id=target_judge, batch_rejudge=False)
        except Exception:
            # The bridge may have accepted the job before the connection failed.
            # Preserve its data rather than deleting a possibly active execution.
            return JsonResponse({'error': 'Unable to confirm scheduling. Please try again later.'}, status=500)

        return JsonResponse({'status': 'queued', 'submission_id': submission.id})

    sub_id = request.GET.get('id')
    if not sub_id:
        return JsonResponse({'error': 'Missing id parameter'}, status=400)
    try:
        sub_id = int(sub_id)
        if not 0 < sub_id <= 9223372036854775807:
            raise ValueError
        submission = Submission.objects.select_related('problem').get(pk=sub_id, user=request.profile)
    except (Submission.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'error': 'Submission not found'}, status=404)
    if not _is_owned_custom_test(submission, request.profile):
        return JsonResponse({'error': 'Submission not found'}, status=404)

    if submission.status in ('QU', 'P', 'G'):
        return JsonResponse({'status': 'grading', 'current_testcase': submission.current_testcase})

    response_data = {
        'status': 'done', 'result': submission.result, 'time': submission.time,
        'memory': submission.memory, 'error': submission.error or '', 'output': '',
    }
    if submission.status == 'D':
        testcases = SubmissionTestCase.objects.filter(submission=submission).order_by('case')
        if testcases.exists():
            output = testcases.first().output
            response_data['output'] = output
            # El juez corta en bytes; comparar en bytes evita avisar de más con
            # salidas acentuadas.
            limit = _custom_test_output_prefix()
            response_data['output_truncated'] = len(output.encode('utf-8')) >= limit
            response_data['output_limit'] = limit
    # Polling is read-only, including repeated and malformed requests.
    return JsonResponse(response_data)

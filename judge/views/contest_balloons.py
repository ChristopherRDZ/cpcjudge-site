"""Balloon sheet: which accepted problems still need a balloon on an on-site contest, and who delivered the rest.

A balloon is due for the first full, finally judged AC of an official participation on a problem, by the same rule
the reveal uses for first to solve. Nothing stores that a balloon is due: it is worked out from the submissions on
every read, so a rejudge moves balloons around by itself. The only thing written is the log of deliveries and of
deliveries taken back, `ContestBalloonAction`, and a balloon counts as delivered when its latest entry says so.

No balloons during the freeze. An AC sent once a participation has entered its freeze window never becomes due,
not even after the scoreboard is revealed: carrying a balloon to a team would give away exactly what the frozen
scoreboard is hiding. Balloon staff are only told that the freeze is on; whoever edits the contest also sees how
many ACs it is holding back.
"""
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from judge import event_poster as event
from judge.models import Contest, ContestBalloonAction, ContestLocation, ContestParticipation, ContestProblem, \
    ContestSubmission, Profile
from judge.models.balloon import MAX_LOCATION

__all__ = ['contest_balloons', 'contest_balloons_ajax', 'contest_balloon_mark', 'contest_balloon_locations']

# How much of the log the sheet shows. The full log stays in the database.
LOG_LENGTH = 100

LOCATION_PAGE_SIZE = 100
LOCATION_SALT = 'contest-balloon-locations-v1'


def accepted_submissions(contest):
    """ContestSubmissions that earn a balloon, before the freeze is taken into account.

    Same rule as `contest_reveal.first_to_solve`: full points on a problem worth something, finally judged, not
    a pretest-only result, inside the contest window of a live participation that is not disqualified.
    """
    accepted = ContestSubmission.objects.filter(
        participation__contest=contest, participation__virtual=ContestParticipation.LIVE,
        participation__is_disqualified=False, problem__contest=contest,
        submission__status='D', submission__result='AC', is_pretest=False,
        submission__is_pretested=False, points__gte=F('problem__points'), problem__points__gt=0,
        submission__date__gte=contest.start_time, submission__date__lt=contest.end_time,
    )
    if contest.time_limit is not None:
        accepted = accepted.filter(submission__date__gte=F('participation__real_start'),
                                   submission__date__lt=F('participation__real_start') + contest.time_limit)
    return accepted


def is_held(participation, time):
    """Whether an AC at this time falls in the participation's freeze window, and so earns no balloon."""
    start = participation.freeze_starts_at
    return start is not None and time >= start


def _contest_time(participation, time):
    seconds = max(int((time - participation.start).total_seconds()), 0)
    return '%d:%02d' % (seconds // 3600, seconds // 60 % 60)


class Balloon:
    def __init__(self, participation, problem, label):
        self.participation = participation
        self.problem = problem
        self.label = label
        self.solved_at = None
        self.contest_time = ''
        self.first_blood = False
        self.delivered_by = None
        self.delivered_at = None

    @property
    def key(self):
        return '%d-%d' % (self.participation.id, self.problem.id)

    @property
    def profile(self):
        return self.participation.user

    @property
    def is_accepted(self):
        return self.solved_at is not None


class BalloonSheet:
    def __init__(self, contest):
        self.contest = contest
        self.problems = []
        self.pending = []
        self.delivered = []
        self.log = []
        self.log_total = 0
        self.held = 0
        self.locations = {}

    @property
    def freeze_on(self):
        """Whether ACs are being held back right now, or were held back before a reveal."""
        return self.contest.freeze_delta is not None and self.contest.freeze_started


def build_balloon_sheet(contest):
    """Everything the balloon sheet shows. Reads only."""
    sheet = BalloonSheet(contest)
    problems = list(contest.contest_problems.select_related('problem').only(
        'id', 'order', 'points', 'balloon_color', 'balloon_color_name', 'problem__code', 'problem__name',
    ).order_by('order'))
    labels = {problem.id: contest.get_label_for_problem(i) for i, problem in enumerate(problems)}
    problem_by_id = {problem.id: problem for problem in problems}

    participations = {}
    for participation in (contest.users.filter(virtual=ContestParticipation.LIVE, is_disqualified=False)
                          .select_related('user__user').only('id', 'real_start', 'virtual', 'contest_id',
                                                              'user__id', 'user__username_display_override',
                                                              'user__user__username')):
        # Reusing the contest already in hand; `participation.contest` would be one query per row.
        participation.contest = contest
        participations[participation.id] = participation

    balloons = {}
    first_blood_taken = set()
    rows = (accepted_submissions(contest).order_by('submission__date', 'submission_id')
            .values_list('participation_id', 'problem_id', 'submission__date'))
    for participation_id, problem_id, date in rows:
        participation = participations.get(participation_id)
        if participation is None or (participation_id, problem_id) in balloons:
            continue
        if is_held(participation, date):
            # Marked so a later AC on the same problem by the same participation is not taken for its first.
            balloons[participation_id, problem_id] = None
            sheet.held += 1
            continue
        balloon = Balloon(participation, problem_by_id[problem_id], labels[problem_id])
        balloon.solved_at = date
        balloon.contest_time = _contest_time(participation, date)
        if problem_id not in first_blood_taken:
            first_blood_taken.add(problem_id)
            balloon.first_blood = True
        balloons[participation_id, problem_id] = balloon

    actions = list(ContestBalloonAction.objects.filter(problem__contest=contest)
                   .select_related('user__user', 'participation__user__user').order_by('time', 'id'))
    latest = {}
    for action in actions:
        latest[action.participation_id, action.problem_id] = action
    for key, action in latest.items():
        if action.action != ContestBalloonAction.DELIVERED:
            continue
        balloon = balloons.get(key)
        if balloon is None:
            # Delivered, but no longer accepted: rejudged, disqualified, or its AC now counts as frozen. It stays
            # on the list so whoever delivered it knows to go and look.
            participation = participations.get(action.participation_id) or action.participation
            participation.contest = contest
            balloon = Balloon(participation, problem_by_id[action.problem_id], labels[action.problem_id])
        balloon.delivered_by = action.user
        balloon.delivered_at = action.time
        sheet.delivered.append(balloon)

    delivered_keys = {(balloon.participation.id, balloon.problem.id) for balloon in sheet.delivered}
    sheet.pending = [balloon for key, balloon in balloons.items()
                     if balloon is not None and key not in delivered_keys]
    sheet.pending.sort(key=lambda balloon: balloon.solved_at)
    sheet.delivered.sort(key=lambda balloon: balloon.delivered_at, reverse=True)

    for problem in problems:
        due = [balloon for balloon in balloons.values() if balloon is not None and balloon.problem.id == problem.id]
        sheet.problems.append({
            'problem': problem,
            'label': labels[problem.id],
            'accepted': len(due),
            'pending': sum(1 for balloon in sheet.pending if balloon.problem.id == problem.id),
        })

    sheet.log_total = len(actions)
    for action in reversed(actions[-LOG_LENGTH:]):
        sheet.log.append({
            'action': action,
            'label': labels[action.problem_id],
            'problem': problem_by_id[action.problem_id],
            'profile': action.participation.user,
        })

    profile_ids = {participation.user_id for participation in participations.values()}
    profile_ids.update(balloon.participation.user_id for balloon in sheet.delivered)
    sheet.locations = dict(ContestLocation.objects.filter(contest=contest, user_id__in=profile_ids)
                           .values_list('user_id', 'location'))
    return sheet


def balloon_is_due(contest, participation, problem):
    participation.contest = contest
    if participation.virtual != ContestParticipation.LIVE or participation.is_disqualified:
        return False
    first = (accepted_submissions(contest).filter(participation=participation, problem=problem)
             .order_by('submission__date', 'submission_id').values_list('submission__date', flat=True).first())
    return first is not None and not is_held(participation, first)


def _balloon_contest(request, key):
    contest = get_object_or_404(Contest, key=key)
    # Everybody else is told the page does not exist, as with the reveal.
    if not contest.can_manage_balloons(request.user):
        raise Http404()
    return contest


def _sheet_context(request, contest):
    can_edit = contest.is_editable_by(request.user)
    return {
        'contest': contest,
        'sheet': build_balloon_sheet(contest),
        'can_edit': can_edit,
    }


@login_required
def contest_balloons(request, contest):
    contest = _balloon_contest(request, contest)
    return _render_balloons(request, contest)


def location_profiles(contest):
    """Official entrants, preassigned private contestants and previously saved locations."""
    ids = set(contest.users.filter(virtual=ContestParticipation.LIVE).values_list('user_id', flat=True))
    ids.update(contest.private_contestants.values_list('id', flat=True))
    ids.update(ContestLocation.objects.filter(contest=contest).values_list('user_id', flat=True))
    return Profile.objects.filter(id__in=ids).select_related('user').order_by('user__username')


def _render_balloons(request, contest, location_errors=None, submitted=None, status=200):
    profile = request.profile
    context = _sheet_context(request, contest)
    context.update({
        'title': _('Balloons'),
        'tab': 'balloons',
        # What contest-tabs.html expects from the contest views.
        'now': timezone.now(),
        'is_editor': profile.id in contest.editor_ids,
        'is_tester': profile.id in contest.tester_ids,
        'has_joined': contest.users.filter(user=profile, virtual=ContestParticipation.LIVE).exists(),
        'last_msg': event.last(),
        'event_daemon': getattr(event, 'real', False),
        'balloon_errors': request.session.pop('balloon_errors', []),
        'balloon_notices': request.session.pop('balloon_notices', []),
    })
    if context['can_edit']:
        query = request.GET.get('q', '').strip()[:150]
        profiles = location_profiles(contest)
        if query:
            profiles = profiles.filter(Q(user__username__icontains=query) | Q(username_display_override__icontains=query))
        page = Paginator(profiles, LOCATION_PAGE_SIZE).get_page(request.GET.get('page'))
        locations = dict(ContestLocation.objects.filter(contest=contest).values_list('user_id', 'location'))
        rows = [{'profile': profile, 'location': locations.get(profile.id, '')} for profile in page]
        snapshot = {str(row['profile'].id): row['location'] for row in rows}
        if submitted is not None:
            for row in rows:
                row['location'] = submitted.get(str(row['profile'].id), row['location'])
        context.update(location_rows=rows, location_page=page, location_query=query, max_location=MAX_LOCATION,
                       location_snapshot=signing.dumps({'contest': contest.id, 'rows': snapshot}, salt=LOCATION_SALT))
        if location_errors:
            context['balloon_errors'] = location_errors
    return render(request, 'contest/balloons.html', context, status=status)


@login_required
def contest_balloons_ajax(request, contest):
    contest = _balloon_contest(request, contest)
    return render(request, 'contest/balloons-sheet.html', _sheet_context(request, contest))


def _is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'


@login_required
@require_POST
def contest_balloon_mark(request, contest):
    contest = _balloon_contest(request, contest)
    action = request.POST.get('balloon_action')
    if action not in (ContestBalloonAction.DELIVERED, ContestBalloonAction.UNDONE):
        return HttpResponseBadRequest()
    try:
        participation_id = int(request.POST.get('participation', ''))
        problem_id = int(request.POST.get('problem', ''))
    except ValueError:
        return HttpResponseBadRequest()
    problem = get_object_or_404(ContestProblem, id=problem_id, contest=contest)

    error = None
    with transaction.atomic():
        # Locking the participation serialises two volunteers pressing the same button in the same second: the
        # second one sees the first delivery and is told so, instead of logging the balloon twice.
        participation = get_object_or_404(ContestParticipation.objects.select_for_update(),
                                          id=participation_id, contest=contest)
        latest = (ContestBalloonAction.objects.filter(participation=participation, problem=problem)
                  .select_related('user__user').order_by('-time', '-id').first())
        delivered = latest is not None and latest.action == ContestBalloonAction.DELIVERED
        if action == ContestBalloonAction.DELIVERED:
            if delivered:
                error = _('%(user)s already marked this balloon as delivered.') % {
                    'user': latest.user.username if latest.user else '?'}
            elif not balloon_is_due(contest, participation, problem):
                error = _('This balloon is not due anymore. The sheet has been refreshed.')
        elif not delivered:
            error = _('This balloon is not marked as delivered anymore. The sheet has been refreshed.')

        if error is None:
            ContestBalloonAction.objects.create(participation=participation, problem=problem, action=action,
                                                user=request.profile)
            # Every other open sheet refreshes; the message carries nothing but the fact that something changed.
            transaction.on_commit(lambda: event.post('balloons_%d' % contest.id, {'type': 'update'}))

    if _is_ajax(request):
        return JsonResponse({'ok': error is None, 'message': error or ''})
    if error is not None:
        request.session['balloon_errors'] = [error]
    return HttpResponseRedirect(reverse('contest_balloons', args=[contest.key]))


@login_required
@require_POST
def contest_balloon_locations(request, contest):
    """Update only edited rows of the displayed page; never replace the whole roster."""
    contest = get_object_or_404(Contest, key=contest)
    if not contest.is_editable_by(request.user):
        raise Http404()

    try:
        snapshot = signing.loads(request.POST.get('location_snapshot', ''), salt=LOCATION_SALT, max_age=86400)
        if snapshot['contest'] != contest.id or len(snapshot['rows']) > LOCATION_PAGE_SIZE:
            raise signing.BadSignature()
    except (signing.BadSignature, KeyError, TypeError):
        return _render_balloons(request, contest, [_('This form has expired. Reload the page and try again.')], status=400)
    initial = snapshot['rows']
    submitted = {}
    for profile_id in initial:
        values = request.POST.getlist('location_' + profile_id)
        if len(values) != 1 or len(values[0].strip()) > MAX_LOCATION:
            return _render_balloons(request, contest, [_('Some locations are missing or too long. Nothing was saved.')],
                                   submitted={key: request.POST.get('location_' + key, value)
                                              for key, value in initial.items()}, status=400)
        submitted[profile_id] = values[0].strip()
    changes = {key: value for key, value in submitted.items() if value != initial[key]}

    with transaction.atomic():
        # Serialise editors and compare only edited fields, so an old tab cannot overwrite someone else's work.
        Contest.objects.select_for_update().get(pk=contest.pk)
        allowed = {str(pk) for pk in location_profiles(contest).values_list('id', flat=True)}
        current = {str(pk): value for pk, value in
                   ContestLocation.objects.filter(contest=contest, user_id__in=changes).values_list('user_id', 'location')}
        if any(key not in allowed or current.get(key, '') != initial[key] for key in changes):
            return _render_balloons(request, contest,
                                   [_('Another editor changed these locations. Review your entries and save again.')],
                                   submitted=submitted, status=409)
        for profile_id, value in changes.items():
            if value:
                ContestLocation.objects.update_or_create(contest=contest, user_id=profile_id, defaults={'location': value})
            else:
                ContestLocation.objects.filter(contest=contest, user_id=profile_id).delete()
        if changes:
            transaction.on_commit(lambda: event.post('balloons_%d' % contest.id, {'type': 'update'}))
    request.session['balloon_notices'] = [_('Locations saved: %(count)d.') % {'count': len(changes)}]
    url = reverse('contest_balloons', args=[contest.key])
    if request.GET:
        url += '?' + request.GET.urlencode()
    return HttpResponseRedirect(url + '#locations')

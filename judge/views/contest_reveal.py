"""Scoreboard reveal: the frozen scoreboard of a contest, uncovered one problem at a time at the closing ceremony.

Everything is decided here, once, when the page loads: the frozen board, the real one, and the whole sequence of
steps that leads from one to the other. The page in the browser only plays that sequence back, so the order of
the reveal is worked out by code the laboratory can test, and a slow projector laptop cannot get it wrong.

The order is the ICPC one. The lowest row that still hides something uncovers its leftmost hidden problem; if that
moves it up, the row that takes its place goes next. A row with nothing left to uncover is passed, including rows
that never submitted anything during the freeze, and the reveal moves one row up.

A problem cell is uncovered as a whole, with every submission sent to it during the freeze, as the ICPC Resolver
does. Uncovering those submissions one by one would need the cell as it stood after each of them, and the contest
formats only ever store the final one.

Nothing in this module writes to the database except `contest_reveal_publish`, which does exactly what the
"Reveal frozen scoreboards" action of the administration does.
"""
import hashlib
import json
import math
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import F, OuterRef, Subquery
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.defaultfilters import floatformat
from django.urls import reverse
from django.utils.html import json_script
from django.utils.translation import gettext as _, ngettext
from django.views.decorators.http import require_POST

from judge.jinja2.gravatar import gravatar
from judge.models import Contest, ContestParticipation, ContestSubmission, Submission
from judge.utils.timedelta import nice_repr

__all__ = ['contest_reveal', 'contest_reveal_publish']

# Formats whose cells are read as "solved after so many wrong tries", like an ICPC scoreboard.
TRIES_FORMATS = ('icpc', 'atcoder')


def _points(cell):
    return (cell or {}).get('points') or 0


def format_totals(format_name, config, cells):
    """(score, cumtime, tiebreaker) for these cells, added up the way the contest format adds them up.

    `cells` maps a contest problem id to the dictionary the format stored for it. Returns None for a format this
    module does not know. Each branch mirrors the end of that format's `update_participation`; the view checks
    every participation against what the format really stored, so a mismatch shows up as a warning instead of as
    a wrong board.
    """
    config = config or {}
    values = [cell for cell in cells.values() if cell]
    solved = [cell for cell in values if _points(cell)]

    if format_name in ('default', 'ioi', 'ioi16'):
        # Legacy IOI and IOI16 store a time of 0 when `cumtime` is off, so the same sum covers both settings.
        score = sum(_points(cell) for cell in values)
        cumtime = max(sum(cell.get('time') or 0 for cell in solved), 0)
        return score, cumtime, 0
    if format_name == 'icpc':
        minutes = config.get('penalty', 20)
        score = sum(_points(cell) for cell in values)
        cumtime = (sum(cell.get('time') or 0 for cell in solved) +
                   sum((cell.get('penalty') or 0) * minutes * 60 for cell in solved))
        tiebreaker = max([0] + [cell.get('time') or 0 for cell in solved])
        return score, cumtime, tiebreaker
    if format_name == 'atcoder':
        minutes = config.get('penalty', 5)
        score = sum(_points(cell) for cell in values)
        cumtime = (max([0] + [cell.get('time') or 0 for cell in solved]) +
                   sum((cell.get('penalty') or 0) * minutes * 60 for cell in solved))
        return score, cumtime, 0
    if format_name == 'ecoo':
        score = sum(_points(cell) + (cell.get('bonus') or 0) for cell in values)
        cumtime = sum(cell.get('time') or 0 for cell in values) if config.get('cumtime') else 0
        return score, cumtime, 0
    return None


def time_matters(format_name, config):
    if format_name in ('ioi', 'ioi16', 'ecoo'):
        return bool((config or {}).get('cumtime'))
    return True


def sort_key(totals, precision):
    """What the scoreboard orders by: more points, then less time, then the format's tiebreaker."""
    score, cumtime, tiebreaker = totals
    return -round(score or 0, precision), int(cumtime or 0), round(tiebreaker or 0, 6)


def totals_match(calculated, stored, precision):
    if calculated is None:
        return False
    return (math.isclose(round(calculated[0], precision), round(stored[0] or 0, precision), abs_tol=1e-9) and
            abs(int(calculated[1]) - int(stored[1] or 0)) <= 1 and
            math.isclose(calculated[2] or 0, stored[2] or 0, abs_tol=1e-3))


def ranks_for(order, keys):
    """Positions as the scoreboard numbers them: tied rows share the rank of the first of them."""
    ranks = []
    last = None
    for position, row in enumerate(order):
        if not ranks or keys[row] != last:
            ranks.append(position + 1)
        else:
            ranks.append(ranks[-1])
        last = keys[row]
    return ranks


class RevealRow:
    """One participation as the reveal sees it: frozen and final cells, and which of them hide something."""

    def __init__(self, index, frozen_cells, final_cells, frozen_totals, final_totals, pending_counts):
        self.index = index
        self.frozen_cells = frozen_cells
        self.final_cells = final_cells
        self.frozen_totals = frozen_totals
        self.final_totals = final_totals
        self.pending_counts = pending_counts
        # A cell hides something if it got submissions during the freeze, or if it changed anyway: a submission
        # sent just before the freeze and judged after it is not in the frozen copy either.
        self.pending = [column for column, count in enumerate(pending_counts)
                        if count or frozen_cells[column] != final_cells[column]]


def plan_reveal(rows, precision, intermediate_totals):
    """The whole ceremony, step by step.

    `intermediate_totals(row, cells)` gives the totals of a row with some of its cells uncovered. The first board is
    the frozen one and the last is the real one, with the totals exactly as the contest format stored them; only
    the boards in between are added up here.

    Returns the first order, its ranks and the list of steps. Rows are referred to by their index. A reveal step
    carries the row's totals after it under `totals`, as numbers.
    """
    totals = {row.index: row.frozen_totals for row in rows}
    cells = {row.index: list(row.frozen_cells) for row in rows}
    left = {row.index: list(row.pending) for row in rows}
    by_index = {row.index: row for row in rows}

    def keys():
        return {index: sort_key(value, precision) for index, value in totals.items()}

    current_keys = keys()
    order = sorted(by_index, key=lambda index: (current_keys[index], index))
    ranks = ranks_for(order, current_keys)
    first_order, first_ranks = list(order), list(ranks)

    finalized = set()
    steps = []
    while len(finalized) < len(order):
        position = max(i for i, index in enumerate(order) if index not in finalized)
        index = order[position]
        row = by_index[index]
        if left[index]:
            column = left[index].pop(0)
            cells[index][column] = row.final_cells[column]
            totals[index] = row.final_totals if not left[index] else intermediate_totals(row, cells[index])
            current_keys = keys()
            # A stable sort: a row that only ties the ones above it stays below them, as on a real board.
            new_order = sorted(order, key=lambda i: current_keys[i])
            new_ranks = ranks_for(new_order, current_keys)
            step = {'kind': 'reveal', 'row': index, 'column': column, 'totals': totals[index],
                    'from': position, 'to': new_order.index(index)}
            if new_order != order:
                step['order'] = new_order
            if new_ranks != ranks:
                step['ranks'] = new_ranks
            order, ranks = new_order, new_ranks
        else:
            finalized.add(index)
            step = {'kind': 'finalize', 'row': index, 'position': position}
        steps.append(step)
    return first_order, first_ranks, steps


def _time_text(seconds):
    return nice_repr(timedelta(seconds=max(int(seconds or 0), 0)), 'noday')


def cell_view(format_name, cell, max_points, show_time):
    """How one problem cell looks on the reveal board: a state for the colour and two short texts."""
    if not cell:
        return {'state': 'empty', 'main': '', 'sub': ''}
    points = _points(cell)
    if max_points and points >= max_points:
        state = 'full'
    elif points > 0:
        state = 'partial'
    else:
        state = 'failed'
    time = _time_text(cell.get('time')) if show_time else ''

    if format_name in TRIES_FORMATS:
        tries = int(cell.get('penalty') or 0)
        if state == 'full':
            main, sub = ('+%d' % tries if tries else '+'), time
        elif state == 'partial':
            main, sub = str(floatformat(points)), time
        else:
            main, sub = ('-%d' % tries if tries else '-'), ''
    else:
        main = str(floatformat(points))
        bonus = cell.get('bonus') or 0
        sub = '+%s' % floatformat(bonus) if bonus else time
    return {'state': state, 'main': main, 'sub': sub}


class RevealEntry:
    """What the reveal needs from one participation, already read from the database."""

    def __init__(self, id, name, username, organization, score, cumtime, tiebreaker, format_data,
                 frozen_at=None, frozen_score=None, frozen_cumtime=None, frozen_tiebreaker=None,
                 frozen_format_data=None, avatar=''):
        self.id = id
        self.avatar = avatar
        self.name = name
        self.username = username
        self.organization = organization
        self.score = score
        self.cumtime = cumtime
        self.tiebreaker = tiebreaker
        self.format_data = format_data
        self.frozen_at = frozen_at
        self.frozen_score = frozen_score
        self.frozen_cumtime = frozen_cumtime
        self.frozen_tiebreaker = frozen_tiebreaker
        self.frozen_format_data = frozen_format_data


class RevealProblem:
    def __init__(self, id, points, label, name):
        self.id = id
        self.points = points
        self.label = label
        self.name = name


def assemble_reveal(format_name, config, precision, problems, entries, pending, first_solves=None):
    """The data of the reveal page, from what was read. No database access, so the laboratory can run it.

    `pending` maps (participation id, contest problem id) to the submissions sent inside that participation's own
    freeze window. Returns the data and how many participations have stored totals that `format_totals` does not
    reproduce.
    """
    show_time = time_matters(format_name, config)
    first_solves = first_solves or {}

    def totals_of(cells_by_problem):
        return format_totals(format_name, config, cells_by_problem)

    def score_text(value):
        return str(floatformat(value or 0, -precision))

    def time_text(value):
        return _time_text(value) if show_time else ''

    rows, row_data, inexact = [], [], 0
    for index, entry in enumerate(entries):
        final_data = entry.format_data or {}
        has_copy = entry.frozen_at is not None
        frozen_data = (entry.frozen_format_data or {}) if has_copy else final_data
        final_totals = (entry.score, entry.cumtime, entry.tiebreaker)
        frozen_totals = ((entry.frozen_score, entry.frozen_cumtime, entry.frozen_tiebreaker)
                         if has_copy else final_totals)
        if not (totals_match(totals_of(frozen_data), frozen_totals, precision) and
                totals_match(totals_of(final_data), final_totals, precision)):
            inexact += 1

        frozen_cells = [frozen_data.get(str(problem.id)) for problem in problems]
        final_cells = [final_data.get(str(problem.id)) for problem in problems]
        counts = [pending.get((entry.id, problem.id), 0) for problem in problems]
        row = RevealRow(index, frozen_cells, final_cells, frozen_totals, final_totals, counts)
        rows.append(row)
        row_data.append({
            'name': entry.name,
            'username': entry.username,
            'organization': entry.organization or '',
            'avatar': entry.avatar or '',
            'score': score_text(frozen_totals[0]),
            'time': time_text(frozen_totals[1]),
            'finalScore': score_text(final_totals[0]),
            'finalTime': _time_text(final_totals[1]),
            'firstSolves': [column for column, problem in enumerate(problems)
                            if first_solves.get(problem.id) == entry.id],
            'frozen': [cell_view(format_name, cell, problem.points, show_time)
                       for cell, problem in zip(frozen_cells, problems)],
            'final': [cell_view(format_name, cell, problem.points, show_time)
                      for cell, problem in zip(final_cells, problems)],
            # -1 marks a cell with nothing to uncover; otherwise, how many submissions it is hiding.
            'pending': [counts[column] if column in row.pending else -1 for column in range(len(problems))],
        })

    def intermediate_totals(row, cells):
        calculated = totals_of({str(problem.id): cell for problem, cell in zip(problems, cells) if cell})
        # A format this module does not know keeps the frozen totals until the row is complete, where the real
        # ones take over. The page warns about it.
        return calculated if calculated is not None else row.frozen_totals

    order, ranks, steps = plan_reveal(rows, precision, intermediate_totals)
    # Some score formats can lose points. A finalized row can still change rank when another row falls;
    # congratulate it only after its last rank change, with all its own cells already revealed.
    award_order, award_ranks = order, ranks
    award_steps = [0] * len(rows)
    for number, step in enumerate(steps, 1):
        new_order = step.get('order', award_order)
        new_ranks = step.get('ranks', award_ranks)
        old_rank = dict(zip(award_order, award_ranks))
        for row, rank in zip(new_order, new_ranks):
            if old_rank[row] != rank:
                award_steps[row] = number
        if step['kind'] == 'finalize':
            award_steps[step['row']] = number
        award_order, award_ranks = new_order, new_ranks
    for position, (row, rank) in enumerate(zip(award_order, award_ranks), 1):
        row_data[row].update(finalRank=rank, finalPosition=position, awardStep=award_steps[row])
    for step in steps:
        if step['kind'] == 'reveal':
            # Text only: the browser never formats a number itself.
            totals = step.pop('totals')
            step['score'] = score_text(totals[0])
            step['time'] = time_text(totals[1])

    data = {
        'problems': [{'label': problem.label, 'name': problem.name} for problem in problems],
        'showTime': show_time,
        'rows': row_data,
        'order': order,
        'ranks': ranks,
        'steps': steps,
    }
    # Lets the page restore its position after an accidental reload, and only onto the same data.
    data['signature'] = hashlib.sha256(json.dumps(
        [data['problems'], row_data, order, steps], sort_keys=True).encode('utf-8')).hexdigest()[:16]
    return data, inexact


def first_to_solve(contest, participations):
    """First full, finally judged AC per problem, among eligible official participations.

    Submission time decides the winner, not judge completion time. The submission id makes identical
    timestamps deterministic. No virtual, spectator, disqualified, partial or pretest-only result qualifies.
    A correlated subquery returns at most one winner per contest problem instead of loading every AC.
    """
    accepted = ContestSubmission.objects.filter(
        participation_id__in=[p.id for p in participations], problem_id=OuterRef('pk'),
        submission__status='D', submission__result='AC', is_pretest=False,
        submission__is_pretested=False, points__gte=F('problem__points'), problem__points__gt=0,
        submission__date__gte=contest.start_time, submission__date__lt=contest.end_time,
    )
    if contest.time_limit is not None:
        accepted = accepted.filter(submission__date__gte=F('participation__real_start'),
                                   submission__date__lt=F('participation__real_start') + contest.time_limit)
    first = accepted.order_by('submission__date', 'submission_id').values('participation_id')[:1]
    winners = contest.contest_problems.annotate(first_participation=Subquery(first))
    return {problem: participant for problem, participant in winners.values_list('id', 'first_participation')
            if participant is not None}


def build_reveal(contest):
    """Everything the reveal page needs, read from the database. Reads only."""
    config = getattr(contest.format, 'config', None) or {}

    contest_problems = list(contest.contest_problems.select_related('problem').defer('problem__description')
                            .order_by('order'))
    problems = [RevealProblem(problem.id, problem.points, contest.get_label_for_problem(i), problem.problem.name)
                for i, problem in enumerate(contest_problems)]

    participations = list(
        contest.users.filter(virtual=ContestParticipation.LIVE, is_disqualified=False)
        .select_related('user__user').prefetch_related('user__organizations')
        .defer('user__about', 'user__organizations__about').order_by('id'),
    )

    starts = {}
    entries = []
    for participation in participations:
        # Reusing the contest already in hand; `participation.contest` would be one query per row.
        participation.contest = contest
        start = participation.freeze_starts_at
        if start is not None:
            starts[participation.id] = start
        profile = participation.user
        organization = profile.organization
        entries.append(RevealEntry(
            id=participation.id, name=profile.display_name, username=profile.username,
            organization=organization.short_name if organization else '',
            score=participation.score, cumtime=participation.cumtime, tiebreaker=participation.tiebreaker,
            format_data=participation.format_data, frozen_at=participation.frozen_at,
            frozen_score=participation.frozen_score, frozen_cumtime=participation.frozen_cumtime,
            frozen_tiebreaker=participation.frozen_tiebreaker,
            frozen_format_data=participation.frozen_format_data,
            # The same picture the site shows on the profile and in the navigation bar.
            avatar=gravatar(profile, 256),
        ))

    pending = {}
    if starts:
        submissions = ContestSubmission.objects.filter(
            participation_id__in=starts, submission__date__gte=min(starts.values()),
        ).values_list('participation_id', 'problem_id', 'submission__date')
        for participation_id, problem_id, date in submissions:
            if date >= starts[participation_id]:
                pending[participation_id, problem_id] = pending.get((participation_id, problem_id), 0) + 1

    data, inexact = assemble_reveal(contest.format_name, config, contest.points_precision, problems, entries,
                                    pending, first_to_solve(contest, participations))

    in_progress = Submission.objects.filter(contest_object=contest,
                                            status__in=Submission.IN_PROGRESS_GRADING_STATUS).count()

    freeze_configured = contest.freeze_minutes is not None
    notices = []
    if not freeze_configured:
        notices.append(('error', _('This contest has no scoreboard freeze configured, so there is nothing to '
                                   'reveal.')))
    elif not contest.freeze_started:
        notices.append(('warning', _('The freeze has not started yet: nothing is hidden so far.')))
    if freeze_configured and contest.end_time > contest._now:
        notices.append(('warning', _('The contest has not ended. Whatever is revealed now can still change.')))
    if in_progress:
        notices.append(('warning', ngettext('%d submission is still being judged. Wait for it before revealing.',
                                            '%d submissions are still being judged. Wait for them before revealing.',
                                            in_progress) % in_progress))
    if inexact:
        notices.append(('warning', ngettext(
            'The totals of %d participant do not add up the way this contest format computes them, so the totals '
            'shown between the first and the last step may be approximate. The frozen and the final scoreboard '
            'are exact.',
            'The totals of %d participants do not add up the way this contest format computes them, so the totals '
            'shown between the first and the last step may be approximate. The frozen and the final scoreboard '
            'are exact.', inexact) % inexact))
    if freeze_configured and contest.scoreboard_revealed:
        notices.append(('info', _('The scoreboard is already public. This is a replay.')))
    if not entries:
        notices.append(('info', _('Nobody took part in this contest.')))

    data.update({
        'contest': contest.key,
        'published': bool(freeze_configured and contest.scoreboard_revealed),
        'canPublish': freeze_configured,
        'publishUrl': reverse('contest_reveal_publish', args=[contest.key]),
    })
    return data, notices, inexact


def reveal_labels():
    """The texts the page's script shows, translated here so the script holds no language of its own."""
    return {
        'autoplay': _('Auto-play'),
        'pause': _('Pause'),
        'restartConfirm': _('Restart the reveal from the beginning?'),
        'stepOf': _('Step %(current)s of %(total)s'),
        'cellsLeft': _('cells left to reveal'),
        'start': _('Press Space or the right arrow to start.'),
        'frozen': _('Frozen scoreboard'),
        'final': _('Final scoreboard'),
        'firstPlace': _('First place'),
        'place': _('Place %(rank)s'),
        'gold': _('Gold medal'),
        'silver': _('Silver medal'),
        'bronze': _('Bronze medal'),
        'firstSolve': _('First to solve: %(problem)s'),
        'points': _('Points'),
        'totalTime': _('Total time'),
        'noAwards': _('No awards for these settings.'),
        'awardsSaved': _('Award settings saved for this contest in this browser.'),
        'invalidAwards': _('Use whole-number places from 1 to 10000, with each start at or before its end and no '
                           'overlapping ranges.'),
        'publishConfirm': _('Publish the real scoreboard to everyone? This lifts the freeze on the site.'),
        'published': _('Scoreboard published.'),
        'alreadyPublished': _('The scoreboard is already public.'),
        'publishFailed': _('Could not publish the scoreboard. Try again or use the administration.'),
    }


def reveal_context(contest, data, notices):
    data = dict(data, labels=reveal_labels())
    return {
        'contest': contest,
        'title': _('Scoreboard reveal'),
        'notices': notices,
        'reveal_data': json_script(data, 'reveal-data'),
        'show_time': data['showTime'],
        'problem_count': len(data['problems']),
    }


@login_required
def contest_reveal(request, contest):
    contest = get_object_or_404(Contest, key=contest)
    # Only whoever sees the real scoreboard may reveal it; everybody else is told the page does not exist.
    if not contest.is_editable_by(request.user):
        raise Http404()

    data, notices, _inexact = build_reveal(contest)
    return render(request, 'contest/reveal.html', reveal_context(contest, data, notices))


@require_POST
@login_required
def contest_reveal_publish(request, contest):
    contest = get_object_or_404(Contest, key=contest)
    if not contest.is_editable_by(request.user):
        raise Http404()

    # The same update as the administration's "Reveal frozen scoreboards" action, for this one contest.
    updated = Contest.objects.filter(id=contest.id, freeze_minutes__isnull=False,
                                     scoreboard_revealed=False).update(scoreboard_revealed=True)
    cache.delete(Contest.FROZEN_WINDOWS_CACHE_KEY)
    already = contest.freeze_minutes is not None and contest.scoreboard_revealed
    return JsonResponse({'published': bool(updated), 'public': bool(updated) or already})

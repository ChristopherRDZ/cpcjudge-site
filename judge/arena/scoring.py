"""Who is winning an arena game, and whether the game can already be called. Pure functions, no database.

A player's score is the sum of their best points on each problem. Ties on points are broken by the moment the
player reached their score, which is the date of the submission that first got each problem to its best, taken
over all problems; the submission date counts, not the moment it was judged. With a penalty configured, every
failed attempt made before a player's best submission on a problem that gave points adds that many minutes to the
moment. A failed attempt on a problem that never gave points costs nothing.

A game ends as soon as one player is certain to win: their score as it stands, counting every pending submission
of theirs as failed, beats the best the rival could still do, counting every pending submission of the rival as a
full solve on the date it was sent and, while there is time and attempts left, a full solve right now of every
problem the rival has not finished. When time is up nobody can submit any more, so only the pending submissions
are left to decide; while one of them can still change the outcome the game waits for its verdict.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

PENDING = ('QU', 'P', 'G')
# Neither of these is the player's doing, so they cost nothing: not an attempt, not a penalty.
NOT_ATTEMPTS = ('CE', 'IE')
EPSILON = 1e-9

# Outcomes of decide().
COUNTDOWN, LIVE, VERIFYING, FINISH, TIE = 'countdown', 'live', 'verifying', 'finish', 'tie'
# Reasons recorded with a finished game.
ALL_SOLVED, UNREACHABLE, MORE_POINTS, EARLIER = 'all_solved', 'unreachable', 'more_points', 'earlier'


@dataclass(frozen=True)
class Attempt:
    id: int
    date: datetime
    status: str
    result: Optional[str]
    case_points: float
    case_total: float
    problem: int

    @property
    def pending(self):
        return self.status in PENDING

    @property
    def counts(self):
        """Whether it uses up one of the attempts allowed on the problem."""
        return self.status not in NOT_ATTEMPTS

    @property
    def penalizable(self):
        return not self.pending and self.status not in NOT_ATTEMPTS


def points_of(attempt, full, partial):
    """Points a judged attempt earns on a problem worth `full`. Pending and failed attempts earn nothing."""
    if attempt.status != 'D' or attempt.case_total <= 0:
        return 0.0
    if attempt.result == 'AC' and attempt.case_points >= attempt.case_total:
        return float(full)
    if partial:
        return min(float(full), round(attempt.case_points / attempt.case_total * full, 3))
    return 0.0


@dataclass(frozen=True)
class _Event:
    problem: int
    date: datetime
    order: tuple
    points: float
    penalizable: bool


@dataclass
class ProblemStanding:
    best: float = 0.0
    full: bool = False
    attempts: int = 0
    pending: int = 0
    penalized: int = 0
    best_at: Optional[datetime] = None
    solved_at: Optional[datetime] = None


@dataclass
class Standing:
    score: float = 0.0
    reach: Optional[datetime] = None
    penalized: int = 0
    effective: Optional[float] = None
    problems: Dict[int, ProblemStanding] = field(default_factory=dict)


def _standing(events, problems, penalty, started_at):
    standing = Standing()
    by_problem = {key: [] for key in problems}
    for event in events:
        if event.problem in by_problem:
            by_problem[event.problem].append(event)
    total = 0.0
    for key, full in problems.items():
        row = ProblemStanding()
        ordered = sorted(by_problem[key], key=lambda event: event.order)
        best = max((event.points for event in ordered), default=0.0)
        if best > EPSILON:
            index = next(i for i, event in enumerate(ordered) if event.points >= best - EPSILON)
            row.best = best
            row.best_at = ordered[index].date
            row.penalized = sum(1 for event in ordered[:index] if event.penalizable)
            row.full = best >= full - EPSILON
            if row.full:
                row.solved_at = row.best_at
            standing.penalized += row.penalized
            standing.reach = row.best_at if standing.reach is None else max(standing.reach, row.best_at)
            total += best
        standing.problems[key] = row
    standing.score = round(total, 3)
    if standing.reach is not None:
        standing.effective = (standing.reach - started_at).total_seconds() + penalty * 60 * standing.penalized
    return standing


def _judged_events(attempts, problems, partial):
    return [_Event(a.problem, a.date, (a.date, a.id), points_of(a, problems[a.problem], partial), a.penalizable)
            for a in attempts if not a.pending and a.problem in problems]


def _annotate(standing, attempts):
    for attempt in attempts:
        row = standing.problems.get(attempt.problem)
        if row is None:
            continue
        if attempt.counts:
            row.attempts += 1
        if attempt.pending:
            row.pending += 1
    return standing


def confirmed(attempts, problems, partial, penalty, started_at):
    """The standing on judged submissions only, with attempt and pending counts per problem."""
    standing = _standing(_judged_events(attempts, problems, partial), problems, penalty, started_at)
    return _annotate(standing, attempts)


def worst_case(attempts, problems, partial, penalty, started_at):
    """Every pending submission fails: it earns nothing, and counts as a failed attempt if it is earlier than the
    best submission on its problem."""
    events = _judged_events(attempts, problems, partial)
    events += [_Event(a.problem, a.date, (a.date, a.id), 0.0, True)
               for a in attempts if a.pending and a.problem in problems]
    return _standing(events, problems, penalty, started_at)


def attempts_left(attempts, problem, max_attempts):
    if not max_attempts:
        return None
    used = sum(1 for a in attempts if a.problem == problem and a.counts)
    return max(0, max_attempts - used)


def best_case(attempts, problems, partial, penalty, started_at, future_at=None, max_attempts=0):
    """Every pending submission is a full solve on the date it was sent, and, if `future_at` is given, every problem
    not yet solved in full gets a full solve at `future_at` as long as attempts are left on it."""
    events = _judged_events(attempts, problems, partial)
    events += [_Event(a.problem, a.date, (a.date, a.id), float(problems[a.problem]), False)
               for a in attempts if a.pending and a.problem in problems]
    if future_at is not None:
        for key, full in problems.items():
            done = any(event.problem == key and event.points >= full - EPSILON for event in events)
            left = attempts_left(attempts, key, max_attempts)
            if not done and (left is None or left > 0):
                events.append(_Event(key, future_at, (future_at, float('inf')), float(full), False))
    return _standing(events, problems, penalty, started_at)


def beats(first, second):
    """Whether standing `first` ranks strictly above standing `second`."""
    if first.score > second.score + EPSILON:
        return True
    if abs(first.score - second.score) <= EPSILON and first.score > EPSILON:
        return first.effective < second.effective - EPSILON
    return False


@dataclass
class Decision:
    outcome: str
    winner: Optional[str] = None
    reason: str = ''


def decide(attempts_a, attempts_b, problems, rules, started_at, ends_at, now, closed=False):
    """What a game is: still in its countdown, live, waiting for verdicts, won by one player, or tied.

    `attempts_a` and `attempts_b` are the `Attempt`s of each player; `problems` maps each problem key to its points;
    `closed` says submissions were closed before `ends_at`.
    """
    if now < started_at:
        return Decision(COUNTDOWN)
    partial = bool(rules.get('partial'))
    penalty = int(rules.get('penalty') or 0)
    max_attempts = int(rules.get('max_attempts') or 0)
    open_future = not closed and now < ends_at
    future_at = now if open_future else None
    total = round(sum(problems.values()), 3)

    sides = {'a': attempts_a, 'b': attempts_b}
    worst = {slot: worst_case(sides[slot], problems, partial, penalty, started_at) for slot in sides}
    best = {slot: best_case(sides[slot], problems, partial, penalty, started_at, future_at, max_attempts)
            for slot in sides}
    for leader, rival in (('a', 'b'), ('b', 'a')):
        if beats(worst[leader], best[rival]):
            if open_future:
                reason = ALL_SOLVED if worst[leader].score >= total - EPSILON else UNREACHABLE
            elif worst[leader].score > best[rival].score + EPSILON:
                reason = MORE_POINTS
            else:
                reason = EARLIER
            return Decision(FINISH, leader, reason)
    if open_future:
        return Decision(LIVE)
    if any(a.pending for a in attempts_a) or any(a.pending for a in attempts_b):
        return Decision(VERIFYING)
    return Decision(TIE)


def decide_ignoring_pending(attempts_a, attempts_b, problems, rules, started_at, ends_at, now):
    """Final call by the organizer while verdicts are stuck: whatever is still pending counts as failed."""
    judged_a = [a for a in attempts_a if not a.pending]
    judged_b = [a for a in attempts_b if not a.pending]
    return decide(judged_a, judged_b, problems, rules, started_at, ends_at, max(now, ends_at), closed=True)


def summary(standing, started_at, problems_order):
    """JSON-friendly view of a standing: seconds from the start instead of dates."""
    def seconds(moment):
        return None if moment is None else round((moment - started_at).total_seconds(), 3)
    return {
        'score': standing.score,
        'effective': None if standing.effective is None else round(standing.effective, 3),
        'reach': seconds(standing.reach),
        'penalized': standing.penalized,
        'problems': {str(key): {
            'best': row.best,
            'full': row.full,
            'attempts': row.attempts,
            'pending': row.pending,
            'penalized': row.penalized,
            'solved_at': seconds(row.solved_at),
            'best_at': seconds(row.best_at),
        } for key, row in ((key, standing.problems[key]) for key in problems_order if key in standing.problems)},
    }


def attempts_from_rows(rows, problem_key):
    """`Attempt`s from Submission rows of values(); `problem_key` maps a row to its problem key or None."""
    attempts = []
    for row in rows:
        key = problem_key(row)
        if key is None:
            continue
        attempts.append(Attempt(id=row['id'], date=row['date'], status=row['status'], result=row['result'],
                                case_points=row['case_points'] or 0.0, case_total=row['case_total'] or 0.0,
                                problem=key))
    return attempts

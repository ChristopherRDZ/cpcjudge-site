"""The decision rule of an arena game, as a table of cases. Pure functions: no database."""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.test import SimpleTestCase

from judge.arena import scoring
from judge.arena.scoring import Attempt

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=dt_timezone.utc)
ENDS = T0 + timedelta(minutes=15)
ONE = {0: 100}
TWO = {0: 100, 1: 100}
RULES = {'partial': False, 'penalty': 0, 'max_attempts': 0}


def at(minutes):
    return T0 + timedelta(minutes=minutes)


_ids = iter(range(1, 10 ** 6))


def ac(minute, problem=0):
    return Attempt(next(_ids), at(minute), 'D', 'AC', 1, 1, problem)


def wa(minute, problem=0, case_points=0):
    return Attempt(next(_ids), at(minute), 'D', 'WA', case_points, 1, problem)


def pending(minute, problem=0):
    return Attempt(next(_ids), at(minute), 'G', None, 0, 0, problem)


def ce(minute, problem=0):
    return Attempt(next(_ids), at(minute), 'CE', 'CE', 0, 0, problem)


def ie(minute, problem=0):
    return Attempt(next(_ids), at(minute), 'IE', 'IE', 0, 0, problem)


def ab(minute, problem=0):
    return Attempt(next(_ids), at(minute), 'AB', 'AB', 0, 0, problem)


def decide(a, b, problems=ONE, now=6, rules=None, closed=False):
    return scoring.decide(a, b, problems, dict(RULES, **(rules or {})), T0, ENDS, at(now), closed=closed)


class PointsTests(SimpleTestCase):
    def test_full_ac(self):
        self.assertEqual(scoring.points_of(ac(1), 100, False), 100)

    def test_partial_only_with_partial_rules(self):
        partial = Attempt(1, at(1), 'D', 'WA', 2, 5, 0)
        self.assertEqual(scoring.points_of(partial, 100, True), 40)
        self.assertEqual(scoring.points_of(partial, 100, False), 0)

    def test_pending_and_errors_earn_nothing(self):
        for attempt in (pending(1), ce(1), ie(1), ab(1)):
            self.assertEqual(scoring.points_of(attempt, 100, True), 0)


class DecisionTests(SimpleTestCase):
    def test_countdown_before_start(self):
        self.assertEqual(scoring.decide([], [], ONE, RULES, T0, ENDS, T0 - timedelta(seconds=1)).outcome,
                         scoring.COUNTDOWN)

    def test_nothing_yet_is_live(self):
        self.assertEqual(decide([], []).outcome, scoring.LIVE)

    def test_first_full_solve_wins_at_once(self):
        result = decide([ac(5)], [])
        self.assertEqual((result.outcome, result.winner, result.reason), (scoring.FINISH, 'a', scoring.ALL_SOLVED))

    def test_rival_pending_sent_earlier_keeps_the_game_open(self):
        # The rival sent before the leader's accepted solution: if it is accepted, the rival got there first.
        self.assertEqual(decide([ac(5)], [pending(3)]).outcome, scoring.LIVE)

    def test_rival_pending_sent_later_does_not(self):
        result = decide([ac(5)], [pending(5.5)])
        self.assertEqual((result.outcome, result.winner), (scoring.FINISH, 'a'))

    def test_the_earlier_pending_decides_once_judged(self):
        self.assertEqual(decide([ac(5)], [ac(3)]).winner, 'b')
        self.assertEqual(decide([ac(5)], [wa(3)]).winner, 'a')

    def test_one_of_two_problems_is_not_enough(self):
        self.assertEqual(decide([ac(2)], [], problems=TWO).outcome, scoring.LIVE)

    def test_all_of_two_problems_wins(self):
        result = decide([ac(2), ac(4, 1)], [ac(1)], problems=TWO, now=5)
        self.assertEqual((result.outcome, result.winner, result.reason), (scoring.FINISH, 'a', scoring.ALL_SOLVED))

    def test_time_up_more_points(self):
        result = decide([ac(2)], [], problems=TWO, now=16)
        self.assertEqual((result.outcome, result.winner, result.reason), (scoring.FINISH, 'a', scoring.MORE_POINTS))

    def test_time_up_same_points_earlier_wins(self):
        result = decide([ac(4)], [ac(2, 1)], problems=TWO, now=16)
        self.assertEqual((result.outcome, result.winner, result.reason), (scoring.FINISH, 'b', scoring.EARLIER))

    def test_time_up_nothing_is_a_tie(self):
        self.assertEqual(decide([wa(2)], [wa(3)], now=16).outcome, scoring.TIE)

    def test_time_up_identical_moment_is_a_tie(self):
        a = Attempt(1, at(4), 'D', 'AC', 1, 1, 0)
        b = Attempt(2, at(4), 'D', 'AC', 1, 1, 1)
        self.assertEqual(decide([a], [b], problems=TWO, now=16).outcome, scoring.TIE)

    def test_time_up_waits_for_a_pending_that_matters(self):
        self.assertEqual(decide([ac(4)], [pending(2, 1)], problems=TWO, now=16).outcome, scoring.VERIFYING)

    def test_time_up_does_not_wait_for_a_pending_that_cannot_change_anything(self):
        result = decide([ac(4), ac(5, 1)], [pending(6)], problems=TWO, now=16)
        self.assertEqual((result.outcome, result.winner), (scoring.FINISH, 'a'))

    def test_closed_early_counts_as_time_up(self):
        self.assertEqual(decide([ac(4)], [], problems=TWO, now=6, closed=True).winner, 'a')

    def test_ignoring_pending_for_the_organizer(self):
        result = scoring.decide_ignoring_pending([ac(4)], [pending(2, 1)], TWO, RULES, T0, ENDS, at(16))
        self.assertEqual((result.outcome, result.winner), (scoring.FINISH, 'a'))


class PenaltyTests(SimpleTestCase):
    def test_penalty_can_keep_a_finished_player_from_winning_yet(self):
        # A solved everything with three failed attempts: 30 minutes of penalty. B can still solve both now and
        # come out ahead, so the game goes on.
        a = [wa(1), wa(2), wa(3), ac(4), ac(5, 1)]
        self.assertEqual(decide(a, [], problems=TWO, now=6, rules={'penalty': 10}).outcome, scoring.LIVE)

    def test_without_penalty_the_same_game_is_over(self):
        a = [wa(1), wa(2), wa(3), ac(4), ac(5, 1)]
        self.assertEqual(decide(a, [], problems=TWO, now=6).winner, 'a')

    def test_penalty_breaks_ties_at_time_up(self):
        a = [wa(1), ac(4)]
        b = [ac(6, 1)]
        self.assertEqual(decide(a, b, problems=TWO, now=16).winner, 'a')
        self.assertEqual(decide(a, b, problems=TWO, now=16, rules={'penalty': 5}).winner, 'b')

    def test_failures_on_a_problem_never_solved_cost_nothing(self):
        standing = scoring.confirmed([wa(1, 1), wa(2, 1), ac(3)], TWO, False, 10, T0)
        self.assertEqual(standing.penalized, 0)

    def test_compile_and_internal_errors_cost_nothing(self):
        standing = scoring.confirmed([ce(1), ie(2), ac(3)], ONE, False, 10, T0)
        self.assertEqual(standing.penalized, 0)
        self.assertEqual(standing.problems[0].attempts, 1)

    def test_an_abort_is_a_failed_attempt(self):
        self.assertEqual(scoring.confirmed([ab(1), ac(3)], ONE, False, 10, T0).penalized, 1)

    def test_a_pending_earlier_than_the_best_counts_against_the_leader(self):
        worst = scoring.worst_case([pending(1), ac(3)], ONE, False, 10, T0)
        self.assertEqual(worst.penalized, 1)
        self.assertEqual(scoring.confirmed([pending(1), ac(3)], ONE, False, 10, T0).penalized, 0)


class AttemptLimitTests(SimpleTestCase):
    def test_a_rival_out_of_attempts_cannot_catch_up(self):
        a = [ac(2)]
        b = [wa(1), wa(1.5)]
        result = decide(a, b, problems=TWO, now=3, rules={'max_attempts': 2})
        self.assertEqual((result.outcome, result.winner, result.reason), (scoring.FINISH, 'a', scoring.UNREACHABLE))

    def test_with_attempts_left_the_game_goes_on(self):
        self.assertEqual(decide([ac(2)], [wa(1)], problems=TWO, now=3, rules={'max_attempts': 2}).outcome,
                         scoring.LIVE)

    def test_compile_errors_do_not_use_attempts(self):
        self.assertEqual(scoring.attempts_left([ce(1), ce(2), wa(3)], 0, 2), 1)


class PartialTests(SimpleTestCase):
    def test_partial_points_decide_at_time_up(self):
        a = [Attempt(1, at(3), 'D', 'WA', 2, 5, 0)]
        result = decide(a, [], now=16, rules={'partial': True})
        self.assertEqual((result.outcome, result.winner, result.reason), (scoring.FINISH, 'a', scoring.MORE_POINTS))
        self.assertEqual(decide(a, [], now=16).outcome, scoring.TIE)

    def test_partial_best_moment_is_the_first_reaching_the_best(self):
        attempts = [Attempt(1, at(3), 'D', 'WA', 2, 5, 0), Attempt(2, at(4), 'D', 'WA', 2, 5, 0)]
        standing = scoring.confirmed(attempts, ONE, True, 0, T0)
        self.assertEqual((standing.score, standing.reach), (40, at(3)))

    def test_summary_is_json_friendly(self):
        summary = scoring.summary(scoring.confirmed([ac(2)], ONE, False, 0, T0), T0, [0])
        self.assertEqual(summary['problems']['0']['solved_at'], 120)
        self.assertEqual(summary['score'], 100)

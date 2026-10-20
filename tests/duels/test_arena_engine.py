"""The arena engine end to end against the database: battles, series, ties, tournaments and their guards.

Judging is replaced by a no-op and the bridge is simulated the way it really works: the submission row gets its
verdict and then `update_contest()` runs, which recomputes the participation with the contest format.
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from arena_testutil import backdate, duel, grade, organizer, online_judge, player, problem, skip_countdown, start, \
    submit, tick, time_up
from judge.arena import engine, tournament as arena_tournament
from judge.arena.engine import ArenaError, Conflict
from judge.models import Contest, ContestParticipation, ContestSubmission, Submission
from judge.models.arena import ArenaAction, ArenaGame, ArenaMatch, ArenaTournament


class EngineTestCase(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer()
        cls.alice = player('alice')
        cls.bob = player('bob')
        cls.carol = player('carol')
        cls.p1 = problem('arenaone', points=1)
        cls.p2 = problem('arenatwo', points=1)
        cls.p3 = problem('arenathree', points=1)
        cls.p4 = problem('arenafour', points=1)
        cls.p5 = problem('arenafive', points=1)
        online_judge(cls.p1, cls.p2, cls.p3, cls.p4, cls.p5)

    def setUp(self):
        cache.clear()

    def live_game(self, problems=None, **kwargs):
        match = duel(self.organizer, self.alice, self.bob, problems or [self.p1], **kwargs)
        game = start(self.organizer, match)
        # Two minutes in, so submissions can be dated anywhere in the first two minutes and stay in the past.
        return match, skip_countdown(game, seconds_ago=120)


class LifecycleTests(EngineTestCase):
    def test_quick_battle_from_call_to_result(self):
        match = duel(self.organizer, self.alice, self.bob, [self.p1])
        self.assertEqual(match.status, ArenaMatch.CALLED)
        game = match.games.get()
        self.assertEqual(game.status, ArenaGame.PREPARED)
        self.assertIsNone(game.contest_id)

        outcome = engine.start_game(self.organizer.profile, match.pk)
        self.assertFalse(outcome['started'], 'nobody has the arena open, so it warns first')
        self.assertTrue(any(w['code'] == 'absent' for w in outcome['warnings']))

        game = start(self.organizer, match)
        self.assertEqual(game.status, ArenaGame.COUNTDOWN)
        contest = game.contest
        self.assertFalse(contest.is_visible)
        self.assertFalse(contest.is_rated)
        self.assertEqual(contest.scoreboard_visibility, Contest.SCOREBOARD_HIDDEN)
        self.assertEqual(contest.start_time, game.started_at)
        self.assertEqual(ContestParticipation.objects.filter(contest=contest, virtual=0).count(), 2)
        self.assertEqual(tick(game).status, ArenaGame.COUNTDOWN)

        game = skip_countdown(game)
        self.assertEqual(tick(game).status, ArenaGame.LIVE)

        wrong = submit(self.bob, game)
        grade(wrong, 'WA', 0, 1)
        self.assertEqual(tick(game).status, ArenaGame.LIVE)
        right = submit(self.alice, game)
        self.assertEqual(ContestSubmission.objects.get(submission=right).participation, game.participation_a)
        self.assertEqual(right.contest_object_id, contest.pk)
        grade(right)
        game = tick(game)
        self.assertEqual((game.status, game.winner_slot, game.reason), (ArenaGame.FINISHED, 'a', 'all_solved'))
        match.refresh_from_db()
        self.assertEqual((match.status, match.winner_slot, match.wins_a), (ArenaMatch.DECIDED, 'a', 1))
        contest.refresh_from_db()
        self.assertLessEqual(contest.end_time, timezone.now())
        self.assertEqual(game.result['a']['score'], 100)

    def test_the_contest_format_agrees_with_the_arena(self):
        match, game = self.live_game([self.p1, self.p2], rules={'partial': True})
        grade(submit(self.alice, game, 0), 'WA', 1, 4)
        grade(submit(self.alice, game, 1), 'AC', 1, 1)
        standing = engine.standings(game)['a']
        game.participation_a.refresh_from_db()
        self.assertEqual(standing.score, 125)
        self.assertEqual(game.participation_a.score, standing.score)

    def test_ticking_twice_records_once(self):
        match, game = self.live_game()
        grade(submit(self.alice, game))
        tick(game)
        tick(game)
        engine.tick(ArenaGame.objects.get(pk=game.pk))
        match.refresh_from_db()
        self.assertEqual(match.wins_a, 1)
        self.assertEqual(ArenaAction.objects.filter(match=match, action='finish').count(), 1)

    def test_starting_twice_is_refused(self):
        match = duel(self.organizer, self.alice, self.bob, [self.p1])
        start(self.organizer, match)
        with self.assertRaises(ArenaError):
            engine.start_game(self.organizer.profile, match.pk, force=True)

    def test_stale_version_is_a_conflict(self):
        match = duel(self.organizer, self.alice, self.bob, [self.p1])
        with self.assertRaises(Conflict):
            engine.start_game(self.organizer.profile, match.pk, force=True, version=match.version + 5)

    def test_earlier_pending_rival_waits_then_wins(self):
        match, game = self.live_game()
        early = submit(self.bob, game)
        late = submit(self.alice, game)
        backdate(early, 10, game)
        backdate(late, 20, game)
        grade(late)
        self.assertEqual(tick(game).status, ArenaGame.LIVE, 'Bob sent first: his verdict decides')
        grade(early)
        game = tick(game)
        self.assertEqual((game.status, game.winner_slot), (ArenaGame.FINISHED, 'b'))

    def test_time_up_with_pending_verifies_then_decides(self):
        match, game = self.live_game([self.p1, self.p2])
        right = submit(self.alice, game, 0)
        pending = submit(self.bob, game, 1)
        # Bob sent before Alice's solution: if his is accepted he reached the same points first.
        backdate(pending, 10, game)
        backdate(right, 20, game)
        grade(right)
        game = time_up(game)
        game = tick(game)
        self.assertEqual(game.status, ArenaGame.VERIFYING)
        self.assertIsNotNone(game.closed_at)
        grade(pending, 'WA', 0, 1)
        game = tick(game)
        self.assertEqual((game.status, game.winner_slot, game.reason), (ArenaGame.FINISHED, 'a', 'more_points'))

    def test_organizer_can_stop_waiting(self):
        match, game = self.live_game([self.p1, self.p2])
        right = submit(self.alice, game, 0)
        pending = submit(self.bob, game, 1)
        backdate(pending, 10, game)
        backdate(right, 20, game)
        grade(right)
        game = tick(time_up(game))
        self.assertEqual(game.status, ArenaGame.VERIFYING)
        engine.finalize_verification(self.organizer.profile, match.pk, game.pk)
        game.refresh_from_db()
        self.assertEqual((game.status, game.winner_slot), (ArenaGame.FINISHED, 'a'))

    def test_tie_then_organizer_decides(self):
        match, game = self.live_game()
        game = tick(time_up(game))
        self.assertEqual(game.status, ArenaGame.TIE)
        self.assertTrue(match.games.filter(status=ArenaGame.PREPARED).exists(), 'a tiebreak game is prepared')
        engine.declare_winner(self.organizer.profile, match.pk, game.pk, 'b')
        match.refresh_from_db()
        self.assertEqual((match.status, match.winner_slot), (ArenaMatch.DECIDED, 'b'))
        self.assertFalse(match.games.filter(status=ArenaGame.PREPARED).exists(), 'unplayed games are cleaned up')

    def test_tie_then_tiebreak_game(self):
        match, game = self.live_game()
        tick(time_up(game))
        tiebreak = match.games.get(status=ArenaGame.PREPARED)
        engine.set_game_problems(self.organizer.profile, match.pk, tiebreak.pk, [{'id': self.p2.id, 'points': 100}])
        second = skip_countdown(start(self.organizer, match))
        self.assertEqual(second.number, 2)
        grade(submit(self.bob, second))
        tick(second)
        match.refresh_from_db()
        self.assertEqual((match.status, match.winner_slot, match.wins_b, match.wins_a), (ArenaMatch.DECIDED, 'b', 1, 0))

    def test_best_of_three(self):
        games = [[{'id': self.p1.id}], [{'id': self.p2.id}], [{'id': self.p3.id}]]
        match = duel(self.organizer, self.alice, self.bob, [], best_of=3, games=games)
        self.assertEqual(match.games.count(), 3)
        winners = ['a', 'b', 'a']
        for number, winner in enumerate(winners, 1):
            game = skip_countdown(start(self.organizer, match))
            self.assertEqual(game.number, number)
            grade(submit(self.alice if winner == 'a' else self.bob, game))
            tick(game)
        match.refresh_from_db()
        self.assertEqual((match.status, match.winner_slot, match.wins_a, match.wins_b), (ArenaMatch.DECIDED, 'a', 2, 1))

    def test_series_decided_early_cleans_prepared_games(self):
        games = [[{'id': self.p1.id}], [{'id': self.p2.id}], [{'id': self.p3.id}]]
        match = duel(self.organizer, self.alice, self.bob, [], best_of=3, games=games)
        for _ in range(2):
            game = skip_countdown(start(self.organizer, match))
            grade(submit(self.alice, game))
            tick(game)
        match.refresh_from_db()
        self.assertEqual(match.status, ArenaMatch.DECIDED)
        self.assertEqual(match.games.count(), 2)

    def test_the_same_problem_twice_in_a_series_is_refused(self):
        with self.assertRaises(ArenaError):
            duel(self.organizer, self.alice, self.bob, [], best_of=3,
                 games=[[{'id': self.p1.id}], [{'id': self.p1.id}]])

    def test_walkover_cancel_and_extend(self):
        match, game = self.live_game()
        before = game.ends_at
        engine.extend_game(self.organizer.profile, match.pk, game.pk, 5)
        game.refresh_from_db()
        self.assertEqual(game.ends_at - before, timedelta(minutes=5))
        self.assertEqual(Contest.objects.get(pk=game.contest_id).end_time, game.ends_at)
        engine.cancel_game(self.organizer.profile, match.pk, game.pk)
        game.refresh_from_db()
        self.assertEqual(game.status, ArenaGame.CANCELLED)
        self.assertTrue(match.games.filter(status=ArenaGame.PREPARED).exists())
        engine.walkover(self.organizer.profile, match.pk, 'b')
        match.refresh_from_db()
        self.assertEqual((match.status, match.winner_slot, match.decided_reason), (ArenaMatch.DECIDED, 'b', 'walkover'))

    def test_cancel_quick_battle(self):
        match, game = self.live_game()
        engine.cancel_match(self.organizer.profile, match.pk)
        match.refresh_from_db()
        game.refresh_from_db()
        self.assertEqual((match.status, game.status), (ArenaMatch.CANCELLED, ArenaGame.CANCELLED))

    def test_penalty_rule_changes_the_winner(self):
        match, game = self.live_game([self.p1, self.p2], rules={'penalty': 10})
        alice_wrong = submit(self.alice, game, 0)
        alice_right = submit(self.alice, game, 0)
        bob_right = submit(self.bob, game, 1)
        backdate(alice_wrong, 10, game)
        backdate(alice_right, 20, game)
        backdate(bob_right, 30, game)
        grade(alice_wrong, 'WA', 0, 1)
        grade(alice_right)
        grade(bob_right)
        game = tick(time_up(game))
        self.assertEqual((game.status, game.winner_slot), (ArenaGame.FINISHED, 'b'))


class SubmitGuardTests(EngineTestCase):
    def test_nobody_else_can_submit(self):
        match, game = self.live_game()
        with self.assertRaises(ArenaError) as error:
            submit(self.carol, game)
        self.assertEqual(error.exception.status, 403)

    def test_not_before_the_countdown_ends(self):
        match = duel(self.organizer, self.alice, self.bob, [self.p1])
        game = start(self.organizer, match)
        with self.assertRaises(ArenaError):
            submit(self.alice, game)

    def test_not_after_the_game_is_decided(self):
        match, game = self.live_game()
        grade(submit(self.alice, game))
        with self.assertRaises(ArenaError):
            submit(self.bob, game)

    def test_not_after_time_is_up(self):
        match, game = self.live_game([self.p1, self.p2])
        time_up(game)
        with self.assertRaises(ArenaError):
            submit(self.bob, game)

    def test_language_must_be_allowed(self):
        match, game = self.live_game(rules={'languages': ['CPP17']})
        with self.assertRaises(ArenaError):
            submit(self.alice, game, language='PY3')
        submit(self.alice, game, language='CPP17')
        with self.assertRaises(ArenaError):
            submit(self.alice, game, language='JAVA8')

    def test_attempt_limit(self):
        match, game = self.live_game([self.p1, self.p2], rules={'max_attempts': 1})
        grade(submit(self.alice, game, 0), 'WA', 0, 1)
        with self.assertRaises(ArenaError):
            submit(self.alice, game, 0)

    def test_unknown_problem(self):
        match, game = self.live_game()
        with self.assertRaises(ArenaError):
            submit(self.alice, game, order=7)

    def test_nonce_makes_a_retry_harmless(self):
        match, game = self.live_game([self.p1, self.p2])
        first = submit(self.alice, game, nonce='abc123')
        again = submit(self.alice, game, nonce='abc123')
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(Submission.objects.filter(user=self.alice.profile).count(), 1)

    def test_in_flight_limit_ignores_custom_tests(self):
        match, game = self.live_game([self.p1, self.p2])
        submit(self.alice, game, 0)
        submit(self.alice, game, 1)
        with self.assertRaises(ArenaError) as error:
            submit(self.alice, game, 0)
        self.assertEqual(error.exception.status, 429)


class SetupGuardTests(EngineTestCase):
    def test_a_player_in_another_battle_is_refused(self):
        duel(self.organizer, self.alice, self.bob, [self.p1])
        with self.assertRaises(ArenaError):
            duel(self.organizer, self.alice, self.carol, [self.p2])

    def test_display_names_only_for_quick_battles(self):
        match = duel(self.organizer, self.alice, self.bob, [self.p1], display_a='Ana', display_b='Beto')
        self.assertEqual((match.shown_name('a'), match.shown_name('b')), ('Ana', 'Beto'))
        tournament = arena_tournament.create_tournament(self.organizer.profile, 'Copa',
                                                        [self.alice.profile, self.bob.profile])
        arena_tournament.generate_bracket(self.organizer.profile, tournament.pk)
        final = ArenaMatch.objects.get(tournament=tournament)
        with self.assertRaises(ArenaError):
            engine.update_match(self.organizer.profile, final.pk, display_a='Otra persona')

    def test_problems_must_be_visible_to_the_organizer(self):
        hidden = problem('nadielove')
        weak = player('weakorganizer')
        with self.assertRaises(ArenaError):
            engine.create_duel(weak.profile, self.alice.profile, self.bob.profile, [[{'id': hidden.id}]])

    def test_custom_tests_are_never_offered(self):
        custom = problem('ct_0123456789ab')
        with self.assertRaises(ArenaError):
            duel(self.organizer, self.alice, self.bob, [custom])

    def test_too_many_problems(self):
        problems = [self.p1, self.p2, self.p3, self.p4, self.p5, problem('arenasix')]
        with self.assertRaises(ArenaError):
            duel(self.organizer, self.alice, self.bob, problems)

    def test_problems_cannot_change_once_started(self):
        match, game = self.live_game()
        with self.assertRaises(ArenaError):
            engine.set_game_problems(self.organizer.profile, match.pk, game.pk, [{'id': self.p2.id}])

    def test_best_of_cannot_decide_the_series_by_itself(self):
        games = [[{'id': self.p1.id}], [{'id': self.p2.id}]]
        match = duel(self.organizer, self.alice, self.bob, [], best_of=3, games=games)
        game = skip_countdown(start(self.organizer, match))
        grade(submit(self.alice, game))
        tick(game)
        with self.assertRaises(ArenaError):
            engine.update_match(self.organizer.profile, match.pk, best_of=1)

    def test_lock_set_follows_the_battle(self):
        with self.captureOnCommitCallbacks(execute=True):
            match = duel(self.organizer, self.alice, self.bob, [self.p1])
        self.assertEqual(sorted(cache.get(engine.LOCK_KEY)), sorted([self.alice.profile.pk, self.bob.profile.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            engine.walkover(self.organizer.profile, match.pk, 'a')
        self.assertEqual(cache.get(engine.LOCK_KEY), {})


class TournamentFlowTests(EngineTestCase):
    def test_four_players_with_third_place(self):
        dave = player('dave')
        profiles = [self.alice.profile, self.bob.profile, self.carol.profile, dave.profile]
        tournament = arena_tournament.create_tournament(self.organizer.profile, 'Copa CPC', profiles,
                                                        third_place=True)
        arena_tournament.generate_bracket(self.organizer.profile, tournament.pk)
        semifinal = ArenaMatch.objects.filter(tournament=tournament, round__number=1).order_by('position').first()
        with self.assertRaises(ArenaError):
            engine.call_match(self.organizer.profile, semifinal.pk)
        arena_tournament.start_tournament(self.organizer.profile, tournament.pk)

        problems = iter([self.p1, self.p2, self.p3, self.p4])
        for match in ArenaMatch.objects.filter(tournament=tournament, round__number=1).order_by('position'):
            engine.call_match(self.organizer.profile, match.pk)
            self.assertEqual(match.games.filter(status=ArenaGame.PREPARED).count(), 1, 'calling prepares game 1')
            engine.set_game_problems(self.organizer.profile, match.pk, None, [{'id': next(problems).id}])
            game = skip_countdown(start(self.organizer, match))
            winner = match.player_a.user
            grade(submit(winner, game))
            tick(game)
        final = ArenaMatch.objects.get(tournament=tournament, next_match__isnull=True, is_third_place=False)
        third = ArenaMatch.objects.get(tournament=tournament, is_third_place=True)
        self.assertEqual(final.status, ArenaMatch.READY)
        self.assertEqual(third.status, ArenaMatch.READY)
        self.assertEqual({final.player_a_id, final.player_b_id},
                         {self.alice.profile.pk, self.bob.profile.pk}, 'seeds 1 and 2 won their semifinals')

        for match, p in ((final, self.p5), (third, problem('arenaseven'))):
            online_judge(p)
            engine.call_match(self.organizer.profile, match.pk)
            engine.set_game_problems(self.organizer.profile, match.pk, None, [{'id': p.id}])
            game = skip_countdown(start(self.organizer, match))
            grade(submit(match.player_b.user, game))
            tick(game)
        tournament.refresh_from_db()
        self.assertEqual(tournament.status, ArenaTournament.FINISHED)

    def test_correcting_a_winner_moves_the_bracket(self):
        profiles = [self.alice.profile, self.bob.profile, self.carol.profile]
        tournament = arena_tournament.create_tournament(self.organizer.profile, 'Tres', profiles)
        arena_tournament.generate_bracket(self.organizer.profile, tournament.pk)
        arena_tournament.start_tournament(self.organizer.profile, tournament.pk)
        semifinal = ArenaMatch.objects.get(tournament=tournament, round__number=1, decided_reason='')
        engine.walkover(self.organizer.profile, semifinal.pk, 'a')
        final = ArenaMatch.objects.get(tournament=tournament, round__number=2)
        self.assertEqual(final.player_b_id, semifinal.player_a_id)
        arena_tournament.set_winner(self.organizer.profile, semifinal.pk, 'b')
        final.refresh_from_db()
        self.assertEqual(final.player_b_id, semifinal.player_b_id)

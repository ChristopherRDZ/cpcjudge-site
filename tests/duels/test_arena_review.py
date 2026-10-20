"""Duels: edge cases of the engine, the lock, the screens and the limits.

- a battle left open by mistake stops holding its players after a while without activity;
- a game nobody looked at after its time ran out does not block normal submissions;
- what the game does by itself (going live, ending) is not a conflict for the organizers;
- a new quick battle goes on a free projector screen by itself;
- three players with a third place match still finish their tournament;
- a decided battle keeps its players, and a player with their own account keeps their drafts;
- screens pick the live code up again after the cache restarts;
- the request limits come from the settings.
"""
import time
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from arena_testutil import duel, grade, organizer, online_judge, player, problem, skip_countdown, start, submit, \
    tick, time_up
from judge.arena import engine, live_code, serialize, tournament as arena_tournament
from judge.arena.engine import ArenaError, Conflict
from judge.models.arena import ArenaGame, ArenaMatch, ArenaScreen, ArenaTournament

PAGE = {'HTTP_ACCEPT': 'text/html,application/xhtml+xml'}


class ReviewTestCase(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer()
        cls.alice, cls.bob, cls.carol, cls.dave, cls.eve, cls.frank = \
            [player(name) for name in ('alice', 'bob', 'carol', 'dave', 'eve', 'frank')]
        cls.p1, cls.p2, cls.p3, cls.p4 = [problem('review%d' % number) for number in range(1, 5)]
        online_judge(cls.p1, cls.p2, cls.p3, cls.p4)

    def setUp(self):
        cache.clear()

    def duel(self, a=None, b=None, problems=None, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return duel(self.organizer, a or self.alice, b or self.bob, problems or [self.p1], **kwargs)

    def held(self, user):
        """Whether the rest of the site sends this user back to the arena."""
        self.client.force_login(user)
        response = self.client.get(reverse('problem_list'), **PAGE)
        return response.status_code == 302 and response['Location'] == '/duelos/'

    def refresh_lock(self):
        engine.refresh_lock()


class LockLapseTests(ReviewTestCase):
    def test_the_cached_entry_runs_out_by_itself(self):
        self.duel()
        self.assertTrue(self.held(self.alice))
        # Nothing rewrites the entry: an hour and a minute later it is simply over.
        with patch('judge.arena.lock.time') as clock:
            clock.time.return_value = time.time() + 61 * 60
            self.assertFalse(self.held(self.alice))
        self.assertTrue(self.held(self.alice))

    def test_rebuilt_from_the_database_after_an_hour_without_activity(self):
        match = self.duel()
        ArenaMatch.objects.filter(pk=match.pk).update(called_at=timezone.now() - timedelta(minutes=61))
        self.refresh_lock()
        self.assertFalse(self.held(self.alice))
        self.assertFalse(self.held(self.bob))
        state = serialize.organizer_match_state(ArenaMatch.objects.get(pk=match.pk), 'es')
        self.assertFalse(state['match']['held'])
        self.assertEqual(state['match']['idle_minutes'], 60)
        # The match itself is untouched: still in the waiting room, and the players still see it in the arena.
        self.assertEqual(ArenaMatch.objects.get(pk=match.pk).status, ArenaMatch.CALLED)
        self.assertEqual(serialize.player_state(self.alice.profile, 'es')['phase'], 'lobby')

    def test_starting_the_game_holds_them_again(self):
        match = self.duel()
        ArenaMatch.objects.filter(pk=match.pk).update(called_at=timezone.now() - timedelta(minutes=61))
        self.refresh_lock()
        self.assertFalse(self.held(self.alice))
        with self.captureOnCommitCallbacks(execute=True):
            start(self.organizer, ArenaMatch.objects.get(pk=match.pk))
        self.assertTrue(self.held(self.alice))
        self.assertTrue(serialize.organizer_match_state(ArenaMatch.objects.get(pk=match.pk), 'es')['match']['held'])

    def test_a_long_game_keeps_holding_until_it_ends(self):
        match = self.duel(rules={'duration': 180})
        game = skip_countdown(start(self.organizer, match))
        now = timezone.now()
        ArenaMatch.objects.filter(pk=match.pk).update(called_at=now - timedelta(minutes=150))
        ArenaGame.objects.filter(pk=game.pk).update(started_at=now - timedelta(minutes=150),
                                                    ends_at=now + timedelta(minutes=30))
        self.refresh_lock()
        self.assertTrue(self.held(self.alice))
        until = engine.lock_until(ArenaMatch.objects.get(pk=match.pk), ArenaGame.objects.filter(match=match))
        self.assertGreater(until, now + timedelta(minutes=85))

    def test_the_count_starts_again_when_a_game_of_the_series_ends(self):
        match = self.duel(best_of=3, games=[[{'id': self.p1.id}], [{'id': self.p2.id}]])
        game = skip_countdown(start(self.organizer, match))
        ArenaMatch.objects.filter(pk=match.pk).update(called_at=timezone.now() - timedelta(minutes=59))
        with self.captureOnCommitCallbacks(execute=True):
            grade(submit(self.alice, game))
            tick(game)
        match.refresh_from_db()
        self.assertEqual(match.status, ArenaMatch.PLAYING)
        until = engine.lock_until(match, ArenaGame.objects.filter(match=match))
        self.assertGreater(until, timezone.now() + timedelta(minutes=59))
        self.assertTrue(self.held(self.alice))

    @override_settings(CPC_ARENA_LOCK_IDLE_MINUTES=10)
    def test_the_time_comes_from_the_settings(self):
        match = self.duel()
        ArenaMatch.objects.filter(pk=match.pk).update(called_at=timezone.now() - timedelta(minutes=11))
        self.refresh_lock()
        self.assertFalse(self.held(self.alice))

    @override_settings(CPC_ARENA_LOCK_IDLE_MINUTES=1)
    def test_a_setting_out_of_range_falls_back_to_an_hour(self):
        self.assertEqual(engine.lock_idle_minutes(), 60)
        match = self.duel()
        ArenaMatch.objects.filter(pk=match.pk).update(called_at=timezone.now() - timedelta(minutes=11))
        self.refresh_lock()
        self.assertTrue(self.held(self.alice))

    def test_the_panel_explains_it(self):
        match = self.duel()
        ArenaMatch.objects.filter(pk=match.pk).update(called_at=timezone.now() - timedelta(minutes=61))
        self.client.force_login(self.organizer)
        for language in ('es', 'en'):
            response = self.client.get(reverse('arena_manage_match', args=[match.pk]), HTTP_ACCEPT_LANGUAGE=language)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'id="mm-released"')
            self.assertContains(response, ' 60 ')


class LiveGuardTests(ReviewTestCase):
    def test_a_game_nobody_looked_at_after_time_does_not_block_normal_submissions(self):
        match = self.duel()
        game = tick(skip_countdown(start(self.organizer, match)))
        self.assertEqual(game.status, ArenaGame.LIVE)
        self.assertTrue(engine.is_live_arena_player(self.alice.profile))
        time_up(game)
        self.assertEqual(ArenaGame.objects.get(pk=game.pk).status, ArenaGame.LIVE, 'nothing ticked it')
        self.assertFalse(engine.is_live_arena_player(self.alice.profile))


class VersionTests(ReviewTestCase):
    def test_going_live_by_itself_is_not_a_conflict(self):
        match = self.duel()
        game = start(self.organizer, match)
        seen = ArenaMatch.objects.get(pk=match.pk).version
        self.assertEqual(tick(skip_countdown(game)).status, ArenaGame.LIVE)
        self.assertEqual(ArenaMatch.objects.get(pk=match.pk).version, seen)
        engine.extend_game(self.organizer.profile, match.pk, game.pk, 1, version=seen)

    def test_ending_by_itself_does_not_move_the_version(self):
        match = self.duel(best_of=3, games=[[{'id': self.p1.id}], [{'id': self.p2.id}]])
        game = skip_countdown(start(self.organizer, match))
        seen = ArenaMatch.objects.get(pk=match.pk).version
        grade(submit(self.alice, game))
        self.assertEqual(tick(game).status, ArenaGame.FINISHED)
        match.refresh_from_db()
        self.assertEqual((match.wins_a, match.version), (1, seen))

    def test_two_organizers_still_conflict(self):
        match = self.duel()
        game = tick(skip_countdown(start(self.organizer, match)))
        seen = ArenaMatch.objects.get(pk=match.pk).version
        engine.extend_game(self.organizer.profile, match.pk, game.pk, 5, version=seen)
        with self.assertRaises(Conflict):
            engine.extend_game(self.organizer.profile, match.pk, game.pk, 5, version=seen)


class ScreenFollowsNewBattleTests(ReviewTestCase):
    def test_the_first_battle_goes_on_the_first_screen(self):
        match = self.duel()
        screen = ArenaScreen.objects.get()
        self.assertEqual((screen.mode, screen.match_id), (ArenaScreen.MATCH, match.pk))

    def test_the_next_battle_replaces_one_that_is_over(self):
        first = self.duel()
        engine.walkover(self.organizer.profile, first.pk, 'a')
        second = self.duel(self.carol, self.dave)
        self.assertEqual(ArenaScreen.objects.get().match_id, second.pk)

    def test_a_battle_still_going_keeps_its_screen(self):
        first = self.duel()
        self.duel(self.carol, self.dave)
        self.assertEqual(ArenaScreen.objects.get().match_id, first.pk)
        spare = engine.create_screen(self.organizer.profile, 'Z spare')
        third = self.duel(self.eve, self.frank)
        spare.refresh_from_db()
        self.assertEqual((spare.mode, spare.match_id), (ArenaScreen.MATCH, third.pk))
        self.assertEqual(ArenaScreen.objects.exclude(pk=spare.pk).get().match_id, first.pk)

    def test_a_message_or_a_bracket_is_left_alone(self):
        message = engine.create_screen(self.organizer.profile, 'A message')
        engine.show_on_screen(self.organizer.profile, message.pk, ArenaScreen.MESSAGE, message='Pausa')
        cup = arena_tournament.create_tournament(self.organizer.profile, 'Copa', [self.eve.profile,
                                                                                self.frank.profile])
        bracket = engine.create_screen(self.organizer.profile, 'B bracket')
        engine.show_on_screen(self.organizer.profile, bracket.pk, ArenaScreen.BRACKET, tournament=cup)
        self.duel()
        message.refresh_from_db()
        bracket.refresh_from_db()
        self.assertEqual((message.mode, message.message), (ArenaScreen.MESSAGE, 'Pausa'))
        self.assertEqual(bracket.mode, ArenaScreen.BRACKET)


class ThirdPlaceTests(ReviewTestCase):
    def play(self, match, problem_obj, winner='a'):
        engine.call_match(self.organizer.profile, match.pk)
        engine.set_game_problems(self.organizer.profile, match.pk, None, [{'id': problem_obj.id}])
        match = ArenaMatch.objects.get(pk=match.pk)
        game = skip_countdown(start(self.organizer, match))
        grade(submit(match.player(winner).user, game))
        tick(game)

    def test_three_players_with_third_place_still_finish(self):
        cup = arena_tournament.create_tournament(self.organizer.profile, 'Tres', [self.alice.profile,
                                                 self.bob.profile, self.carol.profile], third_place=True)
        arena_tournament.generate_bracket(self.organizer.profile, cup.pk)
        arena_tournament.start_tournament(self.organizer.profile, cup.pk)
        self.assertFalse(ArenaMatch.objects.filter(tournament=cup, is_third_place=True).exists())
        semifinal = ArenaMatch.objects.get(tournament=cup, round__number=1, status=ArenaMatch.READY)
        self.play(semifinal, self.p1)
        final = ArenaMatch.objects.get(tournament=cup, round__number=2)
        self.assertEqual(final.status, ArenaMatch.READY)
        self.play(final, self.p2)
        cup.refresh_from_db()
        self.assertEqual(cup.status, ArenaTournament.FINISHED)

    def test_four_players_keep_their_third_place_match(self):
        cup = arena_tournament.create_tournament(self.organizer.profile, 'Cuatro', [self.alice.profile,
                                                 self.bob.profile, self.carol.profile, self.dave.profile],
                                                 third_place=True)
        arena_tournament.generate_bracket(self.organizer.profile, cup.pk)
        self.assertTrue(ArenaMatch.objects.filter(tournament=cup, is_third_place=True).exists())


class SmallerFixesTests(ReviewTestCase):
    def test_a_decided_battle_keeps_its_players(self):
        match = self.duel()
        engine.walkover(self.organizer.profile, match.pk, 'a')
        with self.assertRaises(ArenaError):
            engine.replace_player(self.organizer.profile, match.pk, 'b', self.carol.profile)

    def test_drafts_only_while_the_game_is_on(self):
        match = self.duel()
        game = tick(skip_countdown(start(self.organizer, match)))
        live_code.store(game.pk, 'a', 0, 'PY3', 'print(1)')
        self.client.force_login(self.alice)
        url = reverse('arena_code_mine', args=[game.pk])
        self.assertEqual(self.client.get(url).json()['drafts']['0']['text'], 'print(1)')
        grade(submit(self.alice, game))
        self.assertEqual(tick(game).status, ArenaGame.FINISHED)
        self.assertEqual(self.client.get(url).json()['drafts']['0']['text'], 'print(1)')

    def test_screens_pick_the_code_up_again_after_the_cache_restarts(self):
        match = self.duel()
        game = tick(skip_countdown(start(self.organizer, match)))
        for text in ('a', 'ab', 'abc'):
            live_code.store(game.pk, 'a', 0, 'PY3', text)
        self.assertIsNone(serialize.code_payload(game, {'a': 3, 'b': 0}, False)['a'])
        cache.clear()
        live_code.store(game.pk, 'a', 0, 'PY3', 'again')
        payload = serialize.code_payload(game, {'a': 3, 'b': 0}, False)['a']
        self.assertEqual((payload['seq'], payload['text']), (1, 'again'))


class LimitSettingsTests(ReviewTestCase):
    @override_settings(CPC_ARENA_STATE_PER_SECOND=1)
    def test_limits_come_from_the_settings(self):
        self.client.force_login(self.carol)
        codes = [self.client.get(reverse('arena_state')).status_code for _ in range(3)]
        self.assertIn(429, codes)

    @override_settings(CPC_ARENA_STATE_PER_SECOND=0)
    def test_zero_turns_a_limit_off(self):
        self.client.force_login(self.carol)
        self.assertEqual({self.client.get(reverse('arena_state')).status_code for _ in range(12)}, {200})

    @override_settings(CPC_ARENA_STATE_PER_SECOND='mucho', CPC_ARENA_CODE_PER_SECOND=True)
    def test_a_wrong_setting_falls_back_to_the_default(self):
        self.assertEqual(engine.arena_setting('CPC_ARENA_STATE_PER_SECOND', 6), 6)
        self.assertEqual(engine.arena_setting('CPC_ARENA_CODE_PER_SECOND', 4), 4)
        self.client.force_login(self.carol)
        self.assertEqual(self.client.get(reverse('arena_state')).status_code, 200)

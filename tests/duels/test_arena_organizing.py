"""Duels: problems chosen per battle and per game, problems given by a bank, the bracket built by itself, the
organizer guide, the review mode and the player page.
"""
import json

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from arena_testutil import duel, grade, organizer, online_judge, player, problem, skip_countdown, start, submit, tick
from judge.arena import engine, live_code, rules as arena_rules, serialize, tournament as arena_tournament
from judge.arena.engine import ArenaError
from judge.models.arena import ArenaAction, ArenaGame, ArenaGameProblem, ArenaMatch, ArenaScreen, ArenaTournament


class AjustesTestCase(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer()
        cls.players = [player('jug%d' % number) for number in range(1, 9)]
        cls.problems = [problem('ajuste%d' % number) for number in range(1, 13)]
        online_judge(*cls.problems)

    def setUp(self):
        cache.clear()

    def tournament(self, count=4, **kwargs):
        return arena_tournament.create_tournament(self.organizer.profile, 'Copa %d' % count,
                                                  [p.profile for p in self.players[:count]], **kwargs)

    def first_round(self, tournament):
        return list(ArenaMatch.objects.filter(tournament=tournament, round__number=1, status=ArenaMatch.READY)
                    .order_by('position'))

    def problems_of(self, match, number=1):
        game = ArenaGame.objects.filter(match=match, number=number).first()
        return [] if game is None else list(ArenaGameProblem.objects.filter(game=game).order_by('order')
                                            .values_list('problem__code', flat=True))

    def bank(self, levels):
        bank = arena_tournament.create_bank(self.organizer.profile, 'Banco')
        arena_tournament.set_bank_problems(self.organizer.profile, bank.pk, [
            {'id': p.id, 'level': level} for p, level in zip(self.problems, levels)])
        return bank


class BracketBuiltByItselfTests(AjustesTestCase):
    def test_creating_a_tournament_builds_its_bracket(self):
        cup = self.tournament(4)
        self.assertEqual(ArenaMatch.objects.filter(tournament=cup).count(), 3)

    def test_saving_participants_or_the_third_place_rebuilds_it(self):
        cup = self.tournament(4)
        arena_tournament.set_participants(self.organizer.profile, cup.pk, [p.profile for p in self.players[:6]])
        self.assertEqual(ArenaMatch.objects.filter(tournament=cup).count(), 7)
        arena_tournament.update_tournament(self.organizer.profile, cup.pk, third_place=True)
        self.assertTrue(ArenaMatch.objects.filter(tournament=cup, is_third_place=True).exists())

    def test_problems_chosen_by_hand_survive_a_rebuild(self):
        cup = self.tournament(4)
        final = ArenaMatch.objects.get(tournament=cup, round__number=2)
        semifinal = self.first_round(cup)[0]
        engine.set_game_problems(self.organizer.profile, final.pk, None, [{'id': self.problems[0].id}], number=1)
        engine.set_game_problems(self.organizer.profile, semifinal.pk, None, [{'id': self.problems[1].id}], number=1)
        # Before the change this was a ProtectedError: games point to their match with PROTECT.
        arena_tournament.set_participants(self.organizer.profile, cup.pk, [p.profile for p in self.players[:4]])
        arena_tournament.generate_bracket(self.organizer.profile, cup.pk)
        self.assertEqual(self.problems_of(ArenaMatch.objects.get(tournament=cup, round__number=2)), ['ajuste1'])
        self.assertEqual(self.problems_of(self.first_round(cup)[0]), ['ajuste2'])

    def test_a_place_that_becomes_a_bye_does_not_keep_problems(self):
        cup = self.tournament(4)
        first = self.first_round(cup)[0]
        engine.set_game_problems(self.organizer.profile, first.pk, None, [{'id': self.problems[0].id}], number=1)
        arena_tournament.set_participants(self.organizer.profile, cup.pk, [p.profile for p in self.players[:3]])
        bye = ArenaMatch.objects.get(tournament=cup, round__number=1, position=0)
        self.assertEqual(bye.decided_reason, 'bye')
        self.assertFalse(ArenaGame.objects.filter(match=bye).exists())


class ProblemsPerBattleTests(AjustesTestCase):
    def test_any_game_of_a_series_before_anything_starts(self):
        cup = self.tournament(4)
        arena_tournament.set_round(self.organizer.profile, cup.pk, 1, best_of=3)
        match = self.first_round(cup)[0]
        engine.set_game_problems(self.organizer.profile, match.pk, None, [{'id': self.problems[2].id}], number=3)
        self.assertEqual(sorted(ArenaGame.objects.filter(match=match).values_list('number', flat=True)), [1, 2, 3])
        self.assertEqual(self.problems_of(match, 3), ['ajuste3'])
        with self.assertRaises(ArenaError):
            engine.set_game_problems(self.organizer.profile, match.pk, None, [{'id': self.problems[3].id}], number=4)

    def test_a_match_whose_players_are_not_known_yet(self):
        cup = self.tournament(4)
        final = ArenaMatch.objects.get(tournament=cup, round__number=2)
        self.assertEqual(final.status, ArenaMatch.PENDING)
        engine.set_game_problems(self.organizer.profile, final.pk, None, [{'id': self.problems[5].id, 'points': 300}],
                                 number=1)
        self.assertEqual(self.problems_of(final), ['ajuste6'])

    def test_the_prepared_problems_are_played(self):
        cup = self.tournament(2)
        arena_tournament.start_tournament(self.organizer.profile, cup.pk)
        match = self.first_round(cup)[0]
        engine.set_game_problems(self.organizer.profile, match.pk, None, [{'id': self.problems[4].id}], number=1)
        engine.call_match(self.organizer.profile, match.pk)
        game = skip_countdown(start(self.organizer, ArenaMatch.objects.get(pk=match.pk)))
        self.assertEqual(self.problems_of(match), ['ajuste5'])
        self.assertEqual(game.number, 1)

    def test_the_organizer_page_shows_every_game_of_the_series(self):
        cup = self.tournament(4)
        arena_tournament.set_round(self.organizer.profile, cup.pk, 1, best_of=3)
        state = serialize.organizer_match_state(self.first_round(cup)[0], 'es')
        self.assertEqual(state['match']['max_games'], 3)
        self.assertEqual(state['match']['draw'], {'auto': False, 'bank': False, 'count': 1})


class ProblemsFromTheBankTests(AjustesTestCase):
    def auto_cup(self, levels=(1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 5, 5), count=4, per_game=1):
        cup = self.tournament(count)
        bank = self.bank(levels)
        arena_tournament.update_tournament(self.organizer.profile, cup.pk, bank=bank,
                                           draw={'auto_draw': True, 'draw_count': per_game})
        arena_tournament.start_tournament(self.organizer.profile, cup.pk)
        return ArenaTournament.objects.get(pk=cup.pk)

    def test_calling_a_battle_gives_it_problems(self):
        cup = self.auto_cup(per_game=2)
        match = self.first_round(cup)[0]
        engine.call_match(self.organizer.profile, match.pk)
        chosen = self.problems_of(match)
        self.assertEqual(len(chosen), 2)
        self.assertTrue(ArenaAction.objects.filter(match=match, action='bank_problems').exists())

    def test_never_the_same_problem_twice_in_the_tournament(self):
        cup = self.auto_cup()
        seen = []
        for match in self.first_round(cup):
            engine.call_match(self.organizer.profile, match.pk)
            seen += self.problems_of(match)
        self.assertEqual(len(seen), len(set(seen)))

    def test_problems_chosen_by_hand_are_kept(self):
        cup = self.auto_cup()
        match = self.first_round(cup)[0]
        engine.set_game_problems(self.organizer.profile, match.pk, None, [{'id': self.problems[11].id}], number=1)
        engine.call_match(self.organizer.profile, match.pk)
        self.assertEqual(self.problems_of(match), ['ajuste12'])

    def test_each_round_draws_from_its_difficulty(self):
        cup = self.auto_cup()
        arena_tournament.set_round(self.organizer.profile, cup.pk, 1, draw={'level_min': 4, 'level_max': 5})
        for match in self.first_round(cup):
            engine.call_match(self.organizer.profile, match.pk)
            for code in self.problems_of(match):
                self.assertGreaterEqual(int(code.replace('ajuste', '')), 7, code)

    def test_a_bank_without_enough_problems_leaves_the_game_to_be_chosen_by_hand(self):
        cup = self.auto_cup(levels=(1,) + (None,) * 11)
        arena_tournament.set_round(self.organizer.profile, cup.pk, 1, draw={'level_min': 1, 'level_max': 1})
        first, second = self.first_round(cup)
        engine.call_match(self.organizer.profile, first.pk)
        engine.call_match(self.organizer.profile, second.pk)
        self.assertEqual(len(self.problems_of(first)) + len(self.problems_of(second)), 1)
        empty = second if not self.problems_of(second) else first
        state = serialize.organizer_match_state(ArenaMatch.objects.get(pk=empty.pk), 'es')
        game = next(g for g in state['games'] if g['number'] == 1)
        self.assertTrue(game['bank_empty'])

    def test_the_next_game_of_a_series_gets_problems_too(self):
        cup = self.tournament(2)
        bank = self.bank((1,) * 12)
        arena_tournament.update_tournament(self.organizer.profile, cup.pk, bank=bank, draw={'auto_draw': True})
        arena_tournament.set_round(self.organizer.profile, cup.pk, 1, best_of=3)
        arena_tournament.start_tournament(self.organizer.profile, cup.pk)
        match = self.first_round(cup)[0]
        engine.call_match(self.organizer.profile, match.pk)
        game = skip_countdown(start(self.organizer, ArenaMatch.objects.get(pk=match.pk)))
        grade(submit(match.player_a.user, game))
        self.assertEqual(tick(game).status, ArenaGame.FINISHED)
        self.assertEqual(len(self.problems_of(match, 2)), 1)

    def test_give_problems_now_to_every_battle_ready(self):
        cup = self.tournament(4)
        bank = self.bank((2,) * 12)
        arena_tournament.update_tournament(self.organizer.profile, cup.pk, bank=bank)
        self.assertEqual(arena_tournament.fill_ready_matches(self.organizer.profile, cup.pk), 2)
        self.assertEqual(arena_tournament.fill_ready_matches(self.organizer.profile, cup.pk), 0)

    def test_by_hand_mode_never_gives_problems_by_itself(self):
        cup = self.tournament(2)
        arena_tournament.update_tournament(self.organizer.profile, cup.pk, bank=self.bank((1,) * 12))
        arena_tournament.start_tournament(self.organizer.profile, cup.pk)
        match = self.first_round(cup)[0]
        engine.call_match(self.organizer.profile, match.pk)
        self.assertEqual(self.problems_of(match), [])


class DrawSettingsTests(AjustesTestCase):
    def test_game_rules_and_drawing_settings_do_not_overwrite_each_other(self):
        cup = self.tournament(4)
        arena_tournament.update_tournament(self.organizer.profile, cup.pk, draw={'auto_draw': True, 'draw_count': 3})
        arena_tournament.update_tournament(self.organizer.profile, cup.pk, rules={'duration': 20})
        cup.refresh_from_db()
        self.assertEqual((cup.rules['duration'], cup.rules['auto_draw'], cup.rules['draw_count']), (20, True, 3))
        arena_tournament.update_tournament(self.organizer.profile, cup.pk, draw={'draw_count': None})
        cup.refresh_from_db()
        self.assertNotIn('draw_count', cup.rules)
        self.assertEqual(cup.rules['duration'], 20)
        # Drawing settings never become rules of a game.
        self.assertNotIn('auto_draw', arena_rules.merge(cup.rules))

    def test_bad_values_are_refused(self):
        cup = self.tournament(4)
        for draw in ({'draw_count': 9}, {'level_min': 5, 'level_max': 2}, {'level_min': 'hard'}):
            with self.assertRaises(ArenaError):
                arena_tournament.set_round(self.organizer.profile, cup.pk, 1, draw=draw)


class ReadinessTests(AjustesTestCase):
    def test_the_organizer_sees_which_battles_have_problems(self):
        cup = self.tournament(4)
        first, second = self.first_round(cup)
        engine.set_game_problems(self.organizer.profile, first.pk, None, [{'id': self.problems[0].id}], number=1)
        state = serialize.tournament_state(cup, 'es')
        rows = {m['id']: m['problems'] for m in state['bracket']['rounds'][0]['matches']}
        self.assertEqual(rows[first.pk], {'chosen': 1, 'next_ready': True})
        self.assertEqual(rows[second.pk], {'chosen': 0, 'next_ready': False})

    def test_the_projector_learns_nothing_about_problems(self):
        cup = self.tournament(4)
        engine.set_game_problems(self.organizer.profile, self.first_round(cup)[0].pk, None,
                                 [{'id': self.problems[0].id}], number=1)
        screen = engine.create_screen(self.organizer.profile, 'Proyector')
        engine.show_on_screen(self.organizer.profile, screen.pk, ArenaScreen.BRACKET, tournament=cup)
        data = json.dumps(serialize.screen_state(ArenaScreen.objects.get(pk=screen.pk), 'es'))
        self.assertNotIn('"problems"', data)
        self.assertNotIn('ajuste1', data)


class LiveCodeTests(AjustesTestCase):
    def test_asking_with_nothing_new_costs_no_query(self):
        # Screens now ask about three times a second: the usual answer, "nothing new", must stay cheap.
        game = skip_countdown(start(self.organizer, duel(self.organizer, self.players[0], self.players[1],
                                                         [self.problems[0]])))
        live_code.store(game.pk, 'a', 0, 'PY3', 'print(1)')
        game = ArenaGame.objects.get(pk=game.pk)
        seq = serialize.code_payload(game, {}, True)['a']['seq']
        with self.assertNumQueries(0):
            data = serialize.code_payload(game, {'a': seq}, True)
        self.assertEqual(data, {'enabled': True, 'a': None, 'b': None})
        live_code.store(game.pk, 'a', 0, 'PY3', 'print(2)')
        data = serialize.code_payload(game, {'a': seq}, True)
        self.assertEqual((data['a']['text'], data['a']['label'], data['b']), ('print(2)', 'A', None))


class ReviewTests(AjustesTestCase):
    """After the battle a player on their own account may stay to look it over; a club laptop is cleared."""

    def finished(self, **names):
        match = duel(self.organizer, self.players[0], self.players[1], [self.problems[0]], **names)
        game = skip_countdown(start(self.organizer, match))
        live_code.store(game.pk, 'a', 0, 'PY3', 'print(1)  # draft')
        submission = grade(submit(self.players[0], game, source='print(1)  # sent'))
        self.assertEqual(tick(game).status, ArenaGame.FINISHED)
        return ArenaMatch.objects.get(pk=match.pk), ArenaGame.objects.get(pk=game.pk), submission

    def test_own_account_can_review(self):
        match, game, submission = self.finished()
        self.assertEqual(match.status, ArenaMatch.DECIDED)
        for user in self.players[:2]:
            state = serialize.player_state(user.profile, 'es')
            self.assertEqual((state['phase'], state['review']), ('decided', True))
        self.client.force_login(self.players[0])
        drafts = self.client.get(reverse('arena_code_mine', args=[game.pk])).json()['drafts']
        self.assertEqual(drafts['0']['text'], 'print(1)  # draft')
        detail = serialize.player_submission(self.players[0].profile, game, submission.pk)
        self.assertEqual(detail['source'], 'print(1)  # sent')

    def test_a_club_laptop_is_cleared_for_the_next_guest(self):
        match, game, submission = self.finished(display_a='Invitado Uno')
        self.assertFalse(serialize.player_state(self.players[0].profile, 'es')['review'])
        # The other place has no typed name: that player is on their own account.
        self.assertTrue(serialize.player_state(self.players[1].profile, 'es')['review'])
        self.client.force_login(self.players[0])
        self.assertEqual(self.client.get(reverse('arena_code_mine', args=[game.pk])).json()['drafts'], {})
        self.assertNotIn('source', serialize.player_submission(self.players[0].profile, game, submission.pk))

    def test_no_review_while_playing(self):
        match = duel(self.organizer, self.players[0], self.players[1], [self.problems[0]])
        skip_countdown(start(self.organizer, match))
        state = serialize.player_state(self.players[0].profile, 'es')
        self.assertEqual((state['phase'], state['review']), ('live', False))

    def test_the_page_names_the_rival(self):
        # run.py runs this suite once in Spanish and once in English.
        # Without accents: inside the JSON of the page they may come as \u escapes.
        text = 'todos los problemas antes que' if settings.LANGUAGE_CODE == 'es' else 'solved every problem before you.'
        self.client.force_login(self.players[0])
        response = self.client.get(reverse('arena_home'))
        self.assertContains(response, text)
        self.assertContains(response, '{rival} ')


class PagesTests(AjustesTestCase):
    def post(self, url, data):
        return self.client.post(url, json.dumps(data), content_type='application/json')

    def test_the_guide_is_for_organizers(self):
        self.client.force_login(self.organizer)
        response = self.client.get(reverse('arena_manage_guide'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="torneo"')
        self.assertContains(response, 'id="problemas"')
        self.client.force_login(self.players[0])
        self.assertEqual(self.client.get(reverse('arena_manage_guide')).status_code, 404)

    def test_every_organizer_page_links_to_the_guide(self):
        cup = self.tournament(4)
        self.client.force_login(self.organizer)
        guide = reverse('arena_manage_guide')
        for url in (reverse('arena_manage'), reverse('arena_manage_tournament', args=[cup.slug]),
                    reverse('arena_manage_match', args=[self.first_round(cup)[0].pk]), reverse('arena_manage_banks')):
            self.assertContains(self.client.get(url), guide)

    def test_the_logo_of_the_player_page_goes_home(self):
        self.client.force_login(self.players[0])
        self.assertContains(self.client.get(reverse('arena_home')),
                            '<li class="home-nav-element"><a href="%s">' % reverse('home'))

    def test_problems_by_game_number_and_bank_actions_through_the_views(self):
        cup = self.tournament(4)
        match = self.first_round(cup)[0]
        self.client.force_login(self.organizer)
        response = self.post(reverse('arena_manage_match_action', args=[match.pk]),
                             {'action': 'problems', 'number': 1, 'problems': [{'id': self.problems[0].id}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.problems_of(match), ['ajuste1'])
        bank = self.bank((3,) * 12)
        url = reverse('arena_manage_tournament_action', args=[cup.slug])
        response = self.post(url, {'action': 'update', 'bank': bank.pk, 'draw': {'auto_draw': True, 'draw_count': 2}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state']['draw'], {'auto': True, 'count': 2, 'bank': bank.pk})
        response = self.post(reverse('arena_manage_draw', args=[match.pk]), {'number': 2})
        self.assertEqual(len(response.json()['problems']), 2)
        response = self.post(url, {'action': 'fill'})
        self.assertEqual(response.json()['filled'], 1, 'only the battle without problems')
        response = self.post(url, {'action': 'round', 'number': 1, 'draw': {'level_min': 4, 'level_max': 2}})
        self.assertEqual(response.status_code, 400)

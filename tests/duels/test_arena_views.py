"""Arena pages and endpoints: who can open what, nothing about the problems before the start, limits and CSRF."""
import json

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from arena_testutil import duel, grade, organizer, online_judge, player, problem, skip_countdown, start, submit
from judge.arena import engine
from judge.models import Submission
from judge.models.arena import ArenaGame, ArenaMatch, ArenaScreen
from judge.models.tests.util import create_user

SECRET_CODE = 'secretprob'
SECRET_NAME = 'Secreto Unico Arena'


class ArenaViewTestCase(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer()
        cls.superuser = create_user(username='root', is_superuser=True, is_staff=True)
        cls.alice = player('alice')
        cls.bob = player('bob')
        cls.carol = player('carol')
        cls.secret = problem(SECRET_CODE, name=SECRET_NAME)
        cls.secret.description = 'Statement body of the secret problem.'
        cls.secret.save()
        cls.other = problem('otherprob')
        online_judge(cls.secret, cls.other)

    def setUp(self):
        cache.clear()

    def login(self, user):
        self.client.force_login(user)

    def post_json(self, url, data, client=None, **extra):
        return (client or self.client).post(url, json.dumps(data), content_type='application/json', **extra)

    def battle(self):
        return duel(self.organizer, self.alice, self.bob, [self.secret])

    def live(self):
        match = self.battle()
        game = skip_countdown(start(self.organizer, match), seconds_ago=30)
        return match, game


class PermissionTests(ArenaViewTestCase):
    def test_panel_is_invisible_to_everybody_else(self):
        match = self.battle()
        urls = [reverse('arena_manage'), reverse('arena_manage_overview'),
                reverse('arena_manage_match', args=[match.pk]),
                reverse('arena_manage_match_state', args=[match.pk]), reverse('arena_manage_banks'),
                reverse('arena_manage_problems') + '?term=se', reverse('arena_manage_users') + '?term=al']
        for url in urls:
            self.client.logout()
            self.assertEqual(self.client.get(url).status_code, 404, url)
            self.login(self.carol)
            self.assertEqual(self.client.get(url).status_code, 404, url)
            self.login(self.alice)
            self.assertIn(self.client.get(url).status_code, (302, 404), url)
            self.login(self.organizer)
            self.assertEqual(self.client.get(url).status_code, 200, url)
            self.login(self.superuser)
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_arena_requires_login(self):
        response = self.client.get(reverse('arena_home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_somebody_without_a_battle_sees_the_waiting_page(self):
        self.login(self.carol)
        self.assertEqual(self.client.get(reverse('arena_home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('arena_state')).json()['phase'], 'idle')

    def test_game_endpoints_are_for_its_players(self):
        match, game = self.live()
        own = submit(self.alice, game)
        self.login(self.carol)
        for url in (reverse('arena_code_mine', args=[game.pk]),
                    reverse('arena_submission', args=[game.pk, own.pk]),
                    reverse('arena_statement', args=[game.pk, 0])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.post_json(reverse('arena_code', args=[game.pk]),
                                        {'problem': 0, 'text': 'x', 'language': 'PY3'}).status_code, 404)
        self.login(self.bob)
        self.assertEqual(self.client.get(reverse('arena_submission', args=[game.pk, own.pk])).status_code, 404,
                         'a player never sees the rival submission')
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('arena_submission', args=[game.pk, own.pk])).status_code, 200)

    def test_organizer_reads_only_arena_sources(self):
        match, game = self.live()
        own = submit(self.alice, game)
        outside = Submission.objects.create(user=self.carol.profile, problem=self.other,
                                            language=own.language, status='D', result='AC')
        self.login(self.organizer)
        self.assertEqual(self.client.get(reverse('arena_manage_submission', args=[own.pk])).json()['source'],
                         'print(1)')
        self.assertEqual(self.client.get(reverse('arena_manage_submission', args=[outside.pk])).status_code, 404)


class NoLeakBeforeStartTests(ArenaViewTestCase):
    def assertNoSecret(self, response):
        content = response.content.decode('utf-8')
        self.assertNotIn(SECRET_CODE, content)
        self.assertNotIn(SECRET_NAME, content)
        self.assertNotIn('Statement body', content)

    def test_nothing_about_the_problem_until_the_countdown_ends(self):
        match = self.battle()
        screen = engine.create_screen(self.organizer.profile, 'Proyector')
        engine.show_on_screen(self.organizer.profile, screen.pk, ArenaScreen.MATCH, match=match)
        header = {'HTTP_X_ARENA_SCREEN': screen.token}
        self.login(self.alice)
        for phase in ('lobby', 'countdown'):
            if phase == 'countdown':
                game = start(self.organizer, match)
            self.assertNoSecret(self.client.get(reverse('arena_home')))
            state = self.client.get(reverse('arena_state'))
            self.assertEqual(state.json()['phase'], phase)
            self.assertNoSecret(state)
            self.assertNoSecret(self.client.get(reverse('arena_screen_state'), **header))
            self.assertNoSecret(self.client.get(reverse('arena_screen', args=[screen.token])))
            self.assertEqual(self.client.get(reverse('arena_screen_statement', args=[0]), **header).status_code, 404)
        self.assertEqual(self.client.get(reverse('arena_statement', args=[game.pk, 0])).status_code, 404)

        skip_countdown(game)
        state = self.client.get(reverse('arena_state')).json()
        self.assertEqual(state['phase'], 'live')
        self.assertEqual(state['game']['problems'][0]['name'], SECRET_NAME)
        statement = self.client.get(reverse('arena_statement', args=[game.pk, 0]))
        self.assertEqual(statement.status_code, 200)
        self.assertIn('Statement body', statement.content.decode('utf-8'))
        self.assertIn(SECRET_NAME, self.client.get(reverse('arena_screen_state'), **header).content.decode('utf-8'))

    def test_the_rival_code_never_reaches_a_player(self):
        match, game = self.live()
        self.login(self.alice)
        self.post_json(reverse('arena_code', args=[game.pk]),
                       {'problem': 0, 'text': 'int main() { secret_of_alice(); }', 'language': 'CPP17'})
        self.login(self.bob)
        for url in (reverse('arena_state'), reverse('arena_home'), reverse('arena_code_mine', args=[game.pk])):
            self.assertNotIn('secret_of_alice', self.client.get(url).content.decode('utf-8'), url)
        self.login(self.alice)
        mine = self.client.get(reverse('arena_code_mine', args=[game.pk])).json()
        self.assertIn('secret_of_alice', mine['drafts']['0']['text'])


class ScreenTests(ArenaViewTestCase):
    def test_token_in_the_link_then_in_a_header(self):
        match, game = self.live()
        screen = engine.create_screen(self.organizer.profile, 'Pantalla')
        engine.show_on_screen(self.organizer.profile, screen.pk, ArenaScreen.MATCH, match=match)
        page = self.client.get(reverse('arena_screen', args=[screen.token]) + '?view=combined')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page['Referrer-Policy'], 'no-referrer')
        self.assertIn('noindex', page['X-Robots-Tag'])
        self.assertEqual(self.client.get(reverse('arena_screen_state')).status_code, 404)
        self.assertEqual(self.client.get(reverse('arena_screen_state'), HTTP_X_ARENA_SCREEN='nope').status_code, 404)
        self.assertEqual(self.client.get(reverse('arena_screen_state'),
                                         HTTP_X_ARENA_SCREEN=screen.token).json()['mode'], 'match')
        old = screen.token
        engine.regenerate_screen_token(self.organizer.profile, screen.pk)
        self.assertEqual(self.client.get(reverse('arena_screen_state'), HTTP_X_ARENA_SCREEN=old).status_code, 404)
        self.assertEqual(self.client.get(reverse('arena_screen', args=[old])).status_code, 404)

    def test_live_code_reaches_the_screen(self):
        match, game = self.live()
        screen = engine.create_screen(self.organizer.profile, 'Pantalla')
        engine.show_on_screen(self.organizer.profile, screen.pk, ArenaScreen.MATCH, match=match)
        self.login(self.alice)
        seq = self.post_json(reverse('arena_code', args=[game.pk]),
                             {'problem': 0, 'text': 'print(42)', 'language': 'PY3',
                              'cursor': {'row': 0, 'column': 3}}).json()['seq']
        self.client.logout()
        header = {'HTTP_X_ARENA_SCREEN': screen.token}
        code = self.client.get(reverse('arena_screen_code'), **header).json()
        self.assertEqual(code['a']['text'], 'print(42)')
        self.assertEqual(code['a']['cursor'], {'row': 0, 'column': 3})
        self.assertIsNone(code['b'])
        again = self.client.get(reverse('arena_screen_code') + '?a=%d' % seq, **header).json()
        self.assertIsNone(again['a'], 'nothing new since the last sequence number')

    def test_screen_code_can_be_switched_off(self):
        match = duel(self.organizer, self.alice, self.bob, [self.secret], rules={'screen_code': False})
        game = skip_countdown(start(self.organizer, match))
        screen = engine.create_screen(self.organizer.profile, 'Pantalla')
        engine.show_on_screen(self.organizer.profile, screen.pk, ArenaScreen.MATCH, match=match)
        self.login(self.alice)
        self.post_json(reverse('arena_code', args=[game.pk]), {'problem': 0, 'text': 'x', 'language': 'PY3'})
        self.assertEqual(self.client.get(reverse('arena_screen_code'), HTTP_X_ARENA_SCREEN=screen.token).json(),
                         {'enabled': False})

    def test_every_view_renders(self):
        match, game = self.live()
        screen = engine.create_screen(self.organizer.profile, 'Pantalla')
        engine.show_on_screen(self.organizer.profile, screen.pk, ArenaScreen.MATCH, match=match)
        for view in ('combined', 'scoreboard', 'code-a', 'code-b', 'statements', 'bracket', 'nonsense'):
            response = self.client.get(reverse('arena_screen', args=[screen.token]) + '?view=' + view)
            self.assertEqual(response.status_code, 200, view)


class LimitTests(ArenaViewTestCase):
    def test_code_size_limits(self):
        match, game = self.live()
        self.login(self.alice)
        url = reverse('arena_code', args=[game.pk])

        def status(response):
            # A fresh rate-limit window for every request, so the limit does not get in the way of what is
            # measured here. Sessions survive: they are also kept in the database.
            cache.clear()
            return response.status_code
        self.assertEqual(status(self.post_json(url, {'problem': 0, 'text': 'x' * 70000, 'language': 'PY3'})), 413)
        self.assertEqual(status(self.client.post(url, 'x' * (300 * 1024), content_type='application/json')), 413)
        self.assertEqual(status(self.client.post(url, 'not json', content_type='application/json')), 400)
        self.assertEqual(status(self.post_json(url, {'problem': 'zero', 'text': 'x'})), 400)
        self.assertEqual(status(self.post_json(url, {'problem': 5, 'text': 'x', 'language': 'PY3'})), 400)

    def test_code_rate_limit(self):
        match, game = self.live()
        self.login(self.alice)
        url = reverse('arena_code', args=[game.pk])
        codes = [self.post_json(url, {'problem': 0, 'text': 'x%d' % i, 'language': 'PY3'}).status_code
                 for i in range(12)]
        self.assertIn(429, codes)

    def test_csrf_is_required(self):
        match, game = self.live()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.alice.user if hasattr(self.alice, 'user') else self.alice)
        response = self.post_json(reverse('arena_submit', args=[game.pk]),
                                  {'problem': 0, 'language': 'PY3', 'source': 'print(1)'}, client=client)
        self.assertEqual(response.status_code, 403)
        response = self.post_json(reverse('arena_code', args=[game.pk]),
                                  {'problem': 0, 'language': 'PY3', 'text': 'x'}, client=client)
        self.assertEqual(response.status_code, 403)

    def test_submit_through_the_view(self):
        from arena_testutil import NoJudging
        match, game = self.live()
        self.login(self.alice)
        with NoJudging():
            first = self.post_json(reverse('arena_submit', args=[game.pk]),
                                   {'problem': 0, 'language': 'PY3', 'source': 'print(1)', 'nonce': 'n1'})
            again = self.post_json(reverse('arena_submit', args=[game.pk]),
                                   {'problem': 0, 'language': 'PY3', 'source': 'print(1)', 'nonce': 'n1'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['id'], again.json()['id'])
        state = self.client.get(reverse('arena_state')).json()
        self.assertEqual(len(state['game']['submissions']), 1)
        self.login(self.carol)
        with NoJudging():
            refused = self.post_json(reverse('arena_submit', args=[game.pk]),
                                     {'problem': 0, 'language': 'PY3', 'source': 'print(1)'})
        self.assertEqual(refused.status_code, 403)


class ManageApiTests(ArenaViewTestCase):
    def test_create_a_duel_from_the_panel(self):
        self.login(self.organizer)
        response = self.post_json(reverse('arena_manage_duel_create'), {
            'player_a': self.alice.profile.pk, 'player_b': self.bob.profile.pk, 'best_of': 1,
            'games': [[{'id': self.secret.pk, 'points': 100}]], 'rules': {'duration': 5, 'penalty': 5},
        })
        self.assertEqual(response.status_code, 200, response.content)
        match = ArenaMatch.objects.get(pk=response.json()['id'])
        self.assertEqual(match.rules, {'duration': 5, 'penalty': 5})
        bad = self.post_json(reverse('arena_manage_duel_create'), {
            'player_a': self.alice.profile.pk, 'player_b': self.alice.profile.pk,
            'games': [[{'id': self.secret.pk}]]})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('error', bad.json())
        junk = self.post_json(reverse('arena_manage_duel_create'), {
            'player_a': 'x', 'player_b': True, 'games': 'nope', 'rules': {'duration': 'mucho'}})
        self.assertEqual(junk.status_code, 400)

    def test_actions_and_stale_versions(self):
        match = self.battle()
        self.login(self.organizer)
        url = reverse('arena_manage_match_action', args=[match.pk])
        self.assertEqual(self.post_json(url, {'action': 'start', 'version': match.version + 3}).status_code, 409)
        warned = self.post_json(url, {'action': 'start', 'version': match.version})
        self.assertEqual(warned.status_code, 200)
        self.assertTrue(warned.json()['warnings'])
        started = self.post_json(url, {'action': 'start', 'force': True,
                                       'version': warned.json()['state']['match']['version']})
        self.assertEqual(started.status_code, 200)
        self.assertEqual(ArenaGame.objects.get(match=match).status, ArenaGame.COUNTDOWN)
        self.assertEqual(self.post_json(url, {'action': 'explode'}).status_code, 400)
        self.assertEqual(self.post_json(url, {'action': 'declare', 'game': 'x', 'slot': 'a'}).status_code, 400)

    def test_searches(self):
        self.login(self.organizer)
        users = self.client.get(reverse('arena_manage_users') + '?term=ali').json()['results']
        self.assertEqual([u['username'] for u in users], ['alice'])
        problems = self.client.get(reverse('arena_manage_problems') + '?term=secret&a=%d' % self.alice.profile.pk)
        self.assertEqual([p['code'] for p in problems.json()['results']], [SECRET_CODE])

    def test_screens_from_the_panel(self):
        self.login(self.organizer)
        self.client.get(reverse('arena_manage'))
        self.assertEqual(ArenaScreen.objects.count(), 0, 'opening the panel writes nothing')
        self.battle()
        self.assertEqual(ArenaScreen.objects.count(), 1, 'the first battle comes with a screen')
        screen = ArenaScreen.objects.get()
        url = reverse('arena_manage_screen_action')
        self.assertEqual(self.post_json(url, {'action': 'create', 'name': 'Segunda'}).status_code, 200)
        self.assertEqual(self.post_json(url, {'action': 'show', 'screen': screen.pk, 'mode': 'message',
                                              'message': 'Hola'}).status_code, 200)
        self.assertEqual(self.post_json(url, {'action': 'show', 'screen': 'x', 'mode': 'message'}).status_code, 400)
        self.assertEqual(self.post_json(url, {'action': 'show', 'screen': screen.pk, 'mode': 'weird'}).status_code,
                         400)


class RenderTests(ArenaViewTestCase):
    def test_pages_render_in_every_phase(self):
        self.login(self.organizer)
        self.assertEqual(self.client.get(reverse('arena_manage')).status_code, 200)
        self.assertEqual(self.client.get(reverse('arena_manage_banks')).status_code, 200)
        match = self.battle()
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('arena_home')).status_code, 200)
        game = skip_countdown(start(self.organizer, match))
        page = self.client.get(reverse('arena_home'))
        self.assertEqual(page.status_code, 200)
        self.assertIn('ace.js', page.content.decode('utf-8'))
        self.login(self.organizer)
        self.assertEqual(self.client.get(reverse('arena_manage_match', args=[match.pk])).status_code, 200)
        grade(submit(self.alice, game))
        self.login(self.alice)
        self.assertEqual(self.client.get(reverse('arena_state')).json()['phase'], 'decided')
        self.assertEqual(self.client.get(reverse('arena_home')).status_code, 200)
        self.login(self.organizer)
        self.assertEqual(self.client.get(reverse('arena_manage_match', args=[match.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('arena_manage_match_state', args=[match.pk])).status_code, 200)

    def test_tournament_pages(self):
        from judge.arena import tournament as arena_tournament
        self.login(self.organizer)
        response = self.post_json(reverse('arena_manage_tournament_create'), {
            'name': 'Copa de prueba', 'participants': [self.alice.profile.pk, self.bob.profile.pk,
                                                       self.carol.profile.pk], 'third_place': False})
        self.assertEqual(response.status_code, 200, response.content)
        slug = response.json()['slug']
        self.assertEqual(self.client.get(reverse('arena_manage_tournament', args=[slug])).status_code, 200)
        action = reverse('arena_manage_tournament_action', args=[slug])
        self.assertEqual(self.post_json(action, {'action': 'generate'}).status_code, 200)
        self.assertEqual(self.post_json(action, {'action': 'round', 'number': 2, 'best_of': 3}).status_code, 200)
        self.assertEqual(self.post_json(action, {'action': 'start'}).status_code, 200)
        state = self.client.get(reverse('arena_manage_tournament_state', args=[slug])).json()
        self.assertEqual(state['tournament']['status'], 'running')
        self.assertEqual(len(state['bracket']['rounds']), 2)
        self.assertEqual(self.client.get(reverse('arena_manage_tournament', args=[slug])).status_code, 200)
        self.assertIsNotNone(arena_tournament)

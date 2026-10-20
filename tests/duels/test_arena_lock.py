"""Players are kept inside the arena while their battle lasts, and nothing else of the site is affected."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from arena_testutil import duel, organizer, online_judge, player, problem
from judge.arena import engine

AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
PAGE = {'HTTP_ACCEPT': 'text/html,application/xhtml+xml'}


class LockTests(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer()
        cls.alice = player('alice')
        cls.bob = player('bob')
        cls.carol = player('carol')
        cls.problem = problem('lockprob', public=True)
        online_judge(cls.problem)

    def setUp(self):
        cache.clear()
        with self.captureOnCommitCallbacks(execute=True):
            self.match = duel(self.organizer, self.alice, self.bob, [self.problem])

    def test_pages_send_the_player_back(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse('problem_list'), **PAGE)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/duelos/')
        self.assertEqual(self.client.get('/', **PAGE)['Location'], '/duelos/')
        self.assertEqual(self.client.get(reverse('admin:index'), **PAGE)['Location'], '/duelos/')

    def test_other_requests_get_a_clear_refusal(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse('problem_list'), **AJAX)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['arena'], '/duelos/')
        self.assertEqual(self.client.post(reverse('problem_list')).status_code, 403)

    def test_what_the_arena_needs_stays_open(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('arena_home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('arena_state')).status_code, 200)
        self.assertNotEqual(self.client.get(reverse('custom_test_run')).status_code, 403)
        self.assertNotEqual(self.client.get(reverse('announcements_active')).status_code, 403)
        self.assertNotEqual(self.client.get(reverse('team_invitation_count')).status_code, 403)
        self.assertNotEqual(self.client.get('/favicon-32x32.png').status_code, 403)
        self.assertNotEqual(self.client.get(reverse('language_template_ajax') + '?id=1').status_code, 403)

    def test_everybody_else_is_untouched(self):
        self.client.force_login(self.carol)
        self.assertEqual(self.client.get(reverse('problem_list'), **PAGE).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('problem_list'), **PAGE).status_code, 200)

    def test_released_when_the_battle_is_decided(self):
        with self.captureOnCommitCallbacks(execute=True):
            engine.walkover(self.organizer.profile, self.match.pk, 'a')
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('problem_list'), **PAGE).status_code, 200)

    def test_a_missing_cache_entry_lets_everything_through_until_rebuilt(self):
        cache.delete(engine.LOCK_KEY)
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse('problem_list'), **PAGE).status_code, 200)
        # The arena page itself rebuilds it.
        self.client.get(reverse('arena_state'))
        self.assertEqual(self.client.get(reverse('problem_list'), **PAGE).status_code, 302)

    def test_a_broken_cache_never_breaks_the_site(self):
        # Only the cache the lock reads breaks; sessions keep working.
        for user in (self.alice, self.carol):
            self.client.force_login(user)
            with patch('judge.arena.lock.cache') as broken:
                broken.get.side_effect = RuntimeError('cache down')
                self.assertEqual(self.client.get(reverse('problem_list'), **PAGE).status_code, 200)
                self.assertTrue(broken.get.called)

    def test_not_locked_before_being_called(self):
        from judge.arena import tournament as arena_tournament
        dave, eve = player('dave'), player('eve')
        tournament = arena_tournament.create_tournament(self.organizer.profile, 'Copa', [dave.profile, eve.profile])
        with self.captureOnCommitCallbacks(execute=True):
            arena_tournament.generate_bracket(self.organizer.profile, tournament.pk)
            arena_tournament.start_tournament(self.organizer.profile, tournament.pk)
        engine.refresh_lock()
        self.client.force_login(dave)
        self.assertEqual(self.client.get(reverse('problem_list'), **PAGE).status_code, 200)

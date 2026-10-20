"""The three small changes outside the arena: contest lists, the normal submit page and joining a contest."""
from django.contrib.auth.models import AnonymousUser, Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from arena_testutil import duel, grade, organizer, online_judge, player, problem, skip_countdown, start, submit, \
    tick
from judge.contest_teams import join_attempt
from judge.models import Contest
from judge.models.tests.util import create_contest, create_user

PAGE = {'HTTP_ACCEPT': 'text/html'}


class CoreGuardTests(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer()
        cls.alice = player('alice')
        cls.bob = player('bob')
        cls.carol = player('carol')
        cls.public = problem('guardprob', public=True)
        online_judge(cls.public)
        cls.staff = create_user(username='staffviewer', is_staff=True)
        cls.staff.user_permissions.add(Permission.objects.get(codename='see_private_contest'))

    def setUp(self):
        cache.clear()

    def live(self):
        match = duel(self.organizer, self.alice, self.bob, [self.public])
        return match, skip_countdown(start(self.organizer, match), seconds_ago=30)

    def test_contest_lists_never_show_arena_contests(self):
        match, game = self.live()
        hidden = create_contest(key='normalhidden', is_visible=False)
        visible = create_contest(key='normalvisible', is_visible=True)
        staff_keys = set(Contest.get_visible_contests(self.staff).values_list('key', flat=True))
        self.assertNotIn(game.contest.key, staff_keys)
        self.assertIn(hidden.key, staff_keys)
        self.assertIn(visible.key, staff_keys)
        for user in (AnonymousUser(), self.alice, self.carol):
            self.assertNotIn(game.contest.key, set(Contest.get_visible_contests(user).values_list('key', flat=True)))
        self.client.force_login(self.staff)
        self.assertNotIn(game.contest.key, self.client.get(reverse('contest_list')).content.decode('utf-8'))

    def test_normal_submit_page_is_closed_during_a_game(self):
        match, game = self.live()
        # Without the cached lock set the middleware lets the request through: the page itself refuses.
        cache.clear()
        self.client.force_login(self.alice)
        url = reverse('problem_submit', args=[self.public.code])
        self.assertEqual(self.client.get(url, **PAGE).status_code, 403)
        self.assertEqual(self.client.post(url, {'source': 'print(1)', 'language': 8}).status_code, 403)
        self.client.force_login(self.carol)
        self.assertEqual(self.client.get(url, **PAGE).status_code, 200)
        grade(submit(self.alice, game))
        self.assertEqual(tick(game).status, 'finished')
        cache.clear()
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(url, **PAGE).status_code, 200)

    def test_nobody_joins_an_arena_contest(self):
        match, game = self.live()
        with self.assertRaises(ValidationError):
            join_attempt(game.contest_id, self.staff.profile, 'individual')

    def test_arena_submissions_stay_out_of_the_lists(self):
        match, game = self.live()
        submission = submit(self.alice, game)
        self.client.force_login(self.carol)
        content = self.client.get(reverse('all_submissions')).content.decode('utf-8')
        self.assertNotIn('/submission/%d"' % submission.pk, content)

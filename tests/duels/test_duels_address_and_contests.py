"""Duels at /duelos/, with /arena/ forwarding; their hidden contests out of the
lists of contests, with their own entry in the «Battles» section of the admin, and deleted with their game; and the
player's page under the site's own navigation bar.

The look of the bar (same height, colours, logo and tabs as the rest of the site, the battle in it while held) is
CSS and JavaScript; here only that the pages are built for it.
"""
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

from arena_testutil import duel, organizer, online_judge, player, problem, skip_countdown, start, submit
from judge.arena import engine
from judge.arena.lock import LOCK_KEY
from judge.models import Contest, ContestParticipation, Submission
from judge.models.arena import ArenaGame
from judge.models.tests.util import create_contest, create_user

OWNER = 'site_owner'
PAGE = {'HTTP_ACCEPT': 'text/html'}


class DuelsTestCase(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer('orgduelos')
        cls.owner = create_user(username=OWNER, is_staff=True, is_superuser=True)
        cls.ana, cls.beto = player('anaduelos'), player('betoduelos')
        cls.problems = [problem('duelos%d' % number) for number in range(1, 4)]
        online_judge(*cls.problems)

    def setUp(self):
        cache.clear()

    def played_game(self, title='Prueba', players=None):
        """A battle with its first game started: its hidden contest exists. A player can only be in one battle at a
        time, so each battle of a test gets its own pair."""
        a, b = players or (self.ana, self.beto)
        match = duel(self.organizer, a, b, self.problems[:1], title=title, call=False)
        game = ArenaGame.objects.get(match=match)
        start(self.organizer, match, game)
        game.refresh_from_db()
        return match, game

    def orphan(self):
        """A hidden contest whose game was deleted, as the admin left them until today."""
        _match, game = self.played_game('Huérfana', (player('huerfa'), player('huerfb')))
        contest = game.contest
        ArenaGame.objects.filter(pk=game.pk).delete()
        return contest


# ---------------------------------------------------------------------------------------------------------------
# /duelos/, and /arena/ forwarding there
# ---------------------------------------------------------------------------------------------------------------

class AddressTests(DuelsTestCase):
    def test_the_duels_live_at_duelos(self):
        self.assertEqual(reverse('arena_home'), '/duelos/')
        self.assertEqual(reverse('arena_manage'), '/duelos/manage/')
        self.assertEqual(reverse('arena_screen', args=['x' * 43]), '/duelos/screen/%s/' % ('x' * 43))

    def test_old_addresses_forward_keeping_path_query_and_method(self):
        self.client.force_login(self.ana)
        for old, new in (('/arena/', '/duelos/'), ('/arena', '/duelos/'),
                         ('/arena/screen/%s/?view=code-a' % ('y' * 43), '/duelos/screen/%s/?view=code-a' % ('y' * 43)),
                         ('/arena/manage/guide/', '/duelos/manage/guide/')):
            response = self.client.get(old)
            self.assertEqual((response.status_code, response['Location']), (307, new), old)
        posted = self.client.post('/arena/game/5/code', '{}', content_type='application/json')
        self.assertEqual((posted.status_code, posted['Location']), (307, '/duelos/game/5/code'))

    def test_a_page_left_open_keeps_working_through_the_old_address(self):
        match = duel(self.organizer, self.ana, self.beto, self.problems[:1], call=True)
        self.client.force_login(self.ana)
        state = self.client.get('/arena/state', follow=True)
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()['match']['id'], match.pk)
        self.assertEqual(state.redirect_chain, [('/duelos/state', 307)])
        game = ArenaGame.objects.get(match=match)
        start(self.organizer, match, game)
        # The player page sends its code with POST: 307 keeps the method and the body.
        sent = self.client.post('/arena/game/%d/code' % game.pk, {'text': 'print(1)'}, content_type='application/json',
                                follow=True)
        self.assertEqual(sent.redirect_chain, [('/duelos/game/%d/code' % game.pk, 307)])
        self.assertNotEqual(sent.status_code, 405)

    def test_nothing_forwards_outside_the_duels(self):
        for old in ('/arena//evil.example/', '/arena/..%2F..%2Fadmin/'):
            self.assertTrue(self.client.get(old)['Location'].startswith('/duelos/'), old)
        self.assertEqual(self.client.get('/arenas/').status_code, 404)

    def test_a_held_player_is_sent_to_duelos(self):
        cache.set(LOCK_KEY, {self.ana.profile.pk: 9999999999})
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get('/', **PAGE)['Location'], '/duelos/')
        blocked = self.client.get('/problems/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual((blocked.status_code, blocked.json()['arena']), (403, '/duelos/'))
        # Both addresses of the duels stay open to them: the old one only forwards.
        self.assertEqual(self.client.get('/duelos/', **PAGE).status_code, 200)
        self.assertEqual(self.client.get('/arena/', **PAGE).status_code, 307)

    def test_new_contests_say_duelo(self):
        _match, game = self.played_game()
        self.assertTrue(game.contest.name.startswith('Duelo: '), game.contest.name)
        self.assertTrue(game.contest.summary.startswith('Arena game '))


# ---------------------------------------------------------------------------------------------------------------
# The hidden contests: out of the lists, in their own place, gone with their game
# ---------------------------------------------------------------------------------------------------------------

@override_settings(CPC_SERVER_OWNERS=(OWNER,))
class DuelContestsTests(DuelsTestCase):
    def test_which_contests_are_duels(self):
        _match, game = self.played_game()
        orphan = self.orphan()
        lookalike = create_contest(key='arena77', name='Arena de verano', is_visible=False)
        visible = create_contest(key='arena78', name='Visible', is_visible=True, summary='Arena game 1 of match 1')
        duels = set(Contest.objects.filter(Contest.duel_contests()).values_list('key', flat=True))
        self.assertEqual(duels, {game.contest.key, orphan.key})
        self.assertNotIn(lookalike.key, duels)
        self.assertNotIn(visible.key, duels)

    def test_the_list_of_contests_never_shows_them(self):
        _match, game = self.played_game()
        orphan = self.orphan()
        own = create_contest(key='propio', name='Concurso propio', is_visible=False)
        self.client.force_login(self.owner)
        page = self.client.get(reverse('contest_list')).content.decode('utf-8')
        self.assertIn(own.name, page)
        for contest in (game.contest, orphan):
            self.assertNotIn(contest.key, page)
            self.assertNotIn(contest.name, page)
        self.assertNotIn(orphan, Contest.get_visible_contests(self.owner))
        self.assertIn(own, Contest.get_visible_contests(self.owner))

    def test_the_admin_list_of_contests_leaves_them_out_unless_asked(self):
        _match, game = self.played_game()
        orphan = self.orphan()
        own = create_contest(key='propio', name='Concurso propio')
        self.client.force_login(self.owner)
        url = reverse('admin:judge_contest_changelist')

        def listed(query=None):
            return set(self.client.get(url, query or {}).context['cl'].queryset.values_list('key', flat=True))
        self.assertEqual(listed(), {own.key})
        self.assertEqual(listed({'duelos': 'si'}), {game.contest.key, orphan.key})
        self.assertEqual(listed({'duelos': 'todos'}), {own.key, game.contest.key, orphan.key})
        # Their own changes still open, as from the list of games.
        self.assertEqual(self.client.get(reverse('admin:judge_contest_change', args=[orphan.pk])).status_code, 200)

    def test_the_admin_list_of_participations_too(self):
        _match, game = self.played_game()
        own = create_contest(key='propio', name='Concurso propio')
        ContestParticipation.objects.create(contest=own, user=self.ana.profile)
        self.client.force_login(self.owner)
        url = reverse('admin:judge_contestparticipation_changelist')
        default = self.client.get(url).context['cl'].queryset
        self.assertEqual(set(default.values_list('contest_id', flat=True)), {own.pk})
        duels = self.client.get(url, {'duelos': 'si'}).context['cl'].queryset
        self.assertEqual(set(duels.values_list('contest_id', flat=True)), {game.contest_id})

    def test_the_battles_section_has_their_entry(self):
        menu = next(item for item in settings.WPADMIN['admin']['custom_menu']
                    if isinstance(item, dict) and item['model'] == 'judge.ArenaPanel')
        entry = next(child for child in menu['children'] if isinstance(child, dict))
        self.assertEqual(entry['url'], '/admin/judge/contest/?duelos=si')
        self.client.force_login(self.owner)
        page = self.client.get(reverse('admin:index')).content.decode('utf-8')
        section = page[page.index('fa-bolt'):page.index('fa-bell', page.index('fa-bolt'))]
        self.assertIn('/admin/judge/contest/?duelos=si', section)
        self.assertTrue('Concursos de duelos' in section or 'Duel contests' in section, section[:2000])

    def test_deleting_a_game_takes_its_contest(self):
        match, game = self.played_game()
        contest = game.contest
        skip_countdown(game)
        sent = submit(self.ana, game)
        ArenaGame.objects.filter(pk=game.pk).update(status=ArenaGame.FINISHED, winner_slot='a')
        self.client.force_login(self.owner)
        url = reverse('admin:judge_arenagame_delete', args=[game.pk])
        confirmation = self.client.get(url).content.decode('utf-8')
        self.assertIn(contest.name, confirmation)
        self.assertEqual(self.client.post(url, {'post': 'yes'}).status_code, 302)
        self.assertFalse(ArenaGame.objects.filter(pk=game.pk).exists())
        self.assertFalse(Contest.objects.filter(pk=contest.pk).exists())
        # The submission itself stays in the player's history.
        self.assertTrue(Submission.objects.filter(pk=sent.pk, contest_object__isnull=True).exists())
        # And then the battle can go.
        self.client.post(reverse('admin:judge_arenamatch_delete', args=[match.pk]), {'post': 'yes'})
        self.assertFalse(match.__class__.objects.filter(pk=match.pk).exists())

    def test_deleting_games_from_their_list_takes_their_contests(self):
        games = [self.played_game('Prueba %d' % number, (player('lista%da' % number), player('lista%db' % number)))[1]
                 for number in range(2)]
        for game in games:
            ArenaGame.objects.filter(pk=game.pk).update(status=ArenaGame.FINISHED)
        self.client.force_login(self.owner)
        response = self.client.post(reverse('admin:judge_arenagame_changelist'),
                                    {'action': 'delete_selected', '_selected_action': [g.pk for g in games],
                                     'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Contest.objects.filter(pk__in=[g.contest_id for g in games]).exists())

    def test_only_the_owner_deletes_and_never_a_game_in_play(self):
        _match, game = self.played_game()
        other = create_user(username='otrosuperduelos', is_staff=True, is_superuser=True)
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse('admin:judge_arenagame_delete', args=[game.pk]),
                                          {'post': 'yes'}).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse('admin:judge_arenagame_delete', args=[game.pk]),
                                          {'post': 'yes'}).status_code, 403)
        self.assertTrue(Contest.objects.filter(pk=game.contest_id).exists())

    def test_orphans_go_with_the_purge_of_the_list_of_contests(self):
        orphan = self.orphan()
        self.client.force_login(self.owner)
        response = self.client.post(reverse('admin:judge_contest_changelist') + '?duelos=si',
                                    {'action': 'purge_contests', '_selected_action': [orphan.pk], 'confirmar': 'si'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Contest.objects.filter(pk=orphan.pk).exists())


# ---------------------------------------------------------------------------------------------------------------
# The name, and the page under the site's own bar
# ---------------------------------------------------------------------------------------------------------------

class NameAndBarTests(DuelsTestCase):
    def test_spanish_says_duelos_and_english_duels(self):
        for language, title, games in (('es', 'Duelos', 'partidas de duelos'), ('en', 'Duels', 'duel games')):
            with translation.override(language):
                self.assertEqual(str(translation.gettext('Arena')), title)
                self.assertEqual(str(ArenaGame._meta.verbose_name_plural), games)

    def test_the_player_page_keeps_the_site_bar(self):
        self.client.force_login(self.ana)
        page = self.client.get(reverse('arena_home'), HTTP_ACCEPT_LANGUAGE='es').content.decode('utf-8')
        self.assertTrue('<title>Duelos' in page or '<title>Duels' in page)
        self.assertIn('id="navigation"', page)
        self.assertNotIn('#navigation, footer', page)
        self.assertNotIn('#navigation { display: none', page)
        self.assertIn('html.ar-locked #nav-list', page)
        self.assertIn("classList.toggle('ar-locked', false)", page)

    def test_a_held_player_gets_the_bar_ready_for_the_battle(self):
        duel(self.organizer, self.ana, self.beto, self.problems[:1], call=True)
        self.client.force_login(self.ana)
        page = self.client.get(reverse('arena_home')).content.decode('utf-8')
        self.assertIn("classList.toggle('ar-locked', true)", page)

    def test_the_projector_still_hides_the_bar(self):
        screen = engine.create_screen(self.organizer.profile, 'Proyector')
        page = self.client.get(reverse('arena_screen', args=[screen.token])).content.decode('utf-8')
        self.assertIn('#navigation { display: none !important; }', page)
        self.assertIn('class="site-logo"', page)

    def test_the_guide_speaks_of_duelos(self):
        self.client.force_login(self.organizer)
        page = self.client.get(reverse('arena_manage_guide')).content.decode('utf-8')
        self.assertIn('<code>/duelos/</code>', page)
        self.assertNotIn('la arena', page)

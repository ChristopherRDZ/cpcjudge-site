"""Duels: the games in the Battles section of the admin, with filters to delete the games of a test at
once and no deleting a game that is being played; big photos for the waiting room and the countdown; and
"Use as input" over the samples of the player page.

The projector itself (circles of the series, green frame, countdown between the photos) is JavaScript; here
only that the pages are built with it.
"""
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from arena_testutil import duel, organizer, online_judge, player, problem, skip_countdown, start
from judge.arena import engine, serialize
from judge.models import Contest
from judge.models.arena import ArenaGame, ArenaMatch, ArenaTournament
from judge.models.tests.util import create_user

OWNER = 'site_owner'


# ---------------------------------------------------------------------------------------------------------------
# Admin: «Arena games» next to the battles
# ---------------------------------------------------------------------------------------------------------------

@override_settings(CPC_SERVER_OWNERS=(OWNER,))
class ArenaGamesInAdminTests(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer('orgajustes')
        cls.owner = create_user(username=OWNER, is_staff=True, is_superuser=True)
        cls.other_superuser = create_user(username='otrosuper', is_staff=True, is_superuser=True)
        cls.ana, cls.beto, cls.carla = player('anaaj'), player('betoaj'), player('carlaaj')
        cls.problems = [problem('adminaj%d' % number) for number in range(1, 4)]
        online_judge(*cls.problems)

    def setUp(self):
        cache.clear()

    def battle(self, a, b, title):
        return duel(self.organizer, a, b, self.problems[:1], title=title, call=False)

    def test_the_battles_section_lists_the_games(self):
        menu = next(item for item in settings.WPADMIN['admin']['custom_menu']
                    if isinstance(item, dict) and item['model'] == 'judge.ArenaPanel')
        self.assertIn('judge.ArenaGame', menu['children'])
        self.client.force_login(self.owner)
        page = self.client.get(reverse('admin:index')).content.decode('utf-8')
        section = page[page.index('fa-bolt'):page.index('fa-bell', page.index('fa-bolt'))]
        self.assertIn(reverse('admin:judge_arenagame_changelist'), section)

    def test_organizers_see_the_list_of_games(self):
        self.client.force_login(self.organizer)
        page = self.client.get(reverse('admin:index')).content.decode('utf-8')
        self.assertIn(reverse('admin:judge_arenagame_changelist'), page)
        self.assertEqual(self.client.get(reverse('admin:judge_arenagame_changelist')).status_code, 200)

    def test_the_games_are_found_by_player_and_by_tournament(self):
        first = self.battle(self.ana, self.beto, 'Prueba uno')
        second = self.battle(self.beto, self.carla, 'Prueba dos')
        self.client.force_login(self.owner)
        url = reverse('admin:judge_arenagame_changelist')
        by_player = self.client.get(url, {'q': 'carlaaj'})
        self.assertEqual(list(by_player.context['cl'].queryset), list(ArenaGame.objects.filter(match=second)))
        by_title = self.client.get(url, {'q': 'Prueba uno'})
        self.assertEqual(list(by_title.context['cl'].queryset), list(ArenaGame.objects.filter(match=first)))
        cup = ArenaTournament.objects.create(name='Copa de prueba', slug='copaprueba')
        ArenaMatch.objects.filter(pk=first.pk).update(tournament=cup)
        page = self.client.get(url)
        self.assertIn('match__tournament__id__exact=%d' % cup.pk, page.content.decode('utf-8'))
        by_tournament = self.client.get(url, {'match__tournament__id__exact': cup.pk})
        self.assertEqual(list(by_tournament.context['cl'].queryset), list(ArenaGame.objects.filter(match=first)))
        self.assertIn('Copa de prueba', by_tournament.content.decode('utf-8'))

    def test_a_test_is_deleted_games_first_then_the_battle(self):
        match = self.battle(self.ana, self.beto, 'Prueba')
        game = ArenaGame.objects.get(match=match)
        start(self.organizer, match, game)
        ArenaGame.objects.filter(pk=game.pk).update(status=ArenaGame.FINISHED, winner_slot='a')
        ArenaMatch.objects.filter(pk=match.pk).update(status=ArenaMatch.DECIDED, winner_slot='a')
        self.client.force_login(self.owner)

        # The battle alone cannot go: its games are protected, as before.
        blocked = self.client.post(reverse('admin:judge_arenamatch_delete', args=[match.pk]), {'post': 'yes'})
        self.assertEqual(blocked.status_code, 200)
        self.assertTrue(ArenaMatch.objects.filter(pk=match.pk).exists())

        # The games of the test, all at once from their list, and then the battle.
        response = self.client.post(reverse('admin:judge_arenagame_changelist'),
                                    {'action': 'delete_selected', '_selected_action': [game.pk], 'post': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ArenaGame.objects.filter(match=match).exists())
        self.client.post(reverse('admin:judge_arenamatch_delete', args=[match.pk]), {'post': 'yes'})
        self.assertFalse(ArenaMatch.objects.filter(pk=match.pk).exists())
        # Deleting the game takes its hidden contest with it.
        self.assertFalse(Contest.objects.filter(key__startswith='arena').exists())

    def test_a_game_being_played_cannot_be_deleted(self):
        match = self.battle(self.ana, self.beto, 'En juego')
        game = ArenaGame.objects.get(match=match)
        start(self.organizer, match, game)
        self.client.force_login(self.owner)
        for status in ArenaGame.ACTIVE_STATUSES:
            ArenaGame.objects.filter(pk=game.pk).update(status=status)
            self.assertEqual(self.client.post(reverse('admin:judge_arenagame_delete', args=[game.pk]),
                                              {'post': 'yes'}).status_code, 403)
            self.client.post(reverse('admin:judge_arenagame_changelist'),
                             {'action': 'delete_selected', '_selected_action': [game.pk], 'post': 'yes'})
            self.assertTrue(ArenaGame.objects.filter(pk=game.pk).exists(), status)

    def test_deleting_stays_with_the_owner(self):
        match = self.battle(self.ana, self.beto, 'Ajena')
        game = ArenaGame.objects.get(match=match)
        for user in (self.organizer, self.other_superuser):
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse('admin:judge_arenagame_delete', args=[game.pk]),
                                              {'post': 'yes'}).status_code, 403)
        self.assertTrue(ArenaGame.objects.filter(pk=game.pk).exists())


# ---------------------------------------------------------------------------------------------------------------
# Bigger photos
# ---------------------------------------------------------------------------------------------------------------

class BigPhotosTests(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.organizer = organizer('orgfotos')
        cls.ana, cls.beto = player('anafotos'), player('betofotos')
        cls.problem = problem('fotosaj')
        cls.more = [problem('fotosaj2'), problem('fotosaj3')]
        online_judge(cls.problem, *cls.more)

    def setUp(self):
        cache.clear()

    def test_the_player_page_and_the_projector_ask_for_big_photos(self):
        match = duel(self.organizer, self.ana, self.beto, [self.problem], call=True)
        me = serialize.player_state(self.ana.profile, 'es')['me']
        self.assertIn('s=512', me['avatar'])
        players = serialize.screen_match(match, 'es')['players']
        self.assertIn('s=512', players['a']['avatar'])
        self.assertIn('s=512', players['b']['avatar'])
        # The organizer panel keeps its small ones.
        self.assertIn('s=128', serialize.organizer_player(match, 'a', timezone.now())['avatar'])

    def test_the_projector_page_renders_with_the_new_pieces(self):
        match = duel(self.organizer, self.ana, self.beto, [self.problem], best_of=3,
                     games=[[{'id': item.id}] for item in [self.problem] + self.more], call=True)
        screen = engine.create_screen(self.organizer.profile, 'Proyector')
        engine.show_on_screen(self.organizer.profile, screen.pk, 'match', match=match)
        page = self.client.get(reverse('arena_screen', args=[screen.token]) + '?view=combined')
        self.assertEqual(page.status_code, 200)
        text = page.content.decode('utf-8')
        for piece in ('sc-winner', 'fillSeries(dots, match, slot)', "classList.toggle('sc-winner'",
                      "versus(match, A.el('div', {'class': 'sc-count', id: 'sc-count'}))"):
            self.assertIn(piece, text)

    def test_the_player_page_puts_the_countdown_between_the_photos(self):
        self.client.force_login(self.ana)
        duel(self.organizer, self.ana, self.beto, [self.problem], call=True)
        text = self.client.get(reverse('arena_home')).content.decode('utf-8')
        self.assertIn("versus(document.getElementById('ar-count-versus'), data, document.getElementById('ar-count'))",
                      text)
        self.assertIn('#ar-countdown .ar-fighter img', text)

    def test_the_examples_of_a_statement_can_go_into_try_with_input(self):
        # The page adds «Use as input» over each `pre > code` inside `.content-description` of a statement, and only
        # over the inputs of a table of samples: what the statement of a game is made of must keep that shape.
        sample = problem('fotosej')
        sample.description = 'Suma.\n\n## Ejemplo\n\n```\n3 4\n```\n\n||input\n6\nbanana\n||output\n6\n||end\n'
        sample.save()
        online_judge(sample)
        self.client.force_login(self.ana)
        match = duel(self.organizer, self.ana, self.beto, [sample], call=True)
        game = ArenaGame.objects.get(match=match)
        start(self.organizer, match, game)
        skip_countdown(game)
        page = self.client.get(reverse('arena_home')).content.decode('utf-8')
        self.assertIn('addInputButtons(slot);', page)
        self.assertIn('"Usar como entrada"' if settings.LANGUAGE_CODE == 'es' else '"Use as input"', page)
        html = self.client.get(reverse('arena_statement', args=[game.pk, 0])).content.decode('utf-8')
        description = html[html.index('class="content-description'):]
        self.assertRegex(description, r'<pre[^>]*><code[^>]*>3 4')
        self.assertRegex(description, r'<td class="sample-input"><pre[^>]*><code>6\nbanana</code></pre></td>')

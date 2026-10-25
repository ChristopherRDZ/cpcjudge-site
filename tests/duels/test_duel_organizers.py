"""Duels: who organizes which battles. Organizers manage their own battles and the ones where they are
co-organizers; manage_all_arena sees and manages all of them. Judging is replaced by nothing."""
import json

from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from arena_testutil import NoJudging, duel, online_judge, organizer, player, problem, skip_countdown, start
from judge.arena import access, engine, tournament as arena_tournament
from judge.models.arena import ArenaBankProblem, ArenaMatch, ArenaScreen, ArenaTournament
from judge.models.tests.util import create_user


def grant(user, *codenames):
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


def all_organizer(username):
    user = create_user(username=username, is_staff=True)
    return grant(user, 'manage_all_arena', 'see_private_problem')


def post_json(client, url, data):
    return client.post(url, json.dumps(data), content_type='application/json')


# ---------------------------------------------------------------------------------------------------------------
# Duels
# ---------------------------------------------------------------------------------------------------------------

class ArenaFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = organizer('organa')
        cls.beto = organizer('orgbeto')
        cls.chief = all_organizer('orgjefe')
        cls.root = create_user(username='rootduel', is_staff=True, is_superuser=True)
        cls.nobody = create_user(username='sinpermiso', is_staff=True)
        cls.p1, cls.p2, cls.p3, cls.p4 = player('jug1'), player('jug2'), player('jug3'), player('jug4')
        cls.prob_a = problem('permda')
        cls.prob_b = problem('permdb')
        cls.secret = problem('permsecreto')   # private: seen by organizers with see_private_problem only
        online_judge(cls.prob_a, cls.prob_b, cls.secret)

    def setUp(self):
        cache.clear()

    def duel_of(self, user, a, b):
        return duel(user, a, b, [self.prob_a], call=False)

    def tournament_of(self, user, name, players):
        return arena_tournament.create_tournament(user.profile, name, [p.profile for p in players])


class ArenaAccessTests(ArenaFixture):
    def test_own_battles_only_with_the_old_permission(self):
        mine = self.duel_of(self.ana, self.p1, self.p2)
        theirs = self.duel_of(self.beto, self.p3, self.p4)
        self.assertTrue(access.can_manage_match(self.ana, mine))
        self.assertFalse(access.can_manage_match(self.ana, theirs))
        self.assertEqual(set(access.matches_for(self.ana)), {mine})

    def test_the_new_permission_and_superusers_see_everything(self):
        mine = self.duel_of(self.ana, self.p1, self.p2)
        theirs = self.duel_of(self.beto, self.p3, self.p4)
        for user in (self.chief, self.root):
            self.assertTrue(access.manages_all(user))
            self.assertEqual(set(access.matches_for(user)), {mine, theirs})

    def test_the_new_permission_works_by_itself(self):
        self.assertFalse(self.chief.has_perm('judge.manage_arena'))
        self.assertTrue(access.can_manage(self.chief))

    def test_without_a_permission_nothing(self):
        self.duel_of(self.ana, self.p1, self.p2)
        self.assertFalse(access.can_manage(self.nobody))
        self.assertFalse(access.matches_for(self.nobody).exists())

    def test_battles_of_a_tournament_belong_to_its_organizers(self):
        tournament = self.tournament_of(self.ana, 'Copa permisos', [self.p1, self.p2, self.p3, self.p4])
        matches = list(ArenaMatch.objects.filter(tournament=tournament))
        self.assertTrue(matches)
        self.assertFalse(any(access.can_manage_match(self.beto, match) for match in matches))
        tournament.organizers.add(self.beto.profile)
        self.assertTrue(all(access.can_manage_match(self.beto, match) for match in matches))
        self.assertTrue(access.can_manage_tournament(self.beto, tournament))

    def test_migration_renamed_the_old_permissions_and_added_the_new_ones(self):
        codenames = ('manage_arena', 'manage_all_arena', 'send_mass_email', 'edit_all_mass_email')
        names = dict(Permission.objects.filter(content_type__app_label='judge', codename__in=codenames)
                     .values_list('codename', 'name'))
        self.assertEqual(names, {'manage_arena': 'Organize own arena battles',
                                 'manage_all_arena': 'Organize all arena battles',
                                 'send_mass_email': 'Write and send own emails to users',
                                 'edit_all_mass_email': 'Edit and send all emails to users'})


class ArenaPanelTests(ArenaFixture):
    def test_dashboard_lists_only_what_one_organizes(self):
        mine = self.duel_of(self.ana, self.p1, self.p2)
        theirs = self.duel_of(self.beto, self.p3, self.p4)
        self.tournament_of(self.beto, 'Copa de beto', [self.p1, self.p2])
        self.client.force_login(self.ana)
        state = self.client.get(reverse('arena_manage_overview')).json()
        self.assertEqual([row['id'] for row in state['duels']], [mine.pk])
        self.assertEqual(state['tournaments'], [])
        page = self.client.get(reverse('arena_manage')).content.decode()
        self.assertIn('id="am-own-only"', page)
        self.client.force_login(self.chief)
        state = self.client.get(reverse('arena_manage_overview')).json()
        self.assertEqual({row['id'] for row in state['duels']}, {mine.pk, theirs.pk})
        self.assertEqual(len(state['tournaments']), 1)
        self.assertNotIn('id="am-own-only"', self.client.get(reverse('arena_manage')).content.decode())

    def test_somebody_elses_battle_does_not_exist(self):
        theirs = self.duel_of(self.beto, self.p3, self.p4)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('arena_manage_match', args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('arena_manage_match_state', args=[theirs.pk])).status_code, 404)
        response = post_json(self.client, reverse('arena_manage_match_action', args=[theirs.pk]),
                             {'action': 'cancel', 'version': theirs.version})
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertNotEqual(theirs.status, ArenaMatch.CANCELLED)
        response = post_json(self.client, reverse('arena_manage_draw', args=[theirs.pk]), {'number': 1})
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.chief)
        self.assertEqual(self.client.get(reverse('arena_manage_match', args=[theirs.pk])).status_code, 200)

    def test_live_code_statements_and_submissions_only_of_ones_own_battles(self):
        theirs = duel(self.beto, self.p3, self.p4, [self.prob_a], call=True)
        game = skip_countdown(start(self.beto, theirs))
        with NoJudging():
            submission = engine.submit(self.p3.profile, self.p3, game.pk, 0, 'PY3', 'print(1)', '')
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('arena_manage_game_code', args=[game.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('arena_manage_submission', args=[submission.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('arena_statement', args=[game.pk, 0])).status_code, 404)
        self.client.force_login(self.beto)
        self.assertEqual(self.client.get(reverse('arena_manage_game_code', args=[game.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('arena_manage_submission', args=[submission.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('arena_statement', args=[game.pk, 0])).status_code, 200)
        self.client.force_login(self.p3)   # the players keep their statements
        self.assertEqual(self.client.get(reverse('arena_statement', args=[game.pk, 0])).status_code, 200)

    def test_somebody_elses_tournament_does_not_exist(self):
        tournament = self.tournament_of(self.beto, 'Copa ajena', [self.p1, self.p2])
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('arena_manage_tournament', args=[tournament.slug])).status_code, 404)
        response = post_json(self.client, reverse('arena_manage_tournament_action', args=[tournament.slug]),
                             {'action': 'update', 'name': 'Robada', 'version': tournament.version})
        self.assertEqual(response.status_code, 404)
        tournament.refresh_from_db()
        self.assertEqual(tournament.name, 'Copa ajena')

    def test_co_organizers_of_a_tournament(self):
        tournament = self.tournament_of(self.ana, 'Copa compartida', [self.p1, self.p2, self.p3, self.p4])
        url = reverse('arena_manage_tournament_action', args=[tournament.slug])
        self.client.force_login(self.ana)
        # Only people who can organize battles; the creator is left out of the list without an error.
        response = post_json(self.client, url, {'action': 'organizers', 'organizers': [self.p1.profile.pk]})
        self.assertEqual(response.status_code, 400)
        self.assertIn('jug1', response.json()['error'])
        response = post_json(self.client, url, {'action': 'organizers',
                                                'organizers': [self.beto.profile.pk, self.ana.profile.pk]})
        self.assertEqual(response.status_code, 200, response.content)
        state = response.json()['state']
        self.assertEqual(state['organizers']['creator']['username'], 'organa')
        self.assertEqual([row['username'] for row in state['organizers']['list']], ['orgbeto'])
        self.assertEqual(list(tournament.organizers.all()), [self.beto.profile])
        self.client.force_login(self.beto)
        self.assertEqual(self.client.get(reverse('arena_manage_tournament', args=[tournament.slug])).status_code, 200)
        match = ArenaMatch.objects.filter(tournament=tournament).first()
        state = self.client.get(reverse('arena_manage_match_state', args=[match.pk])).json()
        self.assertTrue(state['organizers']['tournament'])
        # The battles of a tournament take their organizers from the tournament.
        response = post_json(self.client, reverse('arena_manage_match_action', args=[match.pk]),
                             {'action': 'organizers', 'organizers': [self.chief.profile.pk]})
        self.assertEqual(response.status_code, 400)

    def test_co_organizers_of_a_quick_battle(self):
        mine = self.duel_of(self.ana, self.p1, self.p2)
        self.client.force_login(self.ana)
        response = post_json(self.client, reverse('arena_manage_match_action', args=[mine.pk]),
                             {'action': 'organizers', 'organizers': [self.beto.profile.pk]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['state']['organizers']['list'][0]['username'], 'orgbeto')
        self.assertFalse(response.json()['state']['organizers']['tournament'])
        self.assertTrue(access.can_manage_match(self.beto, mine))

    def test_user_search_for_organizers_offers_only_who_can_organize(self):
        self.client.force_login(self.ana)
        found = self.client.get(reverse('arena_manage_users'), {'term': 'org', 'organizers': '1'}).json()['results']
        self.assertEqual({row['username'] for row in found}, {'organa', 'orgbeto', 'orgjefe'})
        found = self.client.get(reverse('arena_manage_users'), {'term': 'jug'}).json()['results']
        self.assertEqual(len(found), 4)
        found = self.client.get(reverse('arena_manage_users'), {'term': 'jug', 'organizers': '1'}).json()['results']
        self.assertEqual(found, [])


class ArenaScreenTests(ArenaFixture):
    def test_each_organizer_gets_and_keeps_their_own_projector(self):
        mine = self.duel_of(self.ana, self.p1, self.p2)
        screen_a = ArenaScreen.objects.get(created_by=self.ana.profile)
        self.assertEqual(screen_a.match_id, mine.pk)
        theirs = self.duel_of(self.beto, self.p3, self.p4)
        screen_a.refresh_from_db()
        self.assertEqual(screen_a.match_id, mine.pk, 'a battle of beto took the projector of ana')
        screen_b = ArenaScreen.objects.get(created_by=self.beto.profile)
        self.assertEqual(screen_b.match_id, theirs.pk)

    def test_somebody_elses_screen_cannot_be_moved_renamed_or_regenerated(self):
        self.duel_of(self.ana, self.p1, self.p2)
        theirs = self.duel_of(self.beto, self.p3, self.p4)
        screen_a = ArenaScreen.objects.get(created_by=self.ana.profile)
        screen_b = ArenaScreen.objects.get(created_by=self.beto.profile)
        token = screen_a.token
        self.client.force_login(self.beto)
        url = reverse('arena_manage_screen_action')
        for data in ({'action': 'rename', 'screen': screen_a.pk, 'name': 'mía'},
                     {'action': 'regenerate', 'screen': screen_a.pk},
                     {'action': 'show', 'screen': screen_a.pk, 'mode': 'match', 'match': theirs.pk}):
            self.assertEqual(post_json(self.client, url, data).status_code, 404, data)
        screen_a.refresh_from_db()
        self.assertEqual(screen_a.token, token)
        # Nor can one's own screen show somebody else's battle.
        mine = ArenaMatch.objects.get(created_by=self.ana.profile)
        response = post_json(self.client, url, {'action': 'show', 'screen': screen_b.pk, 'mode': 'match',
                                                'match': mine.pk})
        self.assertEqual(response.status_code, 404)
        response = post_json(self.client, url, {'action': 'rename', 'screen': screen_b.pk, 'name': 'Proyector B'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['name'] for row in response.json()['screens']], ['Proyector B'])

    def test_the_overview_shows_only_ones_own_screens_and_their_links(self):
        self.duel_of(self.ana, self.p1, self.p2)
        self.duel_of(self.beto, self.p3, self.p4)
        self.client.force_login(self.ana)
        screens = self.client.get(reverse('arena_manage_overview')).json()['screens']
        self.assertEqual([row['token'] for row in screens],
                         [ArenaScreen.objects.get(created_by=self.ana.profile).token])


class ArenaBankTests(ArenaFixture):
    def test_banks_are_their_creators(self):
        bank = arena_tournament.create_bank(self.beto.profile, 'Banco de beto')
        self.client.force_login(self.ana)
        page = self.client.get(reverse('arena_manage_banks'), {'bank': bank.pk}).content.decode()
        self.assertNotIn('Banco de beto', page)
        response = post_json(self.client, reverse('arena_manage_bank_action'),
                             {'action': 'problems', 'bank': bank.pk, 'problems': [{'id': self.prob_a.pk}]})
        self.assertEqual(response.status_code, 400)
        response = post_json(self.client, reverse('arena_manage_tournament_create'),
                             {'name': 'Con banco ajeno', 'participants': [self.p1.profile.pk, self.p2.profile.pk],
                              'bank': bank.pk})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ArenaTournament.objects.filter(name='Con banco ajeno').exists())

    def test_saving_a_tournament_keeps_a_bank_somebody_else_gave_it(self):
        tournament = self.tournament_of(self.ana, 'Copa con banco', [self.p1, self.p2])
        bank = arena_tournament.create_bank(self.chief.profile, 'Banco del jefe')
        ArenaTournament.objects.filter(pk=tournament.pk).update(bank=bank)
        self.client.force_login(self.ana)
        page = self.client.get(reverse('arena_manage_tournament', args=[tournament.slug])).content.decode()
        self.assertIn('Banco del jefe', page)
        tournament.refresh_from_db()
        response = post_json(self.client, reverse('arena_manage_tournament_action', args=[tournament.slug]),
                             {'action': 'update', 'bank': bank.pk, 'draw': {'auto_draw': True, 'draw_count': 1},
                              'version': tournament.version})
        self.assertEqual(response.status_code, 200, response.content)
        tournament.refresh_from_db()
        self.assertEqual(tournament.bank_id, bank.pk)

    def test_problems_one_cannot_see_are_hidden_and_kept(self):
        hidden_from = create_user(username='orgsinprivados', is_staff=True)
        grant(hidden_from, 'manage_all_arena')     # every bank, but no private problems
        bank = arena_tournament.create_bank(self.chief.profile, 'Banco mixto')
        arena_tournament.set_bank_problems(self.chief.profile, bank.pk, [
            {'id': self.secret.pk, 'level': 5, 'note': 'final'}, {'id': self.prob_a.pk, 'level': 1}])
        public = problem('permpublico', public=True)
        self.client.force_login(hidden_from)
        page = self.client.get(reverse('arena_manage_banks'), {'bank': bank.pk}).content.decode()
        self.assertNotIn('permsecreto', page)
        self.assertIn('id="bk-hidden"', page)
        response = post_json(self.client, reverse('arena_manage_bank_action'),
                             {'action': 'problems', 'bank': bank.pk, 'problems': [{'id': public.pk, 'level': 2}]})
        self.assertEqual(response.status_code, 200, response.content)
        rows = list(ArenaBankProblem.objects.filter(bank=bank).order_by('order').values_list('problem__code', 'level',
                                                                                             'note'))
        # prob_a is private too and this organizer cannot see it: kept, like the secret one.
        self.assertEqual(rows[0], ('permpublico', 2, ''))
        self.assertEqual(set(rows[1:]), {('permsecreto', 5, 'final'), ('permda', 1, '')})


class ArenaAdminTests(ArenaFixture):
    def test_lists_of_the_battles_section_show_only_ones_own(self):
        mine = self.duel_of(self.ana, self.p1, self.p2)
        theirs = self.duel_of(self.beto, self.p3, self.p4)
        self.client.force_login(self.ana)
        page = self.client.get(reverse('admin:judge_arenamatch_changelist')).content.decode()
        self.assertIn(reverse('arena_manage_match', args=[mine.pk]), page)
        self.assertNotIn(reverse('arena_manage_match', args=[theirs.pk]), page)
        page = self.client.get(reverse('admin:judge_arenascreen_changelist')).content.decode()
        self.assertEqual(page.count('field-name'), 1)
        self.client.force_login(self.chief)
        page = self.client.get(reverse('admin:judge_arenamatch_changelist')).content.decode()
        self.assertIn(reverse('arena_manage_match', args=[theirs.pk]), page)

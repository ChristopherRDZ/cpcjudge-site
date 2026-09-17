"""Balloon and location regressions; run only with isolated SQLite or disposable MariaDB settings."""
import json
import re
from html import unescape
from unittest.mock import patch
import threading
from datetime import timedelta

from django.contrib.auth.models import Permission
from django.db import connection, connections
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from judge.models import ContestBalloonAction, ContestLocation, ContestParticipation, ContestSubmission, Language, \
    Submission
from judge.models.tests.util import create_contest, create_contest_participation, create_contest_problem, \
    create_problem, create_user
from judge.views.contest_balloons import build_balloon_sheet, contest_balloon_mark

AJAX = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}


class BalloonTest(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.start = now - timedelta(hours=5)
        end = now - timedelta(hours=1)
        cls.freeze = end - timedelta(minutes=60)

        cls.editor = create_user(username='editor', is_staff=True)
        cls.editor.user_permissions.add(Permission.objects.get(codename='edit_own_contest'))
        cls.staff = create_user(username='globos')
        cls.normal = create_user(username='normal')
        cls.superuser = create_user(username='super', is_superuser=True, is_staff=True)

        cls.contest = create_contest(key='presencial', start_time=cls.start, end_time=end, is_visible=True,
                                     format_name='icpc', freeze_minutes=60, authors=('editor',))
        cls.contest.balloon_staff.add(cls.staff.profile)
        cls.other = create_contest(key='otro', start_time=cls.start, end_time=end, is_visible=True)

        cls.a = create_contest_problem(contest=cls.contest, problem=create_problem('pa'), points=1, order=1,
                                       balloon_color='#e74c3c', balloon_color_name='rojo')
        cls.b = create_contest_problem(contest=cls.contest, problem=create_problem('pb'), points=1, order=2)
        cls.c = create_contest_problem(contest=cls.contest, problem=create_problem('pc'), points=1, order=3,
                                       balloon_color='#ff0')
        cls.foreign = create_contest_problem(contest=cls.other, problem=create_problem('px'), points=1, order=1)

        cls.p = {}
        for name in ('alice', 'bob', 'carol', 'dq'):
            cls.p[name] = create_contest_participation(contest=cls.contest, user=name, real_start=cls.start)
        cls.p['dq'].is_disqualified = True
        cls.p['dq'].save()
        cls.p['virtual'] = ContestParticipation.objects.create(contest=cls.contest, user=cls.p['alice'].user,
                                                               virtual=1, real_start=cls.start)

        cls.submit(cls.p['alice'], cls.a, 10)
        cls.submit(cls.p['bob'], cls.a, 20)
        cls.submit(cls.p['bob'], cls.a, 25)
        cls.submit(cls.p['carol'], cls.a, 5, result='WA')
        cls.submit(cls.p['carol'], cls.b, 30)
        cls.submit(cls.p['bob'], cls.b, 2, pretest=True)
        cls.submit(cls.p['dq'], cls.a, 1)
        cls.submit(cls.p['virtual'], cls.c, 3)
        cls.submit(cls.p['alice'], cls.b, None, after_freeze=10)
        cls.submit(cls.p['bob'], cls.c, None, after_freeze=5)

    @classmethod
    def submit(cls, participation, problem, minutes, result='AC', pretest=False, after_freeze=None):
        date = cls.freeze + timedelta(minutes=after_freeze) if after_freeze is not None else \
            cls.start + timedelta(minutes=minutes)
        submission = Submission.objects.create(user=participation.user, problem=problem.problem,
                                               language=Language.objects.first(), status='D', result=result,
                                               points=1 if result == 'AC' else 0, case_points=1, case_total=1)
        Submission.objects.filter(id=submission.id).update(date=date)
        return ContestSubmission.objects.create(submission=submission, problem=problem, participation=participation,
                                                points=1 if result == 'AC' else 0, is_pretest=pretest)

    def url(self, name, contest=None):
        return reverse(name, args=[(contest or self.contest).key])

    def mark(self, user, participation, problem, action='D', ajax=True):
        self.client.force_login(user)
        data = {'participation': participation.id, 'problem': problem.id, 'balloon_action': action}
        return self.client.post(self.url('contest_balloon_mark'), data, **(AJAX if ajax else {}))

    def pending_keys(self):
        return [(b.profile.username, b.label) for b in build_balloon_sheet(self.contest).pending]

    # -- lo que cuenta como globo --

    def test_pending_rules(self):
        sheet = build_balloon_sheet(self.contest)
        self.assertEqual([(b.profile.username, b.problem.id) for b in sheet.pending],
                         [('alice', self.a.id), ('bob', self.a.id), ('carol', self.b.id)])
        self.assertEqual([b.first_blood for b in sheet.pending], [True, False, True])
        self.assertEqual(sheet.held, 2)
        self.assertEqual(sheet.pending[0].contest_time, '0:10')
        self.assertEqual({item['label']: (item['accepted'], item['pending']) for item in sheet.problems},
                         {'A': (2, 2), 'B': (1, 1), 'C': (0, 0)})
        self.assertTrue(sheet.freeze_on)

    def test_held_even_after_reveal(self):
        self.contest.scoreboard_revealed = True
        self.contest.save()
        sheet = build_balloon_sheet(self.contest)
        self.assertEqual(len(sheet.pending), 3)
        self.assertEqual(sheet.held, 2)

    def test_no_freeze_configured(self):
        self.contest.freeze_minutes = None
        self.contest.save()
        sheet = build_balloon_sheet(self.contest)
        self.assertEqual(len(sheet.pending), 5)
        self.assertEqual(sheet.held, 0)
        self.assertFalse(sheet.freeze_on)

    def test_text_color(self):
        self.assertEqual(self.a.balloon_text_color, '#fff')
        self.assertEqual(self.c.balloon_text_color, '#000')
        self.assertEqual(self.b.balloon_text_color, '')

    # -- permisos --

    def test_permissions(self):
        pages = [('contest_balloons', 'get'), ('contest_balloons_ajax', 'get'), ('contest_balloon_mark', 'post'),
                 ('contest_balloon_locations', 'post')]
        for name, method in pages:
            self.client.logout()
            response = getattr(self.client, method)(self.url(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn('login', response['Location'])
            self.client.force_login(self.normal)
            self.assertEqual(getattr(self.client, method)(self.url(name)).status_code, 404, name)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.url('contest_balloons')).status_code, 200)
        self.assertEqual(self.client.get(self.url('contest_balloons_ajax')).status_code, 200)
        self.assertEqual(self.client.post(self.url('contest_balloon_locations'), {'locations': ''}).status_code, 404)
        self.assertEqual(self.client.get(self.url('contest_balloon_mark')).status_code, 405)
        self.assertEqual(self.client.get(self.url('contest_balloons', self.other)).status_code, 404)
        self.client.force_login(self.editor)
        self.assertEqual(self.client.get(self.url('contest_balloons')).status_code, 200)
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.url('contest_balloons')).status_code, 200)
        self.assertFalse(self.contest.is_balloon_staff(self.normal))
        self.assertFalse(self.contest.can_manage_balloons(self.normal))

    def test_held_count_only_for_editors(self):
        self.client.force_login(self.staff)
        staff_page = self.client.get(self.url('contest_balloons')).content.decode()
        self.client.force_login(self.editor)
        editor_page = self.client.get(self.url('contest_balloons')).content.decode()
        self.assertIn('balloon-freeze', staff_page)
        self.assertNotIn('balloon-held', staff_page)
        self.assertRegex(editor_page, r'class="balloon-held">[^<]*2\.</span>')
        self.assertNotIn('name="location_snapshot"', staff_page)
        self.assertIn('name="location_snapshot"', editor_page)

    def test_spanish(self):
        self.client.force_login(self.editor)
        page = self.client.get(self.url('contest_balloons'), HTTP_ACCEPT_LANGUAGE='es').content.decode()
        for text in ('Globos pendientes', 'Marcar entregado', 'Primero en resolver', 'Envíos aceptados retenidos: 2.',
                     'Guardar ubicaciones', 'El marcador está congelado'):
            self.assertIn(text, page)
        self.client.force_login(self.superuser)
        page = self.client.get(reverse('admin:judge_contest_change', args=[self.contest.id]),
                               HTTP_ACCEPT_LANGUAGE='es').content.decode()
        for text in ('Personal de globos', 'color del globo', 'Globos'):
            self.assertIn(text.lower(), page.lower())

    def test_page_does_not_leak_held(self):
        self.client.force_login(self.staff)
        for name in ('contest_balloons', 'contest_balloons_ajax'):
            page = self.client.get(self.url(name)).content.decode()
            self.assertNotIn('data-key="%d-%d"' % (self.p['alice'].id, self.b.id), page)
            self.assertNotIn('data-key="%d-%d"' % (self.p['bob'].id, self.c.id), page)
            self.assertIn('data-key="%d-%d"' % (self.p['alice'].id, self.a.id), page)

    # -- marcar y deshacer --

    def test_deliver_and_undo(self):
        response = self.mark(self.staff, self.p['alice'], self.a)
        self.assertEqual(response.json(), {'ok': True, 'message': ''})
        self.assertEqual(self.pending_keys(), [('bob', 'A'), ('carol', 'B')])
        delivered = build_balloon_sheet(self.contest).delivered
        self.assertEqual([(b.profile.username, b.delivered_by.username) for b in delivered], [('alice', 'globos')])

        again = self.mark(self.editor, self.p['alice'], self.a).json()
        self.assertFalse(again['ok'])
        self.assertIn('globos', again['message'])
        self.assertEqual(ContestBalloonAction.objects.count(), 1)

        self.assertTrue(self.mark(self.editor, self.p['alice'], self.a, 'U').json()['ok'])
        self.assertFalse(self.mark(self.editor, self.p['alice'], self.a, 'U').json()['ok'])
        self.assertEqual(self.pending_keys(), [('alice', 'A'), ('bob', 'A'), ('carol', 'B')])
        log = build_balloon_sheet(self.contest).log
        self.assertEqual([(e['action'].action, e['action'].user.username) for e in log],
                         [('U', 'editor'), ('D', 'globos')])

        self.assertTrue(self.mark(self.staff, self.p['alice'], self.a).json()['ok'])
        self.assertEqual(ContestBalloonAction.objects.count(), 3)
        self.assertEqual(len(build_balloon_sheet(self.contest).delivered), 1)

    def test_cannot_deliver_what_is_not_due(self):
        for who, problem in (('alice', self.b), ('bob', self.c), ('dq', self.a), ('virtual', self.c),
                             ('carol', self.a), ('bob', self.b)):
            response = self.mark(self.staff, self.p[who], problem).json()
            self.assertFalse(response['ok'], (who, problem.id))
        self.assertEqual(ContestBalloonAction.objects.count(), 0)

    def test_bad_requests(self):
        self.assertEqual(self.mark(self.staff, self.p['alice'], self.foreign).status_code, 404)
        self.client.force_login(self.staff)
        url = self.url('contest_balloon_mark')
        self.assertEqual(self.client.post(url, {'participation': 'x', 'problem': self.a.id, 'balloon_action': 'D'})
                         .status_code, 400)
        self.assertEqual(self.client.post(url, {'participation': self.p['alice'].id, 'problem': self.a.id,
                                                'balloon_action': 'X'}).status_code, 400)
        other_participation = create_contest_participation(contest=self.other, user='zed')
        self.assertEqual(self.mark(self.staff, other_participation, self.a).status_code, 404)
        self.assertEqual(ContestBalloonAction.objects.count(), 0)

    def test_form_post_without_javascript(self):
        response = self.mark(self.staff, self.p['bob'], self.a, ajax=False)
        self.assertRedirects(response, self.url('contest_balloons'), fetch_redirect_response=False)
        response = self.mark(self.staff, self.p['bob'], self.a, ajax=False)
        page = self.client.get(response['Location']).content.decode()
        self.assertIn('balloon-errors', page)
        self.assertEqual(ContestBalloonAction.objects.count(), 1)

    def test_delivered_then_rejudged(self):
        self.assertTrue(self.mark(self.staff, self.p['bob'], self.a).json()['ok'])
        Submission.objects.filter(user=self.p['bob'].user, problem=self.a.problem).update(result='WA')
        sheet = build_balloon_sheet(self.contest)
        self.assertEqual([(b.profile.username, b.is_accepted) for b in sheet.delivered], [('bob', False)])
        self.assertNotIn(('bob', 'A'), self.pending_keys())
        self.client.force_login(self.staff)
        self.assertIn('balloon-warning', self.client.get(self.url('contest_balloons_ajax')).content.decode())
        # First blood moves to whoever is first now.
        self.assertTrue(build_balloon_sheet(self.contest).pending[0].first_blood)
        self.assertTrue(self.mark(self.staff, self.p['bob'], self.a, 'U').json()['ok'])

    def test_disqualified_after_delivery(self):
        self.assertTrue(self.mark(self.staff, self.p['carol'], self.b).json()['ok'])
        ContestParticipation.objects.filter(id=self.p['carol'].id).update(is_disqualified=True)
        sheet = build_balloon_sheet(self.contest)
        self.assertEqual([(b.profile.username, b.is_accepted) for b in sheet.delivered], [('carol', False)])

    # -- ubicaciones --

    def location_form(self, query=''):
        self.client.force_login(self.editor)
        body = self.client.get(self.url('contest_balloons') + query).content.decode()
        pairs = re.findall(r'name="(location_[^" ]+)"\s+value="([^"]*)"', body)
        return {name: unescape(value) for name, value in pairs}, body

    def save_locations(self, data, query=''):
        return self.client.post(self.url('contest_balloon_locations') + query, data)

    def test_roster_includes_empty_private_and_existing_locations(self):
        self.contest.private_contestants.add(self.normal.profile)
        ContestLocation.objects.create(contest=self.contest, user=self.staff.profile, location='Prior seat')
        data, body = self.location_form()
        for user in [p.user for p in self.p.values()] + [self.normal.profile, self.staff.profile]:
            self.assertIn('location_%d' % user.id, data)
            self.assertIn(user.username, body)
        self.assertEqual(data['location_%d' % self.p['alice'].user_id], '')
        self.assertEqual(len(data), 7)  # four entrants, one private, one old location, snapshot
        self.assertEqual(ContestLocation.objects.count(), 1)  # GET never creates empty records

    def test_save_clear_and_preserve_absent_rows(self):
        alice = 'location_%d' % self.p['alice'].user_id
        bob = 'location_%d' % self.p['bob'].user_id
        data, _ = self.location_form()
        data[alice] = 'Lab 1'
        self.assertEqual(self.save_locations(data).status_code, 302)
        ContestLocation.objects.create(contest=self.contest, user=self.normal.profile, location='Not on old form')
        # A second save from the old page edits Bob only; Alice and the newly added row survive.
        data[alice] = ''
        data[bob] = 'Lab 2'
        self.assertEqual(self.save_locations(data).status_code, 302)
        self.assertEqual(ContestLocation.objects.count(), 3)
        data, _ = self.location_form()
        data[alice] = ''
        self.assertEqual(self.save_locations(data).status_code, 302)
        self.assertFalse(ContestLocation.objects.filter(user=self.p['alice'].user).exists())
        self.assertEqual(ContestLocation.objects.count(), 2)

    def test_locations_conflict_is_atomic_and_keeps_input(self):
        alice = 'location_%d' % self.p['alice'].user_id
        bob = 'location_%d' % self.p['bob'].user_id
        data, _ = self.location_form()
        ContestLocation.objects.create(contest=self.contest, user=self.p['alice'].user, location='Other editor')
        data[alice], data[bob] = 'My seat', 'Second seat'
        response = self.save_locations(data)
        self.assertEqual(response.status_code, 409)
        self.assertIn('value="My seat"', response.content.decode())
        self.assertEqual(ContestLocation.objects.get().location, 'Other editor')

    def test_invalid_forms_never_delete_locations(self):
        ContestLocation.objects.create(contest=self.contest, user=self.p['alice'].user, location='Keep')
        data, _ = self.location_form()
        alice = 'location_%d' % self.p['alice'].user_id
        for bad in ({}, {'locations': ''}, dict(data, location_snapshot='tampered'),
                    {key: value for key, value in data.items() if key != alice}, dict(data, **{alice: 'x' * 61})):
            self.assertEqual(self.save_locations(bad).status_code, 400)
            self.assertEqual(ContestLocation.objects.get().location, 'Keep')
        self.assertEqual(self.save_locations(dict(data, **{alice: ['a', 'b']})).status_code, 400)
        self.client.post(self.url('contest_balloon_locations', self.other), data)
        self.assertEqual(ContestLocation.objects.get().location, 'Keep')

    def test_locations_escape_and_unauthorised_ids(self):
        data, _ = self.location_form()
        data['location_%d' % self.p['alice'].user_id] = '<script>alert(1)</script>'
        data['location_%d' % self.normal.profile.id] = 'Foreign'
        self.assertEqual(self.save_locations(data).status_code, 302)
        self.assertEqual(ContestLocation.objects.count(), 1)
        _, body = self.location_form()
        self.assertNotIn('<script>alert(1)', body)
        self.assertEqual(body.count('&lt;script&gt;alert(1)&lt;/script&gt;'), 2)

    def test_paging_search_only_changes_visible_rows(self):
        with patch('judge.views.contest_balloons.LOCATION_PAGE_SIZE', 2):
            data, _ = self.location_form('?page=1')
            self.assertEqual(len(data), 3)
            for key in data:
                if key != 'location_snapshot': data[key] = 'First page'
            self.assertEqual(self.save_locations(data, '?page=1').status_code, 302)
            data, _ = self.location_form('?page=2')
            self.assertEqual(len(data), 3)
            for key in data:
                if key != 'location_snapshot': data[key] = 'Second page'
            self.assertEqual(self.save_locations(data, '?page=2').status_code, 302)
            self.assertEqual(ContestLocation.objects.filter(location='First page').count(), 2)
            self.assertEqual(ContestLocation.objects.filter(location='Second page').count(), 2)
            data, _ = self.location_form('?q=alice')
            self.assertEqual(set(data), {'location_snapshot', 'location_%d' % self.p['alice'].user_id})

    # -- pestaña, administración y costo --

    def test_contest_tab(self):
        balloons_url = self.url('contest_balloons')
        for user, visible in ((self.staff, True), (self.editor, True), (self.normal, False)):
            self.client.force_login(user)
            page = self.client.get(self.url('contest_view')).content.decode()
            self.assertEqual(balloons_url in page, visible, user.username)
        self.client.logout()
        self.assertNotIn(balloons_url, self.client.get(self.url('contest_view')).content.decode())

    def test_admin(self):
        self.client.force_login(self.superuser)
        page = self.client.get(reverse('admin:judge_contest_change', args=[self.contest.id])).content.decode()
        for marker in ('name="balloon_staff"', 'balloon_color', 'balloon_color_name', self.url('contest_balloons')):
            self.assertIn(marker, page)

    def test_query_count_does_not_grow(self):
        def count():
            with CaptureQueriesContext(connection) as queries:
                build_balloon_sheet(self.contest)
            return len(queries)

        self.mark(self.staff, self.p['alice'], self.a)
        before = count()
        for i in range(15):
            name = 'extra%d' % i
            self.p[name] = create_contest_participation(contest=self.contest, user=name, real_start=self.start)
            self.submit(self.p[name], self.a, 40 + i)
            self.mark(self.staff, self.p[name], self.a)
        self.client.force_login(self.editor)
        data, _ = self.location_form()
        for i in range(15):
            data['location_%d' % self.p['extra%d' % i].user_id] = 'Lab %d' % i
        self.assertEqual(self.save_locations(data).status_code, 302)
        self.assertEqual(count(), before)
        self.assertEqual(len(build_balloon_sheet(self.contest).delivered), 16)


class ConcurrentDeliveryTest(TransactionTestCase):
    """Several volunteers pressing the same button at the same moment log one delivery, not several.

    Only meaningful on a database that really locks rows, so it runs on the disposable MariaDB of
    probar_mariadb.sh and is skipped on sqlite.
    """
    fixtures = ['language_small.json']

    def test_same_balloon_at_once(self):
        if connection.vendor != 'mysql':
            self.skipTest('sqlite does not lock rows')
        now = timezone.now()
        contest = create_contest(key='carrera', start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=1))
        problem = create_contest_problem(contest=contest, problem=create_problem('carrera'), points=1, order=1)
        participation = create_contest_participation(contest=contest, user='equipo', real_start=contest.start_time)
        submission = Submission.objects.create(user=participation.user, problem=problem.problem,
                                               language=Language.objects.first(), status='D', result='AC', points=1)
        Submission.objects.filter(id=submission.id).update(date=now - timedelta(minutes=5))
        ContestSubmission.objects.create(submission=submission, problem=problem, participation=participation,
                                         points=1)
        volunteers = [create_user(username='voluntario%d' % i) for i in range(6)]
        for volunteer in volunteers:
            contest.balloon_staff.add(volunteer.profile)

        barrier = threading.Barrier(len(volunteers))
        results = []

        def press(user):
            try:
                request = RequestFactory().post('/', {'participation': participation.id, 'problem': problem.id,
                                                      'balloon_action': 'D'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
                request.user = user
                request.profile = user.profile
                request._dont_enforce_csrf_checks = True
                barrier.wait()
                results.append(json.loads(contest_balloon_mark(request, contest.key).content)['ok'])
            finally:
                connections.close_all()

        threads = [threading.Thread(target=press, args=(volunteer,)) for volunteer in volunteers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), [False] * (len(volunteers) - 1) + [True])
        self.assertEqual(ContestBalloonAction.objects.filter(participation=participation).count(), 1)

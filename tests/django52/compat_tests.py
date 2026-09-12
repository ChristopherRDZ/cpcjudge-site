"""Regression tests on copied DMOJ and disposable MariaDB, with synthetic users."""
from datetime import datetime, timedelta, timezone as datetime_timezone
from io import BytesIO
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core import mail
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import pyotp

from judge.models import (Contest, ContestParticipation, ContestProblem, ContestSubmission, Judge, Language, NavigationBar,
                          Problem, Profile, Submission, SubmissionSource, SubmissionTestCase)
from judge.models.problem import SubmissionSourceAccess
from judge.models.tests.util import create_contest, create_problem, create_problem_type


@override_settings(DMOJ_REQUIRE_STAFF_2FA=False)
class CompatibilityFlows(TestCase):
    @classmethod
    def setUpTestData(cls):
        Site.objects.update_or_create(id=1, defaults={'domain': 'lab.invalid', 'name': 'CPC laboratory'})
        cls.language, _ = Language.objects.update_or_create(key='PY3', defaults={
            'name': 'Python 3', 'common_name': 'Python 3', 'ace': 'python', 'pygments': 'python3', 'extension': 'py'})
        Language.objects.update_or_create(key='CPP11', defaults={
            'name': 'C++ 11', 'common_name': 'C++', 'ace': 'c_cpp', 'pygments': 'cpp', 'extension': 'cpp'})
        cls.password = 'Synthetic-Lab-Password-2026!'
        cls.owner = User.objects.create_user('labowner', 'owner@lab.invalid', cls.password)
        cls.other = User.objects.create_user('labother', 'other@lab.invalid', cls.password)
        cls.admin = User.objects.create_superuser('labadmin', 'admin@lab.invalid', cls.password)
        for user in (cls.owner, cls.other, cls.admin):
            p, _ = Profile.objects.get_or_create(user=user)
            p.language = cls.language
            p.timezone = 'America/Mexico_City'
            p.save()
        create_problem_type('synthetic')
        cls.problem = create_problem(code='labnormal', name='Synthetic normal problem', is_public=True,
            description='Synthetic statement', types=['synthetic'],
            allowed_languages=['PY3', 'CPP11'], authors=['labadmin'],
            submission_source_visibility_mode=SubmissionSourceAccess.ALWAYS)
        cls.normal = Submission.objects.create(user=cls.owner.profile, problem=cls.problem,
            language=cls.language, status='D', result='AC', points=1, time=.01, memory=1024)
        SubmissionSource.objects.create(submission=cls.normal, source='print(7)\n')
        SubmissionTestCase.objects.create(submission=cls.normal, case=1, status='AC', output='7\n')
        cls.contest = create_contest(key='labcontest', name='Synthetic contest', is_visible=True,
            start_time=timezone.now()-timedelta(hours=1), end_time=timezone.now()+timedelta(days=1),
            authors=['labadmin'], format_name='icpc', format_config={})
        ContestProblem.objects.create(contest=cls.contest, problem=cls.problem, points=1, order=0)
        NavigationBar.objects.create(key='labnav', label='Lab', path='/problems/', regex='^/problems/', order=1)
        judge = Judge.objects.create(name='synthetic-form-judge', auth_key='synthetic-unused-key', online=True)
        judge.runtimes.set(Language.objects.filter(key__in=['PY3', 'CPP11']))
        judge.problems.add(cls.problem)

    def form_values(self, response, form_id):
        from lxml import html
        self.assertEqual(response.status_code, 200)
        tree = html.fromstring(response.content)
        forms = tree.xpath('//form[@id=$id]', id=form_id)
        self.assertEqual(len(forms), 1)
        data = {}
        for field in forms[0].xpath('.//input[@name] | .//select[@name] | .//textarea[@name]'):
            name = field.get('name')
            if field.get('disabled') is not None or '__prefix__' in name:
                continue
            kind = field.get('type', '').lower()
            if kind in ('submit', 'button', 'file') or (kind in ('checkbox', 'radio') and field.get('checked') is None):
                continue
            if field.tag == 'select':
                options = field.xpath('.//option[@selected]')
                if not options and field.get('multiple') is None:
                    options = field.xpath('.//option')[:1]
                value = [o.get('value', o.text or '') for o in options]
                if field.get('multiple') is None:
                    value = value[0] if value else ''
            else:
                value = field.text or '' if field.tag == 'textarea' else field.get('value', '')
            if name in data:
                previous = data[name] if isinstance(data[name], list) else [data[name]]
                data[name] = previous + (value if isinstance(value, list) else [value])
            else:
                data[name] = value
        # A browser leaves unused extra inline rows unchanged. lxml retains the
        # formatting newline that HTML strips immediately after <textarea>.
        for key, value in list(data.items()):
            if isinstance(value, str) and value.startswith('\n'):
                data[key] = value[1:]
            if key.endswith('-TOTAL_FORMS'):
                # Submit existing inline rows; unused extra rows are not edits.
                data[key] = data[key[:-11] + 'INITIAL_FORMS']
        data['_save'] = 'Save'
        return data

    def assert_admin_saved(self, response):
        errors = []
        if response.context:
            adminform = response.context.get('adminform')
            if adminform:
                errors.append(str(adminform.form.errors))
            for inline in response.context.get('inline_admin_formsets', []):
                errors.append(str(inline.formset.errors))
                errors.append(str(inline.formset.non_form_errors()))
        self.assertEqual(response.status_code, 302, '\n'.join(errors))

    def test_admin_edits_problem_profile_navigation_and_contest(self):
        self.client.force_login(self.admin)
        cases = [(self.problem, 'problem', 'name', 'Edited problem from admin'),
                 (self.owner.profile, 'profile', 'about', 'Synthetic edited profile'),
                 (NavigationBar.objects.get(key='labnav'), 'navigationbar', 'label', 'Edited navigation'),
                 (self.contest, 'contest', 'name', 'Edited contest from admin')]
        for obj, model, field, value in cases:
            with self.subTest(model=model):
                url = '/admin/judge/%s/%s/change/' % (model, obj.pk)
                data = self.form_values(self.client.get(url), model + '_form')
                data[field] = value
                self.assert_admin_saved(self.client.post(url, data))
                obj.refresh_from_db()
                self.assertEqual(getattr(obj, field), value)
        self.assertEqual(ContestProblem.objects.filter(contest=self.contest).count(), 1)
        self.assertEqual(self.problem.allowed_languages.count(), 2)

    def test_admin_creates_contest_and_problem_and_validates_times(self):
        self.client.force_login(self.admin)
        url = '/admin/judge/contest/add/'
        data = self.form_values(self.client.get(url), 'contest_form')
        data.update(key='labcreatedcontest', name='Created through admin', authors=[self.admin.profile.pk],
                    is_visible='on', start_time_0='2026-09-12', start_time_1='10:00:00',
                    end_time_0='2026-09-12', end_time_1='12:00:00', format_name='icpc', format_config='{}')
        self.assert_admin_saved(self.client.post(url, data))
        contest = Contest.objects.get(key='labcreatedcontest')
        self.assertEqual(contest.end_time-contest.start_time, timedelta(hours=2))
        self.assertEqual(contest.start_time.hour, 16)  # Mexico City 10:00 -> UTC 16:00
        self.assertTrue(contest.authors.filter(pk=self.admin.profile.pk).exists())
        url = '/admin/judge/contest/%s/change/' % contest.pk
        data = self.form_values(self.client.get(url), 'contest_form')
        data.update(end_time_0='2026-09-11', end_time_1='09:00:00')
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['adminform'].form.errors)
        contest.refresh_from_db()
        self.assertEqual(contest.end_time-contest.start_time, timedelta(hours=2))
        url = '/admin/judge/problem/add/'
        data = self.form_values(self.client.get(url), 'problem_form')
        data.update(code='labcreatedproblem', name='Created through admin', description='Synthetic statement',
                    authors=[self.admin.profile.pk], group=self.problem.group_id,
                    types=list(self.problem.types.values_list('pk', flat=True)), points='100',
                    time_limit='1', memory_limit='65536', allowed_languages=[self.language.pk])
        self.assert_admin_saved(self.client.post(url, data))
        problem = Problem.objects.get(code='labcreatedproblem')
        self.assertEqual(problem.allowed_languages.get(), self.language)

    def test_admin_denies_nonstaff_and_unprivileged_staff(self):
        for staff in (False, True):
            self.other.is_staff = staff
            self.other.save(update_fields=['is_staff'])
            self.client.force_login(self.other)
            for model, obj in [('problem', self.problem), ('contest', self.contest), ('profile', self.owner.profile)]:
                url = '/admin/judge/%s/%s/change/' % (model, obj.pk)
                for response in (self.client.get(url), self.client.post(url, {'name': 'Unauthorized'})):
                    self.assertIn(response.status_code, (302, 403))
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.name, 'Synthetic normal problem')

    def test_admin_ban_applies_only_after_valid_save(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('contest_join', args=[self.contest.key]))
        self.owner.profile.refresh_from_db()
        participation_id = self.owner.profile.current_contest_id
        self.assertIsNotNone(participation_id)
        self.client.force_login(self.admin)
        url = '/admin/judge/contest/%s/change/' % self.contest.pk
        data = self.form_values(self.client.get(url), 'contest_form')
        data['banned_users'] = [self.owner.profile.pk]
        good_name = data['name']
        data['name'] = ''
        self.assertEqual(self.client.post(url, data).status_code, 200)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.current_contest_id, participation_id)
        data['name'] = good_name
        self.assert_admin_saved(self.client.post(url, data))
        self.owner.profile.refresh_from_db()
        self.assertIsNone(self.owner.profile.current_contest_id)
        self.assertTrue(self.contest.banned_users.filter(pk=self.owner.profile.pk).exists())

    def test_contest_join_submit_scoreboard_and_leave(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(reverse('contest_join', args=[self.contest.key])).status_code, 302)
        participation = ContestParticipation.objects.get(contest=self.contest, user=self.owner.profile)
        self.assertEqual(participation.virtual, ContestParticipation.LIVE)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.current_contest_id, participation.pk)
        self.assertEqual(self.client.get(reverse('problem_submit', args=[self.problem.code])).status_code, 200)
        for result, points in [('WA', 0), ('AC', 1)]:
            with patch.object(Submission, 'judge', autospec=True) as schedule:
                response = self.client.post(reverse('problem_submit', args=[self.problem.code]),
                    {'language': self.language.pk, 'source': 'print(7)\n', 'judge': ''})
            self.assertEqual(response.status_code, 302)
            schedule.assert_called_once()
            sub = Submission.objects.latest('id')
            self.assertEqual(sub.contest_object_id, self.contest.pk)
            cs = ContestSubmission.objects.get(submission=sub)
            self.assertEqual(cs.participation_id, participation.pk)
            sub.status, sub.result, sub.points = 'D', result, points
            sub.save(update_fields=['status', 'result', 'points'])
            cs.points = points
            cs.save(update_fields=['points'])
            self.contest.format.update_participation(participation)
        participation.refresh_from_db()
        self.assertEqual(participation.score, 1)
        self.assertEqual(next(iter(participation.format_data.values()))['penalty'], 1)
        self.assertGreaterEqual(participation.cumtime, 20*60)
        response = self.client.get(reverse('contest_ranking', args=[self.contest.key]))
        self.assertContains(response, self.owner.username)
        self.assertEqual(self.client.post(reverse('contest_leave', args=[self.contest.key])).status_code, 302)
        self.owner.profile.refresh_from_db()
        self.assertIsNone(self.owner.profile.current_contest_id)

    def test_contest_access_code_ban_future_and_virtual(self):
        self.client.force_login(self.owner)
        url = reverse('contest_join', args=[self.contest.key])
        self.contest.access_code = 'synthetic-contest-code'
        self.contest.save()
        self.client.post(url, {'access_code': 'incorrect'})
        self.assertFalse(ContestParticipation.objects.filter(user=self.owner.profile).exists())
        self.contest.banned_users.add(self.owner.profile)
        self.client.post(url, {'access_code': 'synthetic-contest-code'})
        self.assertFalse(ContestParticipation.objects.filter(user=self.owner.profile).exists())
        self.contest.banned_users.clear()
        self.contest.start_time, self.contest.end_time = timezone.now()+timedelta(days=1), timezone.now()+timedelta(days=2)
        self.contest.save()
        self.client.post(url, {'access_code': 'synthetic-contest-code'})
        self.assertFalse(ContestParticipation.objects.filter(user=self.owner.profile).exists())
        self.contest.start_time, self.contest.end_time = timezone.now()-timedelta(days=2), timezone.now()-timedelta(days=1)
        self.contest.save()
        self.client.post(url, {'access_code': 'synthetic-contest-code'})
        participation = ContestParticipation.objects.get(user=self.owner.profile, contest=self.contest)
        self.assertGreater(participation.virtual, 0)

    def test_admin_image_upload(self):
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = BytesIO()
        Image.new('RGB', (2, 2), 'blue').save(buf, format='PNG')
        self.client.force_login(self.admin)
        response = self.client.post(reverse('martor_image_uploader'), {
            'markdown-image-upload': SimpleUploadedFile('fixture.png', buf.getvalue(), content_type='image/png')})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('link'))
        self.assertTrue(list(Path(settings.MEDIA_ROOT).rglob('*.png')))

    def test_admin_contest_problem_inline_add_edit_delete(self):
        self.client.force_login(self.admin)
        extra = create_problem(code='labinline', name='Inline problem', description='Synthetic',
            types=['synthetic'], allowed_languages=['PY3'])
        url = '/admin/judge/contest/%s/change/' % self.contest.pk
        response = self.client.get(url)
        prefix = response.context['inline_admin_formsets'][0].formset.prefix
        data = self.form_values(response, 'contest_form')
        data[prefix + '-TOTAL_FORMS'] = '2'
        data.update({prefix + '-1-problem': extra.pk, prefix + '-1-points': '2',
                     prefix + '-1-order': '1', prefix + '-1-output_prefix_override': '',
                     prefix + '-1-max_submissions': '3'})
        self.assert_admin_saved(self.client.post(url, data))
        self.assertEqual(ContestProblem.objects.filter(contest=self.contest).count(), 2)
        cp = ContestProblem.objects.get(contest=self.contest, problem=extra)
        self.assertEqual((cp.points, cp.max_submissions), (2, 3))
        data = self.form_values(self.client.get(url), 'contest_form')
        data[prefix + '-1-points'] = '3'
        self.assert_admin_saved(self.client.post(url, data))
        cp.refresh_from_db()
        self.assertEqual(cp.points, 3)
        data = self.form_values(self.client.get(url), 'contest_form')
        data[prefix + '-1-DELETE'] = 'on'
        self.assert_admin_saved(self.client.post(url, data))
        self.assertFalse(ContestProblem.objects.filter(pk=cp.pk).exists())
        self.assertTrue(Problem.objects.filter(pk=extra.pk).exists())

    def test_admin_history_revert_and_bulk_visibility(self):
        self.client.force_login(self.admin)
        url = '/admin/judge/problem/%s/change/' % self.problem.pk
        data = self.form_values(self.client.get(url), 'problem_form')
        data['name'] = 'First admin revision'
        self.assert_admin_saved(self.client.post(url, data))
        from reversion.models import Version
        version = Version.objects.get_for_object(self.problem).first()
        data = self.form_values(self.client.get(url), 'problem_form')
        data['name'] = 'Second admin revision'
        self.assert_admin_saved(self.client.post(url, data))
        self.assertEqual(self.client.get('/admin/judge/problem/%s/history/' % self.problem.pk).status_code, 200)
        url = reverse('admin:judge_problem_revision', args=[self.problem.pk, version.pk])
        data = self.form_values(self.client.get(url), 'problem_form')
        self.assertEqual(data['name'], 'First admin revision')
        self.assert_admin_saved(self.client.post(url, data))
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.name, 'First admin revision')
        for action, visible in [('make_hidden', False), ('make_visible', True)]:
            response = self.client.post('/admin/judge/contest/', {'action': action,
                '_selected_action': [self.contest.pk], 'index': '0'})
            self.assertEqual(response.status_code, 302)
            self.contest.refresh_from_db()
            self.assertEqual(self.contest.is_visible, visible)

    def test_admin_ajax_selectors_and_assets(self):
        self.client.force_login(self.admin)
        for name, query, expected in [('profile_select2', 'labowner', self.owner.profile.pk),
                                      ('problem_select2', 'labnormal', self.problem.pk)]:
            response = self.client.get(reverse(name), {'term': query, 'page': 1})
            self.assertEqual(response.status_code, 200)
            self.assertIn(str(expected), [str(r['id']) for r in response.json()['results']])
        from lxml import html
        from django.contrib.staticfiles.storage import staticfiles_storage
        response = self.client.get('/admin/judge/contest/%s/change/' % self.contest.pk)
        tree = html.fromstring(response.content)
        paths = tree.xpath('//script[@src]/@src | //link[@rel="stylesheet"]/@href')
        checked = 0
        for url in paths:
            if url.startswith(settings.STATIC_URL):
                path = url[len(settings.STATIC_URL):].split('?', 1)[0]
                self.assertTrue(staticfiles_storage.exists(path), path)
                checked += 1
        self.assertGreater(checked, 5)

    def setUp(self):
        cache.clear()  # This is only the isolated LocMem cache.

    def test_public_pages_and_own_branding(self):
        for url in ('/', '/accounts/login/', '/accounts/register/', '/problems/', '/users/', '/contests/',
                    '/problem/labnormal', '/contest/labcontest', '/contest/labcontest/ranking/', '/submissions/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
        response = self.client.get('/accounts/register/')
        self.assertContains(response, 'first_name')
        self.assertContains(response, 'last_name')

    def test_password_login_and_logout(self):
        response = self.client.post('/accounts/login/', {'username': self.owner.username, 'password': self.password})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.owner.pk)
        self.assertEqual(self.client.get('/user').status_code, 200)
        response = self.client.post('/accounts/logout/')
        self.assertIn(response.status_code, (200, 302))
        self.assertNotIn('_auth_user_id', self.client.session)
        response = self.client.post('/accounts/login/', {'username': self.owner.username, 'password': 'incorrect'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_registration_custom_names_and_activation(self):
        with self.captureOnCommitCallbacks(execute=True), patch('judge.utils.pwned._get_pwned', return_value={}):
            response = self.client.post('/accounts/register/', {
                'username': 'labnew', 'first_name': 'Nombre', 'last_name': 'Apellido',
                'email': 'new@lab.invalid', 'password1': self.password, 'password2': self.password,
                'timezone': 'America/Mexico_City', 'language': self.language.pk, 'tos': True})
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        user = User.objects.get(username='labnew')
        self.assertEqual((user.first_name, user.last_name), ('Nombre', 'Apellido'))
        self.assertFalse(user.is_active)
        self.assertEqual(user.profile.language, self.language)
        self.assertTrue(mail.outbox)
        from registration.models import RegistrationProfile
        key = RegistrationProfile.objects.get(user=user).activation_key
        response = self.client.get(reverse('registration_activate', args=[key]))
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_password_reset_email_and_token(self):
        response = self.client.post('/accounts/password/reset/', {'email': self.owner.email})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(mail.outbox)
        body = mail.outbox[-1].body
        match = re.search(r'/accounts/password/reset/confirm/[^\s<>]+', body)
        self.assertIsNotNone(match)
        response = self.client.get(match.group(0), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'new_password1')
        final_url = response.redirect_chain[-1][0] if response.redirect_chain else match.group(0)
        with patch('judge.utils.pwned._get_pwned', return_value={}):
            response = self.client.post(final_url, {'new_password1': 'Replacement-Synthetic-2026!',
                                                   'new_password2': 'Replacement-Synthetic-2026!'})
        self.assertEqual(response.status_code, 302)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password('Replacement-Synthetic-2026!'))

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        response = client.post('/custom-test/run/', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_profile_form_and_timezone_calendar(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/edit/profile/').status_code, 200)
        self.assertEqual(self.client.get('/custom-test/').status_code, 200)
        now = timezone.now()
        response = self.client.get(reverse('contest_calendar', args=[now.year, now.month]))
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/contests.ics')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'BEGIN:VCALENDAR', response.content)
        self.assertIn(b'Synthetic contest', response.content)

    def test_timezone_conversion_and_dst(self):
        from judge.timezone import TimezoneMiddleware
        middleware = TimezoneMiddleware(lambda request: None)
        for name, month, expected_hours in [('America/New_York', 1, -5), ('America/New_York', 7, -4),
                                            ('America/Mexico_City', 7, -6), ('UTC', 7, 0)]:
            with self.subTest(zone=name, month=month):
                tz = middleware.get_timezone(SimpleNamespace(profile=SimpleNamespace(timezone=name)))
                utc = datetime(2026, month, 10, 12, tzinfo=datetime_timezone.utc)
                local = timezone.localtime(utc, timezone=tz)
                self.assertEqual(local.utcoffset(), timedelta(hours=expected_hours))
                naive = datetime(2026, month, 10, 12)
                self.assertEqual(timezone.make_aware(naive, timezone=tz).utcoffset(), timedelta(hours=expected_hours))

    def test_admin_lists_forms_and_reversion(self):
        self.client.force_login(self.admin)
        for url in ('/admin/', '/admin/judge/problem/', '/admin/judge/problem/%s/change/' % self.problem.pk,
                    '/admin/judge/profile/', '/admin/judge/contest/', '/admin/judge/navigationbar/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        import reversion
        from reversion.models import Version
        with reversion.create_revision():
            self.problem.name = 'Synthetic edited name'
            self.problem.save()
        revision = Version.objects.get_for_object(self.problem).first().revision
        self.problem.name = 'Other synthetic name'
        self.problem.save()
        revision.revert()
        self.problem.refresh_from_db()
        self.assertEqual(self.problem.name, 'Synthetic edited name')

    def make_custom(self):
        self.client.force_login(self.owner)
        with patch.object(Submission, 'judge', autospec=True) as schedule:
            response = self.client.post('/custom-test/run/', json.dumps({'language': 'PY3',
                'source': 'print(input())', 'input': 'synthetic\n'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        schedule.assert_called_once()
        return Submission.objects.get(pk=response.json()['submission_id'])

    def test_custom_scheduling_poll_and_output(self):
        sub = self.make_custom()
        self.assertEqual(sub.status, 'QU')
        self.assertFalse(sub.problem.is_public)
        self.assertEqual((Path(settings.DMOJ_PROBLEM_DATA_ROOT) / sub.problem.code / 'input.txt').read_text(), 'synthetic\n')
        self.assertEqual(self.client.get('/custom-test/run/', {'id': sub.pk}).json()['status'], 'grading')
        Submission.objects.filter(pk=sub.pk).update(status='D', result='AC', time=.01, memory=1024)
        SubmissionTestCase.objects.create(submission=sub, case=1, status='AC', output='synthetic\n')
        self.assertEqual(self.client.get('/custom-test/run/', {'id': sub.pk}).json()['output'], 'synthetic\n')

    def test_custom_privacy_lists_and_direct_details(self):
        sub = self.make_custom()
        for user in (self.owner, self.other, self.admin):
            client = Client()
            client.force_login(user)
            for url in ('/submissions/', '/api/v2/submissions'):
                with self.subTest(user=user.username, url=url):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn(sub.problem.code.encode(), response.content)
                    self.assertIn(b'labnormal', response.content)
            allowed = user in (self.owner, self.admin)
            for url in ('/submission/%d' % sub.pk, '/src/%d/raw' % sub.pk, '/api/v2/submission/%d' % sub.pk):
                with self.subTest(user=user.username, url=url):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200 if allowed else 403)
            response = client.get('/custom-test/run/', {'id': sub.pk})
            self.assertEqual(response.status_code, 200 if user == self.owner else 404)

    def test_custom_poll_is_read_only_and_rejects_normal_ids(self):
        sub = self.make_custom()
        from judge.views.problem import custom_test_run
        from django.test import RequestFactory
        before = (Problem.objects.count(), Submission.objects.count())
        def readonly(execute, sql, params, many, context):
            self.assertTrue(sql.lstrip().upper().startswith('SELECT'), sql[:70])
            return execute(sql, params, many, context)
        with connection.execute_wrapper(readonly):
            for value, status in [(sub.pk, 200), (self.normal.pk, 404), ('not-an-id', 404), (10**100, 404)]:
                request = RequestFactory().get('/custom-test/run/', {'id': value})
                request.user, request.profile = self.owner, self.owner.profile
                self.assertEqual(custom_test_run(request).status_code, status)
        self.assertEqual(before, (Problem.objects.count(), Submission.objects.count()))

    def test_custom_cleanup_preserves_normal_and_other_owner(self):
        sub = self.make_custom()
        code = sub.problem.code
        Submission.objects.filter(pk=sub.pk).update(status='D', date=timezone.now()-timedelta(minutes=10))
        from judge.views.problem import _cleanup_owned_custom_tests
        with self.captureOnCommitCallbacks(execute=True):
            _cleanup_owned_custom_tests(self.other.profile)
        self.assertTrue(Submission.objects.filter(pk=sub.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            _cleanup_owned_custom_tests(self.owner.profile)
        self.assertFalse(Submission.objects.filter(pk=sub.pk).exists())
        self.assertTrue(Problem.objects.filter(pk=self.problem.pk).exists())
        self.assertTrue(Submission.objects.filter(pk=self.normal.pk).exists())
        self.assertFalse((Path(settings.DMOJ_PROBLEM_DATA_ROOT) / code).exists())

    def test_public_api_and_ranking_sql(self):
        for url in ('/api/v2/problems', '/api/v2/problem/labnormal', '/api/v2/users',
                    '/api/v2/contests', '/api/v2/contest/labcontest', '/api/v2/submissions',
                    '/problem/labnormal/rank/', '/problem/labnormal/submissions/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_storage_operations_are_confined(self):
        from judge.utils.problem_data import ProblemDataStorage
        storage = ProblemDataStorage()
        name = storage.save('labstorage/input.txt', ContentFile(b'synthetic\n'))
        self.assertEqual(storage.open(name).read(), b'synthetic\n')
        storage.rename(name, 'labstorage/renamed.txt')
        storage.delete('labstorage/renamed.txt')
        name = default_storage.save('lab-image.txt', ContentFile(b'fixture image placeholder'))
        self.assertTrue(Path(default_storage.path(name)).is_relative_to(Path(settings.MEDIA_ROOT)))
        default_storage.delete(name)

    def test_totp_and_encrypted_field_roundtrip(self):
        key = pyotp.random_base32()
        profile = self.owner.profile
        profile.totp_key = key
        profile.is_totp_enabled = True
        profile.save(update_fields=['totp_key', 'is_totp_enabled'])
        profile.refresh_from_db()
        self.assertEqual(profile.totp_key, key)
        self.assertTrue(profile.check_totp_code(pyotp.TOTP(key).now()))
        self.assertFalse(profile.check_totp_code('not-a-code'))
        with connection.cursor() as cursor:
            cursor.execute('SELECT totp_key FROM judge_profile WHERE id=%s', [profile.pk])
            stored = cursor.fetchone()[0]
        self.assertNotIn(key.encode(), bytes(stored))

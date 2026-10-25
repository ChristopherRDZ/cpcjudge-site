"""Emails to users: writers manage their own emails and send_mass_email does not open the drafts of others;
edit_all_mass_email does. Who receives an email (all, filters or chosen people), the rating filter and the page that
follows a task. Resend is mass_email_fake.FakeResend: nothing goes to the network."""
import json
from datetime import date

from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from judge.mass_email import describe, effective_audience, empty_filters, filters_of, recipient_rows
from judge.models import MassEmail, MassEmailRecipient, Profile
from judge.models.tests.util import create_user
from judge.tasks import send_mass_email
from mass_email_fake import fake_resend

RESEND = dict(DMOJ_RESEND_API_KEY='re_test_key', DMOJ_RESEND_REQUESTS_PER_SECOND=100000,
              DMOJ_MASS_EMAIL_SITE_URL='https://judge.example', DEFAULT_FROM_EMAIL='Club <contacto@example.com>',
              SITE_ADMIN_EMAIL='contacto@example.com')


def grant(user, *codenames):
    user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
    return user


def all_organizer(username):
    user = create_user(username=username, is_staff=True)
    return grant(user, 'manage_all_arena', 'see_private_problem')


def post_json(client, url, data):
    return client.post(url, json.dumps(data), content_type='application/json')


# ---------------------------------------------------------------------------------------------------------------
# Emails to users
# ---------------------------------------------------------------------------------------------------------------

def make_user(username, problems=0, rating=None, **extra):
    user = create_user(username=username, email='%s@example.com' % username, **extra)
    Profile.objects.filter(user=user).update(last_access=timezone.now(), problem_count=problems, rating=rating)
    return user


def filters(**values):
    result = empty_filters()
    result.update(values)
    return result


def names(rows):
    return sorted(row[1] for row in rows)


class AudienceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = make_user('aud_ana', problems=10, rating=1900)
        cls.beto = make_user('aud_beto', problems=1, rating=1200)
        cls.caro = make_user('aud_caro', problems=5)                  # no rating
        cls.dani = make_user('aud_dani', is_active=False)             # never confirmed
        cls.everyone = names(recipient_rows(filters(audience='all')))

    def test_all_ignores_the_filters_and_the_chosen_people(self):
        rows = recipient_rows(filters(audience='all', min_problems=5, include_users=[self.dani.profile.pk]))
        self.assertEqual(names(rows), self.everyone)
        self.assertNotIn('aud_dani', names(rows))

    def test_only_the_chosen_people(self):
        rows = recipient_rows(filters(audience='users', include_users=[self.beto.profile.pk, self.dani.profile.pk],
                                      min_problems=5))
        self.assertEqual(names(rows), ['aud_beto'])

    def test_filters_plus_people_always_included(self):
        rows = recipient_rows(filters(audience='filters', min_problems=5, include_users=[self.beto.profile.pk]))
        self.assertEqual(names(rows), ['aud_ana', 'aud_beto', 'aud_caro'])

    def test_filters_without_any_filter_is_nobody_but_the_chosen(self):
        self.assertEqual(recipient_rows(filters(audience='filters')), [])
        rows = recipient_rows(filters(audience='filters', include_users=[self.ana.profile.pk]))
        self.assertEqual(names(rows), ['aud_ana'])

    def test_never_include_wins_in_every_mode(self):
        for audience in ('all', 'filters', 'users'):
            rows = recipient_rows(filters(audience=audience, min_problems=1, include_users=[self.ana.profile.pk],
                                          exclude_users=[self.ana.profile.pk]))
            self.assertNotIn('aud_ana', names(rows), audience)

    def test_emails_saved_before_keep_their_meaning(self):
        # Without filters, people to include did not narrow anything: it went to everybody, and still does.
        rows = recipient_rows(filters(include_users=[self.beto.profile.pk]))
        self.assertEqual(names(rows), self.everyone)
        self.assertEqual(effective_audience(filters(include_users=[1])), 'all')
        self.assertEqual(effective_audience(filters(min_problems=3)), 'filters')

    def test_rating_range(self):
        self.assertEqual(names(recipient_rows(filters(audience='filters', min_rating=1500))), ['aud_ana'])
        rows = names(recipient_rows(filters(audience='filters', max_rating=1500)))
        self.assertIn('aud_beto', rows)
        self.assertIn('aud_caro', rows)          # no rating counts as below
        self.assertNotIn('aud_ana', rows)
        # A rating of 0 is a filter too.
        self.assertNotIn('aud_caro', names(recipient_rows(filters(audience='filters', min_rating=0))))

    def test_described_in_words(self):
        translation.activate('es')
        self.addCleanup(translation.deactivate)
        self.assertEqual(describe(filters(audience='all')), ['Todas las cuentas con el correo confirmado.'])
        self.assertEqual(describe(filters(audience='users', include_users=[1, 2])), ['Sólo 2 personas elegidas.'])
        lines = describe(filters(audience='filters', min_problems=3, max_rating=1500, include_users=[1],
                                 exclude_users=[2, 3], joined_after=date(2026, 1, 2)))
        self.assertEqual(lines[0], 'Las cuentas con el correo confirmado que:')
        self.assertIn('resolvieron al menos 3 problemas', lines)
        self.assertIn('tienen un rating de a lo más 1500, o no tienen rating', lines)
        self.assertIn('Además, 1 persona elegida por nombre.', lines)
        self.assertEqual(lines[-1], 'Nunca: 2 personas.')
        self.assertTrue(any(line.startswith('se registraron el ') for line in lines))


@override_settings(**RESEND)
class MailOwnershipTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = make_user('mailroot', is_staff=True, is_superuser=True)
        cls.ana = grant(make_user('mailana', is_staff=True), 'send_mass_email')
        cls.beto = grant(make_user('mailbeto', is_staff=True), 'send_mass_email')
        cls.chief = grant(make_user('mailjefe', is_staff=True), 'edit_all_mass_email')
        cls.people = [make_user('maillector%d' % index) for index in range(3)]

    def setUp(self):
        cache.clear()

    def mail_of(self, user, **values):
        values.setdefault('subject', 'Aviso de %s' % user.username)
        values.setdefault('content', 'Hola')
        return MassEmail.objects.create(created_by=user.profile, **values)

    def test_the_list_shows_only_ones_own_emails(self):
        self.mail_of(self.ana)
        self.mail_of(self.beto)
        self.client.force_login(self.ana)
        page = self.client.get(reverse('admin:judge_massemail_changelist')).content.decode()
        self.assertIn('Aviso de mailana', page)
        self.assertNotIn('Aviso de mailbeto', page)
        for user in (self.chief, self.root):
            self.client.force_login(user)
            page = self.client.get(reverse('admin:judge_massemail_changelist')).content.decode()
            self.assertIn('Aviso de mailana', page)
            self.assertIn('Aviso de mailbeto', page)

    def test_somebody_elses_email_cannot_be_opened_sent_or_copied(self):
        theirs = self.mail_of(self.beto)
        self.client.force_login(self.ana)
        response = self.client.get(reverse('admin:judge_massemail_change', args=[theirs.pk]))
        self.assertEqual(response.status_code, 302)          # the admin's «does not exist», back to the list
        for name in ('judge_massemail_send', 'judge_massemail_email', 'judge_massemail_sent_to'):
            self.assertEqual(self.client.get(reverse('admin:' + name, args=[theirs.pk])).status_code, 404, name)
        for name in ('judge_massemail_duplicate', 'judge_massemail_retry', 'judge_massemail_cancel',
                     'judge_massemail_stats'):
            self.assertEqual(self.client.post(reverse('admin:' + name, args=[theirs.pk])).status_code, 404, name)
        self.assertEqual(MassEmail.objects.count(), 1)
        response = self.client.post(reverse('admin:judge_massemail_change', args=[theirs.pk]),
                                    {'subject': 'Cambiado', 'content': 'x', 'language': 'es', '_continue': '1'})
        theirs.refresh_from_db()
        self.assertEqual(theirs.subject, 'Aviso de mailbeto')

    def test_the_new_permission_opens_everything_and_works_by_itself(self):
        theirs = self.mail_of(self.beto)
        self.client.force_login(self.chief)
        self.assertIn('fa-envelope', self.client.get(reverse('admin:index')).content.decode())
        self.assertEqual(self.client.get(reverse('admin:judge_massemail_change', args=[theirs.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:judge_massemail_send', args=[theirs.pk])).status_code, 200)
        response = self.client.post(reverse('admin:judge_massemail_duplicate', args=[theirs.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MassEmail.objects.latest('id').created_by, self.chief.profile)

    def test_the_form_asks_who_receives_it(self):
        self.client.force_login(self.ana)
        page = self.client.get(reverse('admin:judge_massemail_add'), HTTP_ACCEPT_LANGUAGE='es').content.decode()
        for text in ('Todas las cuentas con el correo confirmado', 'Sólo quienes cumplan los filtros de abajo',
                     'Sólo las personas que yo elija', 'id="id_audience_0"', 'name="min_rating"'):
            self.assertIn(text, page)
        self.assertRegex(page, r'value="all"[^>]*checked')

    def test_count_validates_and_describes(self):
        self.client.force_login(self.ana)
        url = reverse('admin:judge_massemail_count')
        data = self.client.post(url, {'audience': 'users'}, HTTP_ACCEPT_LANGUAGE='es').json()
        self.assertEqual(data['errors'], ['Elige a las personas que lo recibirán.'])
        data = self.client.post(url, {'audience': 'filters'}, HTTP_ACCEPT_LANGUAGE='es').json()
        self.assertTrue(data['errors'])
        data = self.client.post(url, {'audience': 'filters', 'min_rating': '2000', 'max_rating': '1000'}).json()
        self.assertTrue(data['errors'])
        data = self.client.post(url, {'audience': 'users', 'include_users': [self.people[0].profile.pk]},
                                HTTP_ACCEPT_LANGUAGE='es').json()
        self.assertEqual((data['count'], data['sample'], data['audience']), (1, ['maillector0'], 'users'))
        self.assertEqual(data['summary'], ['Sólo 1 persona elegida.'])
        # A form without the choice (a page opened before) keeps the old meaning.
        data = self.client.post(url, {'include_users': [self.people[0].profile.pk]}).json()
        self.assertEqual(data['audience'], 'all')

    def test_saving_only_chosen_people(self):
        self.client.force_login(self.ana)
        response = self.client.post(reverse('admin:judge_massemail_add'), {
            'subject': 'Solo a dos', 'preheader': '', 'heading': '', 'language': 'es', 'content': 'Hola',
            'audience': 'users', 'include_users': [self.people[0].profile.pk, self.people[1].profile.pk],
            '_continue': '1'})
        self.assertEqual(response.status_code, 302)
        mail = MassEmail.objects.get(subject='Solo a dos')
        self.assertEqual(mail.audience, 'users')
        self.assertEqual(names(recipient_rows(filters_of(mail))), ['maillector0', 'maillector1'])
        page = self.client.get(reverse('admin:judge_massemail_send', args=[mail.pk]),
                               HTTP_ACCEPT_LANGUAGE='es').content.decode()
        self.assertIn('Sólo 2 personas elegidas.', page)

    def test_a_draft_saved_before_shows_what_it_meant(self):
        old = self.mail_of(self.ana, min_problems=1)
        MassEmail.objects.filter(pk=old.pk).update(audience='')
        self.client.force_login(self.ana)
        page = self.client.get(reverse('admin:judge_massemail_change', args=[old.pk])).content.decode()
        self.assertRegex(page, r'value="filters"[^>]*checked')
        self.client.post(reverse('admin:judge_massemail_duplicate', args=[old.pk]))
        self.assertEqual(MassEmail.objects.latest('id').audience, 'filters')

    def test_unsubscribed_people_masked_and_resubscribing_for_all_only(self):
        with fake_resend() as resend:
            contact = resend.add_contact('maillector0@example.com', unsubscribed=True)
            self.client.force_login(self.ana)
            page = self.client.get(reverse('admin:judge_massemail_unsubscribed')).content.decode()
            self.assertIn('maillector0', page)
            self.assertNotIn('maillector0@example.com', page)
            self.assertIn('ma***@example.com', page)
            self.assertNotIn('name="contact"', page)
            response = self.client.post(reverse('admin:judge_massemail_unsubscribed'), {'contact': contact})
            self.assertEqual(response.status_code, 403)
            self.assertTrue(resend.contacts[contact]['unsubscribed'])
            self.client.force_login(self.chief)
            page = self.client.get(reverse('admin:judge_massemail_unsubscribed')).content.decode()
            self.assertIn('name="contact"', page)
            self.client.force_login(self.root)
            self.assertIn('maillector0@example.com',
                          self.client.get(reverse('admin:judge_massemail_unsubscribed')).content.decode())

    def test_progress_page_comes_back_to_the_email(self):
        mail = self.mail_of(self.ana, audience='users')
        mail.include_users.set([self.people[0].profile])
        self.client.force_login(self.ana)
        with fake_resend():
            response = self.client.post(reverse('admin:judge_massemail_send', args=[mail.pk]), {'confirm': '1',
                                                                                              'when': 'now'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('back=' + reverse('admin:judge_massemail_change', args=[mail.pk]).replace('/', '%2F'),
                      response['Location'])


@override_settings(**RESEND)
class MailTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = [make_user('tarea%d' % index) for index in range(2)]

    def test_errors_in_the_language_of_the_email_with_the_first_answer_of_resend(self):
        mail = MassEmail.objects.create(subject='Aviso', content='Hola', status=MassEmail.SENDING, language='es',
                                        from_email='Club <contacto@example.com>')
        for user in self.users:
            MassEmailRecipient.objects.create(mass_email=mail, profile=user.profile, username=user.username,
                                              email=user.email)
        with fake_resend() as resend:
            resend.fail('POST', '/contacts', 422, times=10)
            with translation.override('en'):                 # Celery has no request: whatever was active
                send_mass_email.apply(args=[mail.pk])
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.FAILED)
        self.assertTrue(mail.error.startswith('No se pudo añadir a nadie'), mail.error)
        self.assertIn('Primer error: ', mail.error)
        self.assertIn('422', mail.error)


class TaskStatusPageTests(TestCase):
    TASK = '6f1c1d5e-4b7a-4f3e-9a1e-0c1d2e3f4a5b'

    def test_logo_of_the_site_and_a_way_back(self):
        url = reverse('task_status', args=[self.TASK])
        page = self.client.get(url, {'message': 'Trabajando', 'back': '/admin/judge/massemail/1/change/'},
                               HTTP_ACCEPT_LANGUAGE='es').content.decode()
        self.assertIn('icons/logo', page)
        self.assertNotIn('icons/icon.svg', page)
        self.assertNotIn('DMOJ Icon', page)
        self.assertIn('href="/admin/judge/massemail/1/change/">Volver</a>', page)
        self.assertIn('<html lang="es">', page)

    def test_back_goes_where_the_user_came_from_and_never_outside(self):
        url = reverse('task_status', args=[self.TASK])
        page = self.client.get(url, HTTP_REFERER='http://testserver/problem/a/manage/submission').content.decode()
        self.assertIn('href="http://testserver/problem/a/manage/submission"', page)
        page = self.client.get(url, {'back': 'https://evil.example/'}).content.decode()
        self.assertNotIn('evil.example', page)
        self.assertIn('href="%s"' % reverse('home'), page)

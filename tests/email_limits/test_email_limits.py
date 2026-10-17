"""Email limits: per recipient, per day, and no account left without its activation email.

Run with run.py: in-memory database, cache and mail. Every address is under `lab.invalid`; nothing
leaves the machine.
"""
import logging
import smtplib
from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core import mail
from django.core.cache import cache
from django.core.mail.backends.base import BaseEmailBackend
from django.test import TestCase, override_settings
from django.conf import settings
from registration.models import RegistrationProfile

from dmoj.throttle_mail import ThrottledEmailHandler
from judge.models import Language, Profile
from judge.utils import mail as mail_utils

TEST_PASSWORD = 'Synthetic-Lab-Password-2026!'
FAILING = 'test_email_limits.FailingBackend'

ES_LIMIT = 'El sitio llegó a su límite de correos por hoy'
ES_RESET_LIMIT = 'Ya enviamos a esta dirección el máximo de correos para restablecer la contraseña'
ES_CHANGE_LIMIT = 'Ya pediste un cambio de correo en las últimas 24 horas'
ES_NOT_SENT = 'No pudimos enviar el correo en este momento'
ES_NOT_CREATED = 'No pudimos enviar el correo de activación, así que tu cuenta no se creó'


class FailingBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        raise smtplib.SMTPServerDisconnected('lab: the provider is unreachable')


def today_key():
    return 'mailbudget!' + datetime.now(timezone.utc).strftime('%Y-%m-%d')


class MailLimitsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Site.objects.update_or_create(id=1, defaults={'domain': 'lab.invalid', 'name': 'CPC laboratory'})
        cls.language, _ = Language.objects.update_or_create(key='PY3', defaults={
            'name': 'Python 3', 'common_name': 'Python 3', 'ace': 'python', 'pygments': 'python3', 'extension': 'py'})
        cls.owner = User.objects.create_user('labowner', 'owner@lab.invalid', TEST_PASSWORD)
        cls.other = User.objects.create_user('labother', 'other@lab.invalid', TEST_PASSWORD)
        for user in (cls.owner, cls.other):
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.language = cls.language
            profile.save()

    def setUp(self):
        cache.clear()  # Only the isolated in-memory cache.
        self.address = 0

    def ip(self):
        # Each request from another address, so the older per-IP limit does not mix with the new ones.
        self.address += 1
        return '192.0.2.%d' % self.address


class PasswordResetLimits(MailLimitsBase):
    def reset(self, email, language='es', ip=None):
        return self.client.post('/accounts/password/reset/', {'email': email},
                                REMOTE_ADDR=ip or self.ip(), HTTP_ACCEPT_LANGUAGE=language)

    def test_two_per_address_then_a_clear_message(self):
        for _ in range(2):
            self.assertEqual(self.reset('owner@lab.invalid').status_code, 302)
        self.assertEqual(len(mail.outbox), 2)
        response = self.reset('owner@lab.invalid')
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, ES_RESET_LIMIT, status_code=429)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail_utils.emails_sent_today(), 2)

    def test_message_in_english(self):
        for _ in range(2):
            self.reset('owner@lab.invalid', language='en')
        response = self.reset('owner@lab.invalid', language='en')
        self.assertContains(response, 'We have already sent the maximum number of password reset emails',
                            status_code=429)

    def test_case_and_spaces_count_as_the_same_address(self):
        self.reset('owner@lab.invalid')
        self.reset('OWNER@Lab.Invalid')
        self.assertEqual(self.reset('Owner@lab.invalid').status_code, 429)
        self.assertEqual(len(mail.outbox), 2)

    def test_unknown_address_gets_the_same_answers_and_spends_nothing(self):
        for _ in range(2):
            self.assertEqual(self.reset('nobody@lab.invalid').status_code, 302)
        self.assertContains(self.reset('nobody@lab.invalid'), ES_RESET_LIMIT, status_code=429)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(mail_utils.emails_sent_today(), 0)

    def test_addresses_are_independent(self):
        for _ in range(2):
            self.reset('owner@lab.invalid')
        self.assertEqual(self.reset('other@lab.invalid').status_code, 302)
        self.assertEqual([message.to for message in mail.outbox][-1], ['other@lab.invalid'])

    @override_settings(DMOJ_EMAIL_DAILY_LIMIT=5)
    def test_daily_limit_refuses_without_using_the_address_attempts(self):
        cache.set(today_key(), 5)
        response = self.reset('owner@lab.invalid')
        self.assertContains(response, ES_LIMIT, status_code=429)
        self.assertEqual(len(mail.outbox), 0)
        cache.set(today_key(), 0)
        for _ in range(2):
            self.assertEqual(self.reset('owner@lab.invalid').status_code, 302)
        self.assertEqual(len(mail.outbox), 2)

    @override_settings(DMOJ_EMAIL_DAILY_LIMIT=30, DMOJ_EMAIL_DAILY_RESERVE=20)
    def test_reset_may_use_the_reserve(self):
        cache.set(today_key(), 25)
        self.assertEqual(self.reset('owner@lab.invalid').status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_failed_send_gives_the_attempt_back_and_looks_the_same(self):
        with override_settings(EMAIL_BACKEND=FAILING), self.assertLogs('judge.mail', 'ERROR'):
            for _ in range(3):
                self.assertEqual(self.reset('owner@lab.invalid').status_code, 302)
        self.assertEqual(mail_utils.emails_sent_today(), 0)
        for _ in range(2):
            self.assertEqual(self.reset('owner@lab.invalid').status_code, 302)
        self.assertEqual(len(mail.outbox), 2)

    def test_previous_per_ip_limit_still_applies(self):
        ip = self.ip()
        for number in range(settings.DMOJ_PASSWORD_RESET_LIMIT_COUNT):
            self.assertEqual(self.reset('nobody%d@lab.invalid' % number, ip=ip).status_code, 302)
        response = self.reset('owner@lab.invalid', ip=ip)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertEqual(len(mail.outbox), 0)

    def test_link_still_works(self):
        self.reset('owner@lab.invalid')
        self.assertIn('/accounts/password/reset/confirm/', mail.outbox[0].body)


class EmailChangeLimits(MailLimitsBase):
    def change(self, user, new_email, language='es'):
        self.client.force_login(user)
        return self.client.post('/accounts/email/change/', {'password': TEST_PASSWORD, 'email': new_email},
                                REMOTE_ADDR=self.ip(), HTTP_ACCEPT_LANGUAGE=language)

    def test_one_request_per_user(self):
        response = self.change(self.owner, 'nuevo@lab.invalid')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['nuevo@lab.invalid', 'owner@lab.invalid'])
        self.assertEqual(mail_utils.emails_sent_today(), 2)
        response = self.change(self.owner, 'otro@lab.invalid')
        self.assertContains(response, ES_CHANGE_LIMIT, status_code=429)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(self.change(self.other, 'tercero@lab.invalid').status_code, 200)
        self.assertEqual(len(mail.outbox), 4)

    @override_settings(DMOJ_EMAIL_DAILY_LIMIT=5)
    def test_needs_room_for_both_emails(self):
        cache.set(today_key(), 4)
        self.assertContains(self.change(self.owner, 'nuevo@lab.invalid'), ES_LIMIT, status_code=429)
        self.assertEqual(len(mail.outbox), 0)
        cache.set(today_key(), 3)
        self.assertEqual(self.change(self.owner, 'nuevo@lab.invalid').status_code, 200)

    def test_failed_send_says_so_and_gives_the_attempt_back(self):
        with override_settings(EMAIL_BACKEND=FAILING), self.assertLogs('judge.mail', 'ERROR'):
            response = self.change(self.owner, 'nuevo@lab.invalid')
        self.assertContains(response, ES_NOT_SENT, status_code=503)
        self.assertEqual(mail_utils.emails_sent_today(), 0)
        self.assertEqual(self.change(self.owner, 'nuevo@lab.invalid').status_code, 200)
        self.assertEqual(len(mail.outbox), 2)


@patch('judge.utils.pwned._get_pwned', return_value={})
class RegistrationLimits(MailLimitsBase):
    def register(self, username, email, language='es'):
        return self.client.post('/accounts/register/', {
            'username': username, 'first_name': 'Nombre', 'last_name': 'Apellido', 'email': email,
            'password1': TEST_PASSWORD, 'password2': TEST_PASSWORD, 'timezone': 'America/Mexico_City',
            'language': self.language.pk, 'tos': True}, REMOTE_ADDR=self.ip(), HTTP_ACCEPT_LANGUAGE=language)

    def test_success_sends_once_and_counts(self, _pwned):
        response = self.register('labnew', 'new@lab.invalid')
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        user = User.objects.get(username='labnew')
        self.assertFalse(user.is_active)
        self.assertEqual((user.first_name, user.profile.language), ('Nombre', self.language))
        self.assertTrue(RegistrationProfile.objects.filter(user=user).exists())
        self.assertEqual([m.to for m in mail.outbox], [['new@lab.invalid']])
        self.assertIn(RegistrationProfile.objects.get(user=user).activation_key, mail.outbox[0].body)
        self.assertEqual(mail_utils.emails_sent_today(), 1)

    def test_failed_activation_email_leaves_no_account(self, _pwned):
        with override_settings(EMAIL_BACKEND=FAILING), self.assertLogs('judge.mail', 'ERROR'):
            response = self.register('labnew', 'new@lab.invalid')
        self.assertContains(response, ES_NOT_CREATED, status_code=503)
        self.assertFalse(User.objects.filter(username='labnew').exists())
        self.assertFalse(User.objects.filter(email='new@lab.invalid').exists())
        self.assertFalse(Profile.objects.filter(user__username='labnew').exists())
        self.assertEqual(RegistrationProfile.objects.count(), 0)
        self.assertEqual(mail_utils.emails_sent_today(), 0)
        # The same address and username can be used to try again.
        self.assertEqual(self.register('labnew', 'new@lab.invalid').status_code, 302)
        self.assertTrue(User.objects.get(username='labnew').profile)

    def test_message_in_english(self, _pwned):
        with override_settings(EMAIL_BACKEND=FAILING), self.assertLogs('judge.mail', 'ERROR'):
            response = self.register('labnew', 'new@lab.invalid', language='en')
        self.assertContains(response, 'Account not created', status_code=503)

    @override_settings(DMOJ_EMAIL_DAILY_LIMIT=30, DMOJ_EMAIL_DAILY_RESERVE=20)
    def test_registrations_leave_the_reserve(self, _pwned):
        cache.set(today_key(), 9)
        self.assertEqual(self.register('labnine', 'nine@lab.invalid').status_code, 302)
        response = self.register('labten', 'ten@lab.invalid')
        self.assertContains(response, ES_NOT_CREATED, status_code=429)
        self.assertFalse(User.objects.filter(username='labten').exists())
        self.assertEqual(len(mail.outbox), 1)


class ErrorReportLimits(MailLimitsBase):
    def emit(self, times):
        handler = ThrottledEmailHandler()
        for number in range(times):
            handler.emit(logging.LogRecord('django.request', logging.ERROR, __file__, number,
                                           'Synthetic lab error %d', (number,), None))

    @override_settings(ADMINS=[('Lab', 'admin@lab.invalid')], DMOJ_EMAIL_ERROR_DAILY_LIMIT=2,
                       DMOJ_EMAIL_THROTTLING=(100, 60))
    def test_daily_share_of_error_reports(self):
        self.emit(5)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail_utils.emails_sent_today(), 2)

    @override_settings(ADMINS=[], DMOJ_EMAIL_THROTTLING=(100, 60))
    def test_no_admins_spends_nothing(self):
        self.emit(3)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(mail_utils.emails_sent_today(), 0)

    @override_settings(ADMINS=[('Lab', 'admin@lab.invalid')], DMOJ_EMAIL_DAILY_LIMIT=30,
                       DMOJ_EMAIL_DAILY_RESERVE=20, DMOJ_EMAIL_THROTTLING=(100, 60))
    def test_error_reports_never_use_the_reserve(self):
        cache.set(today_key(), 10)
        self.emit(1)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(ADMINS=[('Lab', 'admin@lab.invalid')], DMOJ_EMAIL_THROTTLING=(3, 60))
    def test_per_minute_throttle_still_first(self):
        self.emit(5)
        self.assertEqual(len(mail.outbox), 2)


class Budget(TestCase):
    def setUp(self):
        cache.clear()

    def test_defaults(self):
        self.assertEqual((settings.DMOJ_EMAIL_DAILY_LIMIT, settings.DMOJ_EMAIL_DAILY_RESERVE,
                          settings.DMOJ_PASSWORD_RESET_DAILY_LIMIT, settings.DMOJ_EMAIL_CHANGE_DAILY_LIMIT,
                          settings.DMOJ_EMAIL_ERROR_DAILY_LIMIT), (90, 20, 2, 1, 10))

    def test_day_turns_at_utc_midnight(self):
        class Clock(datetime):
            moment = datetime(2026, 10, 14, 23, 59, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.moment

        with patch.object(mail_utils, 'datetime', Clock):
            mail_utils.spend_email_budget(3)
            self.assertEqual(mail_utils.emails_sent_today(), 3)
            # 18:01 in Mexico City is still the same day there, but another day in UTC.
            Clock.moment = datetime(2026, 10, 15, 0, 1, tzinfo=timezone.utc)
            self.assertEqual(mail_utils.emails_sent_today(), 0)

    def test_release_never_goes_below_zero_or_fails(self):
        mail_utils.release_email_attempt('pwreset', 'nobody@lab.invalid')
        self.assertTrue(mail_utils.claim_email_attempt('pwreset', 'nobody@lab.invalid', 1))

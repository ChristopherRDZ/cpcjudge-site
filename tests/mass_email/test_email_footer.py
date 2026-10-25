"""The email footer: every email says the club is independent and not an official service of the university,
and the emails to users also link the privacy notice (/privacidad/), in HTML and in plain text.

Real flows (password reset, sign up, email change) and the mass email renderer, in Spanish, English and a language
without a translation. Email goes to memory; addresses are in example.invalid.
"""
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import translation

from judge.mass_email import render_email
from judge.models import Language, MassEmail, Profile

TEST_PASSWORD = 'Synthetic-Test-Password-2026!'
CLUB = {'es': 'CPC-UAEH es un club estudiantil independiente; no es un servicio oficial de la UAEH.',
        'en': 'CPC-UAEH is an independent student club; it is not an official UAEH service.'}
PRIVACY = {'es': 'Aviso de privacidad', 'en': 'Privacy notice'}


def club(language):
    """Without a French catalog entry Django falls back to LANGUAGE_CODE, like the rest of the footer."""
    return CLUB[language] if language in CLUB else CLUB[settings.LANGUAGE_CODE[:2]]
SITE_URL = 'https://judge.example'


@override_settings(DMOJ_MASS_EMAIL_SITE_URL=SITE_URL, SITE_ADMIN_EMAIL='contacto@example.invalid')
class FooterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Site.objects.update_or_create(id=1, defaults={'domain': 'example.invalid', 'name': 'Test site'})
        cls.language, _ = Language.objects.update_or_create(key='PY3', defaults={
            'name': 'Python 3', 'common_name': 'Python 3', 'ace': 'python', 'pygments': 'python3', 'extension': 'py'})
        cls.owner = User.objects.create_user('owner', 'owner@example.invalid', TEST_PASSWORD)
        profile, _ = Profile.objects.get_or_create(user=cls.owner)
        profile.language = cls.language
        profile.save()

    def setUp(self):
        cache.clear()
        self.address = 0

    def ip(self):
        self.address += 1
        return '192.0.2.%d' % self.address

    def assertFooter(self, message, language):
        html = message.alternatives[0][0]
        for name, text in (('text', message.body), ('html', html)):
            self.assertEqual(text.count(club(language)), 1, '%s %s:\n%s' % (language, name, text[-1200:]))
            self.assertNotIn('%(', text)
        self.assertNotIn('&#', message.body)
        other = [text for text in CLUB.values() if text != club(language)]
        self.assertFalse(any(text in message.body + html for text in other))

    def test_password_reset(self):
        for language in ('es', 'en', 'fr'):
            mail.outbox.clear()
            cache.clear()
            response = self.client.post('/accounts/password/reset/', {'email': 'owner@example.invalid'},
                                        REMOTE_ADDR=self.ip(), HTTP_ACCEPT_LANGUAGE=language)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(mail.outbox), 1)
            self.assertFooter(mail.outbox[0], language)

    @patch('judge.utils.pwned._get_pwned', return_value={})
    def test_sign_up(self, _pwned):
        for number, language in enumerate(('es', 'en', 'fr')):
            mail.outbox.clear()
            response = self.client.post('/accounts/register/', {
                'username': 'newuser%d' % number, 'first_name': 'Nombre', 'last_name': 'Apellido',
                'email': 'new%d@example.invalid' % number, 'password1': TEST_PASSWORD, 'password2': TEST_PASSWORD,
                'timezone': 'America/Mexico_City', 'language': self.language.pk},
                REMOTE_ADDR=self.ip(), HTTP_ACCEPT_LANGUAGE=language)
            self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
            self.assertEqual(len(mail.outbox), 1)
            self.assertFooter(mail.outbox[0], language)

    def test_email_change_both_messages(self):
        for number, language in enumerate(('es', 'en')):
            mail.outbox.clear()
            cache.clear()
            user = User.objects.create_user('changer%d' % number, 'change%d@example.invalid' % number, TEST_PASSWORD)
            Profile.objects.get_or_create(user=user)
            self.client.force_login(user)
            response = self.client.post('/accounts/email/change/', {'password': TEST_PASSWORD,
                                                                    'email': 'nuevo%d@example.invalid' % number},
                                        REMOTE_ADDR=self.ip(), HTTP_ACCEPT_LANGUAGE=language)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(mail.outbox), 2)
            for message in mail.outbox:
                self.assertFooter(message, language)

    def test_email_to_users_html_and_text(self):
        for language in ('es', 'en'):
            item = MassEmail(subject='Aviso', heading='Concurso', content='Hola, **todos**.', language=language)
            for for_resend in (False, True):
                _subject, html, text = render_email(item, username='lector', for_resend=for_resend)
                self.assertEqual(html.count(CLUB[language]), 1, html[-1500:])
                self.assertEqual(text.count(CLUB[language]), 1, text)
                self.assertIn('href="%s/privacidad/"' % SITE_URL, html)
                self.assertIn('>%s</a>' % PRIVACY[language], html)
                self.assertIn('%s: %s/privacidad/' % (PRIVACY[language], SITE_URL), text)
                self.assertNotIn('%(', html + text)
                # The unsubscribe link and the rest of the footer are still there.
                self.assertIn('href="%s"' % ('{{{RESEND_UNSUBSCRIBE_URL}}}' if for_resend else SITE_URL + '/'), html)
                self.assertIn(SITE_URL, text.splitlines()[-1])

    def test_catalog(self):
        with translation.override('es'):
            self.assertEqual(translation.gettext(CLUB['en']), CLUB['es'])
            self.assertEqual(translation.gettext('Privacy notice'), 'Aviso de privacidad')
            self.assertEqual(translation.gettext('Privacy notice: %(url)s') % {'url': 'x'}, 'Aviso de privacidad: x')
            # What was there keeps its translation.
            self.assertEqual(translation.gettext('Unsubscribe from these emails'), 'Darme de baja de estos correos')

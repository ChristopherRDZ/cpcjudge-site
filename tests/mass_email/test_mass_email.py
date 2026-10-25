"""Emails to users: who receives them, how they look, the Resend client, the task that delivers them and the
"Emails to users" section of the admin. Resend is mass_email_fake.FakeResend: nothing goes to the network."""
import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from judge.mass_email import empty_filters, filters_of, html_to_text, recipient_rows, render_body, render_email
from judge.models import ContestParticipation, MassEmail, MassEmailRecipient, Profile
from judge.models.team import ContestTeamMember, Team
from judge.models.tests.util import create_contest, create_organization, create_user
from judge.tasks import send_mass_email
from judge.utils.resend import ResendClient, ResendError
from mass_email_fake import fake_resend

RESEND = dict(DMOJ_RESEND_API_KEY='re_test_key', DMOJ_RESEND_REQUESTS_PER_SECOND=100000,
              DMOJ_MASS_EMAIL_SITE_URL='https://judge.example', DEFAULT_FROM_EMAIL='Club <contacto@example.com>',
              SITE_ADMIN_EMAIL='contacto@example.com')


def make_user(username, email=None, active=True, joined=None, last_access=None, problems=0, **extra):
    user = create_user(username=username, email=email if email is not None else '%s@example.com' % username,
                       is_active=active, **extra)
    if joined:
        user.date_joined = timezone.make_aware(datetime.combine(joined, datetime.min.time()))
        user.save(update_fields=['date_joined'])
    Profile.objects.filter(user=user).update(last_access=last_access or timezone.now(), problem_count=problems)
    return user


def names(rows):
    return sorted(row[1] for row in rows)


def filters(**values):
    result = empty_filters()
    result.update(values)
    return result


class RecipientTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.ana = make_user('ana', joined=date(2026, 1, 10), problems=10)
        cls.beto = make_user('beto', joined=date(2025, 6, 1), last_access=now - timedelta(days=200))
        cls.caro = make_user('caro', active=False, joined=date(2026, 2, 1))           # never confirmed
        cls.dani = make_user('dani', email='', joined=date(2026, 2, 1))               # no address
        cls.eva = make_user('eva', email='ANA@example.com', joined=date(2026, 2, 1))  # same address as ana
        cls.fer = make_user('fer', joined=date(2026, 3, 1))
        cls.gil = make_user('gil', joined=date(2026, 3, 1))
        cls.hugo = make_user('hugo', joined=date(2026, 3, 1))
        cls.ines = make_user('ines', joined=date(2026, 3, 1), problems=3)
        cls.c1 = create_contest(key='mailc1')
        cls.c2 = create_contest(key='mailc2')
        cls.c3 = create_contest(key='mailc3')
        ContestParticipation.objects.create(contest=cls.c1, user=cls.ana.profile, virtual=ContestParticipation.LIVE)
        ContestParticipation.objects.create(contest=cls.c1, user=cls.gil.profile, virtual=1)
        ContestParticipation.objects.create(contest=cls.c1, user=cls.hugo.profile,
                                            virtual=ContestParticipation.SPECTATE)
        ContestParticipation.objects.create(contest=cls.c2, user=cls.beto.profile, virtual=ContestParticipation.LIVE)
        team = Team.objects.create(name='Equipo', owner=cls.ines.profile)
        entry = ContestParticipation.objects.create(contest=cls.c3, user=cls.ines.profile, team=team,
                                                    virtual=ContestParticipation.LIVE)
        for user in (cls.ines, cls.fer):
            ContestTeamMember.objects.create(participation=entry, profile=user.profile, username=user.username)
        cls.org = create_organization(name='mailorg')
        cls.ines.profile.organizations.add(cls.org)
        cls.group = Group.objects.create(name='Creadores de prueba')
        cls.fer.groups.add(cls.group)

    def test_without_filters_every_confirmed_account_once(self):
        self.assertEqual(names(recipient_rows(empty_filters())), ['ana', 'beto', 'fer', 'gil', 'hugo', 'ines'])

    def test_unconfirmed_accounts_never_receive_it_even_when_included(self):
        rows = recipient_rows(filters(contests=[self.c1.pk], include_users=[self.caro.profile.pk]))
        self.assertNotIn('caro', names(rows))

    def test_one_email_per_address_whatever_the_case(self):
        rows = recipient_rows(empty_filters())
        self.assertEqual(len({row[2].lower() for row in rows}), len(rows))
        self.assertIn('ana', names(rows))
        self.assertNotIn('eva', names(rows))

    def test_contests_are_a_union_of_official_participations(self):
        self.assertEqual(names(recipient_rows(filters(contests=[self.c1.pk, self.c2.pk]))), ['ana', 'beto'])

    def test_virtual_participations_only_when_asked_and_never_spectators(self):
        self.assertEqual(names(recipient_rows(filters(contests=[self.c1.pk], include_virtual=True))), ['ana', 'gil'])

    def test_team_members_count_as_participants(self):
        self.assertEqual(names(recipient_rows(filters(contests=[self.c3.pk]))), ['fer', 'ines'])

    def test_organizations_and_groups(self):
        self.assertEqual(names(recipient_rows(filters(organizations=[self.org.pk]))), ['ines'])
        self.assertEqual(names(recipient_rows(filters(groups=[self.group.pk]))), ['fer'])

    def test_recent_activity(self):
        self.assertNotIn('beto', names(recipient_rows(filters(active_within_days=90))))
        self.assertIn('beto', names(recipient_rows(filters(active_within_days=365))))

    def test_sign_up_dates_include_both_ends(self):
        rows = recipient_rows(filters(joined_after=date(2026, 1, 10), joined_before=date(2026, 3, 1)))
        self.assertEqual(names(rows), ['ana', 'fer', 'gil', 'hugo', 'ines'])
        self.assertEqual(names(recipient_rows(filters(joined_before=date(2025, 12, 31)))), ['beto'])

    def test_solved_problems(self):
        self.assertEqual(names(recipient_rows(filters(min_problems=3))), ['ana', 'ines'])

    def test_different_filters_must_all_hold(self):
        rows = recipient_rows(filters(contests=[self.c1.pk, self.c2.pk], active_within_days=90))
        self.assertEqual(names(rows), ['ana'])

    def test_always_include_adds_to_filters_and_never_include_wins(self):
        rows = recipient_rows(filters(contests=[self.c1.pk], include_users=[self.ines.profile.pk, self.ana.profile.pk],
                                      exclude_users=[self.ana.profile.pk]))
        self.assertEqual(names(rows), ['ines'])

    def test_always_include_without_filters_does_not_narrow(self):
        rows = recipient_rows(filters(include_users=[self.ines.profile.pk]))
        self.assertEqual(len(rows), 6)

    def test_filters_of_a_saved_email(self):
        mail = MassEmail.objects.create(subject='s', content='c', include_virtual=True, min_problems=1)
        mail.contests.set([self.c1])
        self.assertEqual(names(recipient_rows(filters_of(mail))), ['ana'])


@override_settings(**RESEND)
class RenderTests(TestCase):
    def mail(self, **values):
        values.setdefault('subject', 'Concurso de otoño')
        values.setdefault('content', 'Hola {{usuario}}, te esperamos.')
        return MassEmail(**values)

    def test_links_and_images_become_absolute_and_styled_inline(self):
        body = render_body('# Gran final\n\nVer [concursos](/contests/) y ![cartel](/media/martor/a.png)')
        self.assertIn('href="https://judge.example/contests/"', body)
        self.assertIn('src="https://judge.example/media/martor/a.png"', body)
        self.assertRegex(body, r'<h3[^>]* style="margin:22px 0 10px;font-size:20px;[^"]*">Gran final</h3>')
        self.assertIn('max-width:100%', body)

    def test_scripts_and_event_handlers_are_removed(self):
        body = render_body('<script>alert(1)</script><p onclick="x()">hola</p>'
                           '<a href="javascript:alert(1)">mal</a><style>p{color:red}</style>')
        for bad in ('<script', 'onclick', 'javascript:', '<style'):
            self.assertNotIn(bad, body)
        self.assertIn('hola', body)

    def test_shortcut_classes_and_own_styles(self):
        body = render_body('<a class="boton" href="https://x.example/">Inscríbete</a>\n\n'
                           '<div class="note">Recuadro</div>\n\n<span style="color:#d52020">rojo</span>')
        self.assertIn('background-color:#1958c1;color:#ffffff', body)
        self.assertNotIn('color:#1958c1;display', body)  # a button does not get the colour of a link
        self.assertIn('background-color:#f8f8f8;border:1px solid #cccccc', body)
        self.assertIn('color:#d52020', body)

    def test_code_inside_pre_has_no_inline_background(self):
        body = render_body('```\nint main() {}\n```\n\nY `x` en línea.')
        self.assertRegex(body, r'<pre style="[^"]*"><code style="font-family:Consolas')
        self.assertIn('background-color:#f3f3f3', body)

    def test_preview_and_test_use_the_name_and_a_link_to_the_site(self):
        subject, page, text = render_email(self.mail(), username='ana')
        self.assertEqual(subject, 'Concurso de otoño')
        self.assertIn('Hola ana, te esperamos.', page)
        self.assertIn('Hola ana, te esperamos.', text)
        self.assertNotIn('{{', page + text)
        self.assertIn('https://judge.example/static/icons/logo-correo.png', page)

    def test_for_resend_the_name_and_unsubscribe_link_are_its_variables(self):
        subject, page, text = render_email(self.mail(), for_resend=True)
        self.assertIn('Hola {{{contact.first_name}}}, te esperamos.', page)
        self.assertIn('href="{{{RESEND_UNSUBSCRIBE_URL}}}"', page)
        self.assertIn('{{{RESEND_UNSUBSCRIBE_URL}}}', text)

    def test_the_name_is_escaped(self):
        subject, page, text = render_email(self.mail(), username='<b>x</b>')
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', page)

    def test_footer_language(self):
        page = render_email(self.mail(language='es'))[1]
        self.assertIn('Recibes este correo porque tienes una cuenta en', page)
        self.assertIn('Darme de baja de estos correos', page)
        self.assertIn('mailto:contacto@example.com', page)
        page = render_email(self.mail(language='en'))[1]
        self.assertIn('You receive this email because you have an account on', page)

    def test_title_and_preview_text_are_optional(self):
        page = render_email(self.mail())[1]
        self.assertNotIn('<h1 class="title"', page)
        page = render_email(self.mail(heading='Gran final', preheader='Sábado 9:00'))[1]
        self.assertIn('<h1 class="title"', page)
        self.assertIn('Gran final</h1>', page)
        self.assertIn('Sábado 9:00', page)

    def test_math_arrives_as_text(self):
        self.assertIn('x^2', render_body('Calcula ~x^2~.'))

    def test_plain_text_version(self):
        text = html_to_text(render_body('## Fecha\n\n- uno\n- dos\n\nVer [aquí](https://x.example/a).\n\n'
                                        '```\n  a  b\n```'))
        self.assertIn('Fecha', text)
        self.assertIn('- uno\n- dos', text)
        self.assertIn('aquí (https://x.example/a)', text)
        self.assertIn('  a  b', text)


@override_settings(MIGRATION_MODULES={})
class MigrationTests(TestCase):
    def test_0158_only_creates_the_new_models_after_0157(self):
        from django.db import connection
        from django.db.migrations.loader import MigrationLoader
        loader = MigrationLoader(connection, ignore_no_migrations=True)
        migration = loader.get_migration('judge', '0158_mass_email')
        self.assertIn(('judge', '0157_arena'), migration.dependencies)
        self.assertEqual([(type(op).__name__, op.name) for op in migration.operations],
                         [('CreateModel', 'MassEmail'), ('CreateModel', 'MassEmailRecipient')])

    def test_the_permission_exists(self):
        self.assertTrue(Permission.objects.filter(content_type__app_label='judge',
                                                  codename='send_mass_email').exists())



@override_settings(**RESEND)
class ResendClientTests(TestCase):
    def test_waits_and_retries_when_resend_says_429(self):
        with fake_resend() as resend:
            resend.fail('POST', '/segments', 429, times=2)
            self.assertTrue(ResendClient().create_segment('x'))
            self.assertEqual(resend.count('POST', '/segments'), 3)

    def test_gives_up_after_five_attempts(self):
        with fake_resend() as resend:
            resend.fail('POST', '/segments', 503, times=10)
            with self.assertRaises(ResendError):
                ResendClient().create_segment('x')
            self.assertEqual(resend.count('POST', '/segments'), 5)

    def test_what_sends_emails_is_never_repeated_after_a_5xx(self):
        with fake_resend() as resend:
            segment = ResendClient().create_segment('x')
            resend.fail('POST', '/broadcasts', 500)
            with self.assertRaises(ResendError):
                ResendClient().create_broadcast({'segment_id': segment, 'send': True})
            self.assertEqual(resend.count('POST', '/broadcasts'), 1)
            resend.fail('POST', '/emails', 'network')
            with self.assertRaises(ResendError):
                ResendClient().send_email({'to': ['a@example.com']})
            self.assertEqual(resend.count('POST', '/emails'), 1)

    def test_client_errors_are_reported_with_the_message(self):
        with fake_resend() as resend:
            resend.fail('POST', '/segments', 403)
            with self.assertRaisesRegex(ResendError, 'injected 403'):
                ResendClient().create_segment('x')

    def test_contacts_are_read_page_by_page(self):
        with fake_resend() as resend:
            for index in range(205):
                resend.add_contact('p%d@Example.com' % index)
            contacts = ResendClient().contacts()
            self.assertEqual(len(contacts), 205)
            self.assertIn('p204@example.com', contacts)
            self.assertEqual(resend.count('GET', '/contacts'), 3)

    @override_settings(DMOJ_RESEND_API_KEY=None)
    def test_without_a_key_nothing_is_called(self):
        with self.assertRaises(ResendError):
            ResendClient()


@override_settings(**RESEND)
class SendTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = [make_user('dest%d' % index) for index in range(5)]
        cls.sender = make_user('remitente', is_staff=True, is_superuser=True)

    def setUp(self):
        cache.clear()

    def prepared(self, scheduled_at=None, **values):
        mail = MassEmail.objects.create(subject='Aviso', content='Hola {{usuario}}', status=MassEmail.SENDING,
                                        scheduled_at=scheduled_at, from_email='Club <contacto@example.com>',
                                        **values)
        for user in self.users:
            MassEmailRecipient.objects.create(mass_email=mail, profile=user.profile, username=user.username,
                                              email=user.email)
        return mail

    def run_task(self, mail):
        return send_mass_email.apply(args=[mail.pk])

    def test_hands_everybody_to_a_segment_and_sends_one_broadcast(self):
        mail = self.prepared(preheader='Resumen')
        with fake_resend() as resend:
            resend.add_contact('dest0@example.com')                  # already a contact: only added to the segment
            resend.add_contact('dest1@example.com', unsubscribed=True)  # unsubscribed: left out
            result = self.run_task(mail)
            self.assertTrue(result.successful(), result.result)
            mail.refresh_from_db()
            self.assertEqual(mail.status, MassEmail.SENT)
            self.assertEqual(mail.recipient_count, 4)
            self.assertEqual(len(resend.broadcasts), 1)
            broadcast = next(iter(resend.broadcasts.values()))
            self.assertEqual(broadcast['recipients'], ['dest0@example.com', 'dest2@example.com', 'dest3@example.com',
                                                       'dest4@example.com'])
            self.assertEqual(broadcast['from'], 'Club <contacto@example.com>')
            self.assertEqual(broadcast['reply_to'], 'contacto@example.com')
            self.assertEqual(broadcast['preview_text'], 'Resumen')
            self.assertEqual(broadcast['name'], 'cpcjudge-%d: Aviso' % mail.pk)
            self.assertIn('{{{contact.first_name}}}', broadcast['html'])
            self.assertIn('{{{RESEND_UNSUBSCRIBE_URL}}}', broadcast['html'])
            self.assertNotIn('scheduled_at', broadcast)
            self.assertEqual(resend.contacts[resend._contact('dest2@example.com')['id']]['first_name'], 'dest2')
        states = dict(mail.recipients.values_list('username', 'state'))
        self.assertEqual(states['dest1'], MassEmailRecipient.UNSUBSCRIBED)
        self.assertEqual(states['dest0'], MassEmailRecipient.ADDED)

    def test_scheduled(self):
        when = timezone.now() + timedelta(days=1)
        mail = self.prepared(scheduled_at=when)
        with fake_resend() as resend:
            self.run_task(mail)
            broadcast = next(iter(resend.broadcasts.values()))
            self.assertEqual(broadcast['scheduled_at'], when.astimezone(dt_timezone.utc).isoformat())
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.SCHEDULED)

    def test_does_nothing_unless_it_is_sending(self):
        mail = self.prepared()
        MassEmail.objects.filter(pk=mail.pk).update(status=MassEmail.DRAFT)
        with fake_resend() as resend:
            self.run_task(mail)
            self.assertEqual(resend.calls, [])

    def test_a_failure_is_recorded_and_a_retry_continues_without_repeating(self):
        mail = self.prepared()
        with fake_resend() as resend:
            resend.fail('POST', '/contacts', 422, times=1)      # the first person fails
            resend.fail('POST', '/broadcasts', 403, times=1)    # and then the broadcast is refused
            result = self.run_task(mail)
            self.assertFalse(result.successful())
            mail.refresh_from_db()
            self.assertEqual(mail.status, MassEmail.FAILED)
            self.assertIn('injected 403', mail.error)
            self.assertEqual(mail.recipients.filter(state=MassEmailRecipient.FAILED).count(), 1)
            segment = mail.resend_segment_id

            MassEmail.objects.filter(pk=mail.pk).update(status=MassEmail.SENDING)
            result = self.run_task(mail)
            self.assertTrue(result.successful(), result.result)
            mail.refresh_from_db()
            self.assertEqual(mail.status, MassEmail.SENT)
            self.assertEqual(mail.resend_segment_id, segment)
            self.assertEqual(len(resend.segments), 1)
            self.assertEqual(len(resend.broadcasts), 1)
            self.assertEqual(len(resend.segment_emails(segment)), 5)
            self.assertEqual(mail.recipient_count, 5)

    def test_a_lost_answer_never_sends_twice(self):
        mail = self.prepared()
        with fake_resend() as resend:
            resend.lose_broadcast_answer = True      # Resend created it, but the answer never arrived
            self.assertFalse(self.run_task(mail).successful())
            mail.refresh_from_db()
            self.assertEqual(mail.status, MassEmail.FAILED)
            self.assertTrue(mail.broadcast_requested)
            MassEmail.objects.filter(pk=mail.pk).update(status=MassEmail.SENDING)
            self.assertTrue(self.run_task(mail).successful())
            self.assertEqual(len(resend.broadcasts), 1)
            mail.refresh_from_db()
            self.assertEqual(mail.resend_broadcast_id, next(iter(resend.broadcasts)))
            self.assertEqual(resend.count('POST', '/broadcasts/'), 0)  # it was already queued: not sent again

    def test_a_broadcast_left_as_draft_is_sent_on_retry(self):
        mail = self.prepared()
        with fake_resend() as resend:
            resend.lose_broadcast_answer = True
            self.run_task(mail)
            only = next(iter(resend.broadcasts.values()))
            only['status'] = 'draft'
            MassEmail.objects.filter(pk=mail.pk).update(status=MassEmail.SENDING)
            self.assertTrue(self.run_task(mail).successful())
            self.assertEqual(only['status'], 'queued')
            self.assertEqual(len(resend.broadcasts), 1)

    def other(self, status, broadcast_status=None, **values):
        """An earlier email with its segment (and broadcast) already in Resend."""
        mail = MassEmail.objects.create(subject='Antes', content='x', status=status, **values)
        segment = self.resend._post_segments([], {'name': mail.broadcast_name}, {}).json()['id']
        mail.resend_segment_id = segment
        if broadcast_status:
            broadcast = self.resend._post_broadcasts([], {'segment_id': segment}, {}).json()['id']
            self.resend.broadcasts[broadcast]['status'] = broadcast_status
            mail.resend_broadcast_id = broadcast
        mail.save()
        return mail

    def test_segments_of_finished_emails_are_freed_for_the_free_plan(self):
        with fake_resend() as resend:
            self.resend = resend
            delivered = self.other(MassEmail.SENT, 'sent')
            scheduled = self.other(MassEmail.SCHEDULED, 'scheduled')
            cancelled = self.other(MassEmail.CANCELLED)
            self.assertEqual(len(resend.segments), 3)
            mail = self.prepared()
            self.assertTrue(self.run_task(mail).successful())
            mail.refresh_from_db()
            self.assertEqual(set(resend.segments), {scheduled.resend_segment_id, mail.resend_segment_id})
        delivered.refresh_from_db()
        cancelled.refresh_from_db()
        self.assertEqual((delivered.resend_segment_id, cancelled.resend_segment_id), ('', ''))

    def test_a_sending_broadcast_keeps_its_segment(self):
        with fake_resend() as resend:
            self.resend = resend
            for _ in range(3):
                self.other(MassEmail.SENT, 'sending')
            mail = self.prepared()
            self.assertFalse(self.run_task(mail).successful())
            mail.refresh_from_db()
            self.assertIn('maximum number of segments', mail.error)
            self.assertEqual(len(resend.segments), 3)

    def test_a_failed_email_whose_segment_was_freed_rebuilds_it_on_retry(self):
        with fake_resend() as resend:
            self.resend = resend
            failed = self.prepared()
            resend.fail('POST', '/broadcasts', 403, times=1)
            self.assertFalse(self.run_task(failed).successful())
            failed.refresh_from_db()
            # It asked for a broadcast and was refused: its segment is kept.
            self.assertTrue(failed.broadcast_requested)
            MassEmail.objects.filter(pk=failed.pk).update(broadcast_requested=False)
            self.other(MassEmail.SCHEDULED, 'scheduled')
            self.other(MassEmail.SCHEDULED, 'scheduled')
            mail = self.prepared()
            self.assertTrue(self.run_task(mail).successful())
            failed.refresh_from_db()
            self.assertEqual(failed.resend_segment_id, '')
            self.assertFalse(failed.recipients.filter(state=MassEmailRecipient.ADDED).exists())
            # Retrying it needs a segment again: one of the scheduled ones would have to go first.
            resend.segment_limit = None
            MassEmail.objects.filter(pk=failed.pk).update(status=MassEmail.SENDING)
            self.assertTrue(self.run_task(failed).successful())
            failed.refresh_from_db()
            self.assertEqual(len(resend.segment_emails(failed.resend_segment_id)), 5)

    def test_nobody_to_send_to_fails_without_a_broadcast(self):
        mail = self.prepared()
        with fake_resend() as resend:
            for user in self.users:
                resend.add_contact(user.email, unsubscribed=True)
            self.assertFalse(self.run_task(mail).successful())
            self.assertEqual(resend.broadcasts, {})
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.FAILED)


@override_settings(**RESEND)
class AdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.root = make_user('root', is_staff=True, is_superuser=True)
        cls.writer = make_user('writer', is_staff=True)
        cls.writer.user_permissions.add(Permission.objects.get(codename='send_mass_email'))
        cls.plain = make_user('plainstaff', is_staff=True)
        cls.people = [make_user('lector%d' % index) for index in range(3)]

    def setUp(self):
        cache.clear()

    def draft(self, **values):
        values.setdefault('subject', 'Gran final')
        values.setdefault('content', 'Hola {{usuario}}')
        return MassEmail.objects.create(created_by=self.writer.profile, **values)

    def form_data(self, **values):
        data = {'subject': 'Gran final', 'preheader': '', 'heading': 'La final', 'language': 'es',
                'content': 'Hola **{{usuario}}**', 'active_within_days': '', 'joined_after': '', 'joined_before': '',
                'min_problems': ''}
        data.update(values)
        return data

    # --- who can see it ---

    def test_menu_and_pages_for_superusers_and_the_permission(self):
        for user in (self.root, self.writer):
            self.client.force_login(user)
            page = self.client.get(reverse('admin:index')).content.decode('utf-8')
            self.assertIn('fa-envelope', page)
            self.assertIn('/admin/judge/massemail/unsubscribed/', page)
            self.assertEqual(self.client.get(reverse('admin:judge_massemail_changelist')).status_code, 200)
            self.assertEqual(self.client.get(reverse('admin:judge_massemail_add')).status_code, 200)

    def test_other_staff_see_nothing(self):
        mail = self.draft()
        self.client.force_login(self.plain)
        self.assertNotIn('fa-envelope', self.client.get(reverse('admin:index')).content.decode('utf-8'))
        for url in (reverse('admin:judge_massemail_changelist'), reverse('admin:judge_massemail_add'),
                    reverse('admin:judge_massemail_change', args=[mail.pk]),
                    reverse('admin:judge_massemail_send', args=[mail.pk]),
                    reverse('admin:judge_massemail_email', args=[mail.pk]),
                    reverse('admin:judge_massemail_unsubscribed')):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        for url in (reverse('admin:judge_massemail_count'), reverse('admin:judge_massemail_preview'),
                    reverse('admin:judge_massemail_fragment'),
                    reverse('admin:judge_massemail_duplicate', args=[mail.pk])):
            self.assertEqual(self.client.post(url, self.form_data()).status_code, 403, url)

    def test_spanish_labels(self):
        self.client.force_login(self.root)
        page = self.client.get(reverse('admin:judge_massemail_add'), HTTP_ACCEPT_LANGUAGE='es').content.decode()
        for text in ('Destinatarios', 'Guardar y pasar al envío', 'Lo recibirán:', 'Cómo darle formato',
                     'Participó en alguno de estos concursos'):
            self.assertIn(text, page)
        # Nothing to create a contest, organization or group from here, and no «hold down Control» note.
        for field in ('contests', 'organizations', 'groups'):
            self.assertNotIn('id="add_id_%s"' % field, page)
        self.assertNotIn('Mantenga presionado', page)

    # --- writing ---

    def test_save_a_draft(self):
        self.client.force_login(self.writer)
        response = self.client.post(reverse('admin:judge_massemail_add'), self.form_data(_continue='1'))
        self.assertEqual(response.status_code, 302)
        mail = MassEmail.objects.get()
        self.assertEqual(mail.status, MassEmail.DRAFT)
        self.assertEqual(mail.created_by, self.writer.profile)

    def test_the_name_cannot_go_in_the_subject(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse('admin:judge_massemail_add'),
                                    self.form_data(subject='Hola {{usuario}}', _continue='1'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MassEmail.objects.exists())

    def test_live_count(self):
        self.client.force_login(self.root)
        data = self.client.post(reverse('admin:judge_massemail_count'), {}).json()
        self.assertEqual(data['count'], Profile.objects.filter(user__is_active=True).count())
        self.assertFalse(data['filtered'])
        data = self.client.post(reverse('admin:judge_massemail_count'),
                                {'include_users': [self.people[0].profile.pk], 'min_problems': '1'}).json()
        self.assertEqual({key: data[key] for key in ('count', 'filtered', 'sample')}, {'count': 1, 'filtered': True, 'sample': ['lector0']})
        data = self.client.post(reverse('admin:judge_massemail_count'),
                                {'joined_after': '2026-05-02', 'joined_before': '2026-05-01'}).json()
        self.assertTrue(data['errors'])

    def test_recipient_list_hides_addresses_from_non_superusers(self):
        self.client.force_login(self.writer)
        page = self.client.post(reverse('admin:judge_massemail_recipients'), {}).content.decode()
        self.assertIn('lector1', page)
        self.assertNotIn('lector1@example.com', page)
        self.client.force_login(self.root)
        self.assertIn('lector1@example.com',
                      self.client.post(reverse('admin:judge_massemail_recipients'), {}).content.decode())

    def test_preview_is_isolated_and_shows_the_email(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse('admin:judge_massemail_preview'),
                                    self.form_data(content='Hola **{{usuario}}** <script>alert(1)</script>'))
        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'none'", response['Content-Security-Policy'])
        page = response.content.decode()
        self.assertIn('sandbox=""', page)
        self.assertIn('Hola &lt;strong', page)          # the email goes escaped inside srcdoc
        self.assertIn('root', page)
        self.assertNotIn('<script>alert', page)

    def test_editor_preview_fragment(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse('admin:judge_massemail_fragment'), {'content': '# Hola'})
        self.assertIn('font-size:20px', response.content.decode())

    def test_send_me_a_test(self):
        self.client.force_login(self.root)
        mail = self.draft()
        with fake_resend() as resend:
            response = self.client.post(reverse('admin:judge_massemail_change', args=[mail.pk]),
                                        self.form_data(_send_test='1'))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(resend.emails), 1)
            sent = resend.emails[0]
            self.assertEqual(sent['to'], ['root@example.com'])
            self.assertIn('Gran final', sent['subject'])
            self.assertIn('<strong>root</strong>', sent['html'])
            self.assertEqual(resend.broadcasts, {})

    @override_settings(DMOJ_RESEND_API_KEY=None)
    def test_without_the_key_drafts_work_but_nothing_is_sent(self):
        self.client.force_login(self.root)
        mail = self.draft()
        page = self.client.get(reverse('admin:judge_massemail_change', args=[mail.pk])).content.decode()
        self.assertIn('DMOJ_RESEND_API_KEY', page)
        response = self.client.post(reverse('admin:judge_massemail_send', args=[mail.pk]),
                                    {'confirm': '4', 'when': 'now'})
        self.assertEqual(response.status_code, 200)
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.DRAFT)

    # --- sending ---

    def send(self, mail, **data):
        with mock.patch('judge.tasks.send_mass_email.apply_async') as queued:
            response = self.client.post(reverse('admin:judge_massemail_send', args=[mail.pk]), data)
        return response, queued

    def test_confirmation_page(self):
        self.client.force_login(self.root)
        mail = self.draft()
        with fake_resend() as resend:
            resend.add_contact('lector2@example.com', unsubscribed=True)
            page = self.client.get(reverse('admin:judge_massemail_send', args=[mail.pk])).content.decode()
        total = Profile.objects.filter(user__is_active=True).count()
        self.assertIn('<span class="mass-email-number">%d</span>' % (total - 1), page)
        self.assertIn('Club &lt;contacto@example.com&gt;', page)

    def test_wrong_number_does_not_send(self):
        self.client.force_login(self.root)
        mail = self.draft()
        with fake_resend():
            response, queued = self.send(mail, confirm='999', when='now')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(queued.called)
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.DRAFT)

    def test_send_fixes_the_list_and_queues_the_task(self):
        self.client.force_login(self.writer)
        mail = self.draft()
        mail.include_users.set([self.people[1].profile])
        mail.min_problems = 50
        mail.save()
        with fake_resend():
            response, queued = self.send(mail, confirm='1', when='now')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/tasks/status/', response['Location'])
        queued.assert_called_once()
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.SENDING)
        self.assertEqual(mail.sent_by, self.writer.profile)
        self.assertEqual(queued.call_args.kwargs['task_id'], mail.task_id)
        self.assertEqual(list(mail.recipients.values_list('username', 'state')),
                         [('lector1', MassEmailRecipient.PENDING)])
        # It is no longer editable, and a second send does nothing.
        response = self.client.post(reverse('admin:judge_massemail_change', args=[mail.pk]), self.form_data())
        self.assertEqual(response.status_code, 403)
        with fake_resend():
            response, queued = self.send(mail, confirm='1', when='now')
        self.assertFalse(queued.called)

    def test_schedule(self):
        self.client.force_login(self.root)
        mail = self.draft()
        mail.include_users.set([self.people[0].profile])
        mail.min_problems = 50
        mail.save()
        # The time is read in the time zone of whoever sends it, as the page says.
        zone = timezone.get_default_timezone().__class__(self.root.profile.timezone)
        moment = timezone.localtime(timezone.now() + timedelta(days=2), zone).strftime('%Y-%m-%dT%H:%M')
        with fake_resend():
            response, queued = self.send(mail, confirm='1', when='later', scheduled_at=moment)
        self.assertEqual(response.status_code, 302)
        mail.refresh_from_db()
        self.assertEqual(timezone.localtime(mail.scheduled_at, zone).strftime('%Y-%m-%dT%H:%M'), moment)
        too_soon = timezone.localtime(timezone.now() + timedelta(minutes=1), zone).strftime('%Y-%m-%dT%H:%M')
        other = self.draft()
        with fake_resend():
            response, queued = self.send(other, confirm=str(Profile.objects.filter(user__is_active=True).count()),
                                         when='later', scheduled_at=too_soon)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(queued.called)

    def test_full_flow_with_the_task(self):
        """From the confirmation to Resend, running the task for real (eagerly)."""
        self.client.force_login(self.root)
        mail = self.draft()
        total = Profile.objects.filter(user__is_active=True).count()
        with fake_resend() as resend:
            with mock.patch('judge.tasks.send_mass_email.apply_async',
                            side_effect=lambda args, task_id: send_mass_email.apply(args=args, task_id=task_id)):
                response = self.client.post(reverse('admin:judge_massemail_send', args=[mail.pk]),
                                            {'confirm': str(total), 'when': 'now'})
            self.assertEqual(response.status_code, 302)
            mail.refresh_from_db()
            self.assertEqual(mail.status, MassEmail.SENT)
            self.assertEqual(len(next(iter(resend.broadcasts.values()))['recipients']), total)
            page = self.client.get(reverse('admin:judge_massemail_change', args=[mail.pk])).content.decode()
            self.assertIn('Entregado a Resend el', page)
            resend.events[mail.resend_broadcast_id] = {'delivered': ['a', 'b'], 'bounced': ['c']}
            self.client.post(reverse('admin:judge_massemail_stats', args=[mail.pk]))
            mail.refresh_from_db()
            self.assertEqual(json.loads(mail.stats)['delivered'], 2)
            page = self.client.get(reverse('admin:judge_massemail_change', args=[mail.pk])).content.decode()
            self.assertIn('Entregados', page)
            sent_to = self.client.get(reverse('admin:judge_massemail_sent_to', args=[mail.pk])).content.decode()
            self.assertIn('lector0', sent_to)

    def test_failed_send_can_be_retried(self):
        self.client.force_login(self.root)
        mail = self.draft(status=MassEmail.FAILED, error='boom')
        with mock.patch('judge.tasks.send_mass_email.apply_async') as queued:
            response = self.client.post(reverse('admin:judge_massemail_retry', args=[mail.pk]))
        self.assertEqual(response.status_code, 302)
        queued.assert_called_once()
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.SENDING)
        with mock.patch('judge.tasks.send_mass_email.apply_async') as queued:
            self.client.post(reverse('admin:judge_massemail_retry', args=[mail.pk]))
        self.assertFalse(queued.called)

    def test_an_email_stuck_sending_can_be_retried_after_15_minutes(self):
        self.client.force_login(self.root)
        mail = self.draft(status=MassEmail.SENDING)
        with mock.patch('judge.tasks.send_mass_email.apply_async') as queued:
            self.client.post(reverse('admin:judge_massemail_retry', args=[mail.pk]))
        self.assertFalse(queued.called)
        page = self.client.get(reverse('admin:judge_massemail_change', args=[mail.pk])).content.decode()
        self.assertNotIn('/retry/', page)
        MassEmail.objects.filter(pk=mail.pk).update(updated=timezone.now() - timedelta(minutes=20))
        page = self.client.get(reverse('admin:judge_massemail_change', args=[mail.pk])).content.decode()
        self.assertIn('/retry/', page)
        with mock.patch('judge.tasks.send_mass_email.apply_async') as queued:
            self.client.post(reverse('admin:judge_massemail_retry', args=[mail.pk]))
        queued.assert_called_once()

    def test_queue_down_marks_it_failed(self):
        self.client.force_login(self.root)
        mail = self.draft()
        mail.include_users.set([self.people[0].profile])
        mail.min_problems = 50
        mail.save()
        with fake_resend(), mock.patch('judge.tasks.send_mass_email.apply_async', side_effect=OSError('redis')):
            response = self.client.post(reverse('admin:judge_massemail_send', args=[mail.pk]),
                                        {'confirm': '1', 'when': 'now'})
        self.assertEqual(response.status_code, 302)
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.FAILED)

    def test_cancel_a_scheduled_email(self):
        self.client.force_login(self.root)
        with fake_resend() as resend:
            segment = resend._post_segments([], {'name': 'x'}, {}).json()['id']
            broadcast = resend._post_broadcasts([], {'segment_id': segment, 'send': True,
                                                     'scheduled_at': 'in 1 day'}, {}).json()['id']
            mail = self.draft(status=MassEmail.SCHEDULED, resend_broadcast_id=broadcast)
            self.client.post(reverse('admin:judge_massemail_cancel', args=[mail.pk]))
            self.assertEqual(resend.broadcasts[broadcast]['status'], 'draft')
        mail.refresh_from_db()
        self.assertEqual(mail.status, MassEmail.CANCELLED)

    def test_duplicate(self):
        self.client.force_login(self.writer)
        mail = self.draft(status=MassEmail.SENT, min_problems=4)
        mail.include_users.set([self.people[0].profile])
        response = self.client.post(reverse('admin:judge_massemail_duplicate', args=[mail.pk]))
        copy = MassEmail.objects.exclude(pk=mail.pk).get()
        self.assertEqual(response['Location'], reverse('admin:judge_massemail_change', args=[copy.pk]))
        self.assertEqual((copy.status, copy.subject, copy.min_problems), (MassEmail.DRAFT, mail.subject, 4))
        self.assertEqual(list(copy.include_users.all()), [self.people[0].profile])
        self.assertEqual(copy.created_by, self.writer.profile)

    def test_sent_email_page(self):
        self.client.force_login(self.root)
        mail = self.draft(status=MassEmail.SENT, recipient_count=3, sent_at=timezone.now())
        response = self.client.get(reverse('admin:judge_massemail_email', args=[mail.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'none'", response['Content-Security-Policy'])
        page = self.client.get(reverse('admin:judge_massemail_change', args=[mail.pk])).content.decode()
        self.assertIn('Duplicar como borrador nuevo', page)
        self.assertNotIn('name="_to_send"', page)

    def test_unsubscribed_people_and_subscribing_again(self):
        self.client.force_login(self.root)
        with fake_resend() as resend:
            gone = resend.add_contact('lector0@example.com', unsubscribed=True)
            resend.add_contact('lector1@example.com')
            page = self.client.get(reverse('admin:judge_massemail_unsubscribed')).content.decode()
            self.assertIn('lector0@example.com', page)
            self.assertIn('lector0', page)
            self.assertNotIn('lector1@example.com', page)
            self.client.post(reverse('admin:judge_massemail_unsubscribed'),
                             {'contact': gone, 'email': 'lector0@example.com'})
            self.assertFalse(resend.contacts[gone]['unsubscribed'])

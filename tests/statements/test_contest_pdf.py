"""Every statement of a contest in one PDF: who gets it, what it holds, its cache and the page shown while it
is being made. pdfoid is replaced by a stub that records what it gets.
"""
import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

import judge.views.contests as contest_views
from arena_testutil import duel, organizer, online_judge, player, problem, start
from judge.models import Contest, ContestParticipation, ProblemTranslation
from judge.models.arena import ArenaGame
from judge.models.tests.util import create_contest, create_contest_participation, create_contest_problem, \
    create_problem, create_user

FAKE_PDF = b'%PDF-1.4 contest'


# ---------------------------------------------------------------------------------------------------------------
# Every statement of a contest in one PDF
# ---------------------------------------------------------------------------------------------------------------

class ContestPdfTestCase(TestCase):
    fixtures = ['language_small.json']

    @classmethod
    def setUpTestData(cls):
        cls.author = create_user(username='autorpdf', is_staff=True,
                                 user_permissions=('edit_own_contest',))
        cls.tester = create_user(username='testerpdf')
        cls.participant = create_user(username='concursante')
        cls.outsider = create_user(username='ajeno')
        cls.other_participant = create_user(username='deotro')
        cls.superuser = create_user(username='superpdf', is_staff=True, is_superuser=True)
        cls.second = create_problem(code='pdfsegundo', name='Segundo problema', is_public=False,
                                    description='Calcula ~a+b~ con **cuidado**.', time_limit=2, memory_limit=262144)
        cls.first = create_problem(code='pdfprimero', name='Primer problema', is_public=False,
                                   description='Cuenta los pares de ~S~.')
        ProblemTranslation.objects.create(problem=cls.first, language='en', name='First problem',
                                          description='Count the pairs of ~S~.')
        now = timezone.now()
        cls.contest = create_contest(key='pdfconcurso', name='Concurso <b>de prueba</b>',
                                     start_time=now - timedelta(hours=1), end_time=now + timedelta(hours=4),
                                     is_visible=True, authors=('autorpdf',), testers=('testerpdf',))
        # The order of the contest, not the codes: «primero» first, although «pdfprimero» > «pdfsegundo» is false.
        create_contest_problem(contest=cls.contest, problem=cls.first, order=1)
        create_contest_problem(contest=cls.contest, problem=cls.second, order=2)
        cls.other = create_contest(key='pdfotro', name='Otro', start_time=now - timedelta(hours=1),
                                   end_time=now + timedelta(hours=4), is_visible=True)
        create_contest_problem(contest=cls.other, problem=create_problem(code='pdfotroproblema'), order=1)
        cls.enter(cls.participant, cls.contest)
        cls.enter(cls.other_participant, cls.other)

    @staticmethod
    def enter(user, contest):
        participation = create_contest_participation(contest=contest, user=user.profile)
        user.profile.current_contest = participation
        user.profile.save(update_fields=['current_contest'])
        return participation

    def setUp(self):
        cache.clear()
        self.folder = tempfile.mkdtemp(prefix='contest-pdf-cache-')
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.calls = []
        self.fail_render = False

        def fake_render(*, title, html, footer=False, timeout=None):
            self.calls.append({'title': title, 'html': html, 'footer': footer, 'timeout': timeout})
            if self.fail_render:
                raise RuntimeError('pdfoid is down')
            return FAKE_PDF

        for patcher in (mock.patch.object(contest_views, 'PDF_RENDERING_ENABLED', True),
                        mock.patch.object(contest_views, 'render_pdf', fake_render)):
            patcher.start()
            self.addCleanup(patcher.stop)
        override = override_settings(DMOJ_PDF_PROBLEM_CACHE=self.folder)
        override.enable()
        self.addCleanup(override.disable)

    @property
    def url(self):
        return reverse('contest_problems_pdf', args=[self.contest.key])

    def get(self, user, url=None, lang='es'):
        self.client.force_login(user)
        return self.client.get(url or self.url, HTTP_ACCEPT_LANGUAGE=lang)

    def body(self, response):
        return b''.join(response.streaming_content) if response.streaming else response.content

    def cached(self):
        return sorted(os.listdir(self.folder))


class ContestPdfTests(ContestPdfTestCase):
    def test_a_participant_downloads_every_statement_in_one_pdf(self):
        response = self.get(self.participant)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename=pdfconcurso.pdf')
        self.assertEqual(self.body(response), FAKE_PDF)
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual(call['title'], 'Concurso <b>de prueba</b>')
        self.assertTrue(call['footer'], 'page numbers')
        self.assertEqual(call['timeout'], contest_views.CONTEST_PDF_RENDER_TIMEOUT)
        self.assertLess(sum(call['timeout']), 120, 'gives up before uWSGI kills the worker')
        html = call['html']
        self.assertIn('<title>Concurso &lt;b&gt;de prueba&lt;/b&gt;</title>', html)
        # Both statements, in the order of the contest, each on a page of its own, with its label and limits.
        self.assertLess(html.index('Cuenta los pares'), html.index('Calcula'))
        self.assertEqual(html.count('class="contest-problem"'), 2)
        self.assertIn('<span class="problem-label">1</span>Primer problema', html)
        self.assertIn('<span class="problem-label">2</span>Segundo problema', html)
        self.assertIn('<strong>cuidado</strong>', html)
        self.assertIn('Límite de tiempo:', html)
        self.assertIn('Límite de memoria:', html)
        self.assertIn("classList.add('math-loaded')", html)
        self.assertNotIn('"//', html)

    def test_it_is_rendered_once_and_then_served_from_the_cache(self):
        self.get(self.participant)
        names = self.cached()
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r'^contest-pdfconcurso\.es\.[0-9a-f]{16}\.pdf$')
        again = self.get(self.participant)
        self.assertEqual(self.body(again), FAKE_PDF)
        self.assertEqual(len(self.calls), 1, 'the second request comes from the cache')

    def test_editing_a_statement_makes_a_new_pdf_and_drops_the_old_one(self):
        self.get(self.participant)
        before = self.cached()
        self.second.description = 'Calcula ~a-b~.'
        self.second.save()
        self.get(self.participant)
        self.assertEqual(len(self.calls), 2)
        after = self.cached()
        self.assertEqual(len(after), 1)
        self.assertNotEqual(before, after)
        self.assertIn('a-b', self.calls[1]['html'])

    def test_each_language_has_its_own_pdf(self):
        self.get(self.participant, lang='es')
        self.get(self.participant, lang='en')
        self.assertEqual(len(self.calls), 2)
        english = self.calls[1]['html']
        self.assertIn('Time limit:', english)
        self.assertIn('First problem', english)
        self.assertIn('Count the pairs', english)
        self.assertNotIn('First problem', self.calls[0]['html'])
        self.assertEqual([name.split('.')[1] for name in self.cached()], ['en', 'es'])

    def test_problem_pdfs_and_contest_pdfs_do_not_touch_each_other(self):
        self.get(self.participant)
        problem_pdf = os.path.join(self.folder, 'pdfprimero.es.pdf')
        with open(problem_pdf, 'wb') as handle:
            handle.write(b'%PDF problem')
        self.second.description = 'Otro enunciado.'
        self.second.save()
        self.get(self.participant)
        self.assertIn('pdfprimero.es.pdf', self.cached(), 'the contest PDF only removes its own old versions')
        # Saving a problem removes its own PDF, as before, and leaves the contest one alone.
        self.first.save()
        self.assertNotIn('pdfprimero.es.pdf', self.cached())
        self.assertEqual(len([name for name in self.cached() if name.startswith('contest-')]), 1)


class ContestPdfAccessTests(ContestPdfTestCase):
    def test_organizers_can_prepare_it_at_any_time(self):
        Contest.objects.filter(pk=self.contest.pk).update(start_time=timezone.now() + timedelta(days=1),
                                                          end_time=timezone.now() + timedelta(days=2))
        for user in (self.author, self.tester, self.superuser):
            self.assertEqual(self.get(user).status_code, 200, user.username)

    def test_whoever_is_not_inside_the_contest_gets_nothing(self):
        for user in (self.outsider, self.other_participant):
            self.assertEqual(self.get(user).status_code, 404, user.username)
        self.client.logout()
        anonymous = self.client.get(self.url)
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn(reverse('auth_login'), anonymous['Location'])
        self.assertEqual(self.calls, [])

    def test_a_participant_gets_nothing_before_the_contest_starts(self):
        Contest.objects.filter(pk=self.contest.pk).update(start_time=timezone.now() + timedelta(days=1),
                                                          end_time=timezone.now() + timedelta(days=2))
        self.assertEqual(self.get(self.participant).status_code, 404)
        self.assertEqual(self.calls, [])

    def test_a_spectator_inside_the_contest_can_download_it(self):
        spectator = create_user(username='espectador')
        participation = create_contest_participation(contest=self.contest, user=spectator.profile,
                                                     virtual=ContestParticipation.SPECTATE)
        spectator.profile.current_contest = participation
        spectator.profile.save(update_fields=['current_contest'])
        self.assertEqual(self.get(spectator).status_code, 200)

    def test_never_for_the_hidden_contest_of_an_arena_game(self):
        arena_organizer = organizer('orgpdf')
        arena_problem = problem('pdfarena')
        online_judge(arena_problem)
        match = duel(arena_organizer, player('jugpdf1'), player('jugpdf2'), [arena_problem])
        game = ArenaGame.objects.get(match=match)
        start(arena_organizer, match, game)
        game.refresh_from_db()
        url = reverse('contest_problems_pdf', args=[game.contest.key])
        self.assertEqual(self.get(self.superuser, url).status_code, 404)
        self.assertEqual(self.calls, [])

    def test_without_pdfoid_there_is_no_pdf_and_no_button(self):
        with mock.patch.object(contest_views, 'PDF_RENDERING_ENABLED', False):
            self.assertEqual(self.get(self.participant).status_code, 404)
            self.assertNotIn('contest-pdf-download', self.get(self.participant, reverse('problem_list'))
                             .content.decode('utf-8'))
        self.assertEqual(self.calls, [])


class ContestPdfRenderingTests(ContestPdfTestCase):
    def test_while_somebody_renders_it_the_others_get_a_page_that_retries(self):
        self.get(self.participant)
        self.second.description = 'Cambio.'
        self.second.save()
        with mock.patch.object(contest_views.cache, 'add', return_value=False):
            waiting = self.get(self.participant)
        self.assertEqual(waiting.status_code, 202)
        self.assertEqual(waiting['Refresh'], str(contest_views.CONTEST_PDF_RETRY_SECONDS))
        self.assertEqual(waiting['Cache-Control'], 'no-store')
        self.assertIn('Se está preparando el PDF con todos los enunciados', waiting.content.decode('utf-8'))
        self.assertEqual(len(self.calls), 1, 'no second render while the first one runs')

    def test_the_lock_is_released_after_rendering(self):
        self.get(self.participant)
        self.assertFalse([key for key in cache._cache if 'contest_pdf:' in str(key)]
                         if hasattr(cache, '_cache') else [])

    def test_a_failed_render_leaves_nothing_and_the_next_request_tries_again(self):
        self.fail_render = True
        failed = self.get(self.participant)
        self.assertEqual(failed.status_code, 503)
        self.assertIn('No se pudo preparar el PDF', failed.content.decode('utf-8'))
        self.assertEqual(self.cached(), [])
        self.fail_render = False
        self.assertEqual(self.get(self.participant).status_code, 200)
        self.assertEqual(len(self.calls), 2)

    def test_without_a_cache_folder_it_is_rendered_every_time(self):
        with override_settings(DMOJ_PDF_PROBLEM_CACHE=None):
            for _ in range(2):
                response = self.get(self.participant)
                self.assertEqual(self.body(response), FAKE_PDF)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.cached(), [])


class ContestPdfButtonTests(ContestPdfTestCase):
    def test_the_problem_list_of_a_participant_ends_with_the_button(self):
        page = self.get(self.participant, reverse('problem_list')).content.decode('utf-8')
        self.assertIn('contest-pdf-download', page)
        self.assertIn('href="%s"' % self.url, page)
        self.assertIn('Descargar todos los enunciados (PDF)', page)
        self.assertGreater(page.index('contest-pdf-download'), page.index('id="problem-table"'))

    def test_outside_a_contest_the_list_has_no_button(self):
        page = self.get(self.outsider, reverse('problem_list')).content.decode('utf-8')
        self.assertNotIn('contest-pdf-download', page)

    def test_the_contest_page_shows_it_to_organizers(self):
        page = self.get(self.author, reverse('contest_view', args=[self.contest.key])).content.decode('utf-8')
        self.assertIn('href="%s"' % self.url, page)
        page = self.get(self.outsider, reverse('contest_view', args=[self.contest.key])).content.decode('utf-8')
        self.assertNotIn('contest-pdf-download', page)

    def test_after_the_contest_whoever_left_it_sees_no_button(self):
        Contest.objects.filter(pk=self.contest.pk).update(start_time=timezone.now() - timedelta(days=2),
                                                          end_time=timezone.now() - timedelta(days=1))
        page = self.get(self.outsider, reverse('contest_view', args=[self.contest.key])).content.decode('utf-8')
        self.assertIn('contest-problems', page)
        self.assertNotIn('contest-pdf-download', page)

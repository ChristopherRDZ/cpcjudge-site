"""Problem PDFs: what the site does once DMOJ_PDF_PDFOID_URL and DMOJ_PDF_PROBLEM_CACHE are set.

pdfoid itself is replaced by a stub that records the HTML it gets.
"""
import os
import shutil
import tempfile
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

import judge.views.problem as problem_views
from judge.models.tests.util import create_problem

FAKE_PDF = b'%PDF-1.4 fake'


class PdfTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.public = create_problem(code='pdfpublico', name='Palíndromos <b>& más</b>', is_public=True,
                                    description='Cuenta los palíndromos de ~S~.')
        cls.private = create_problem(code='pdfprivado', name='Secreto', is_public=False,
                                     description='Problema de un concurso que no ha empezado.')

    def setUp(self):
        cache.clear()
        self.folder = tempfile.mkdtemp(prefix='pdf-cache-')
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.calls = []

        def fake_render(*, title, html, footer=False):
            self.calls.append({'title': title, 'html': html})
            return FAKE_PDF

        for patcher in (mock.patch.object(problem_views, 'PDF_RENDERING_ENABLED', True),
                        mock.patch.object(problem_views, 'render_pdf', fake_render)):
            patcher.start()
            self.addCleanup(patcher.stop)
        settings = override_settings(DMOJ_PDF_PROBLEM_CACHE=self.folder)
        settings.enable()
        self.addCleanup(settings.disable)

    def get(self, url, lang='es'):
        return self.client.get(url, HTTP_ACCEPT_LANGUAGE=lang)


class PdfTests(PdfTestCase):
    def test_the_pdf_is_rendered_once_and_then_served_from_the_cache(self):
        response = self.get('/problem/pdfpublico/pdf')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertEqual(b''.join(response.streaming_content) if response.streaming else response.content, FAKE_PDF)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(os.path.exists(os.path.join(self.folder, 'pdfpublico.es.pdf')))

        again = self.get('/problem/pdfpublico/pdf')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.calls), 1, 'the second request must come from the cache')

    def test_the_title_of_the_pdf_is_the_name_of_the_problem(self):
        self.get('/problem/pdfpublico/pdf')
        html = self.calls[0]['html']
        self.assertIn('<title>Palíndromos &lt;b&gt;&amp; más&lt;/b&gt;</title>', html)
        self.assertEqual(self.calls[0]['title'], 'Palíndromos <b>& más</b>')
        # What pdfoid waits for before printing.
        self.assertIn("classList.add('math-loaded')", html)

    def test_each_language_has_its_own_pdf(self):
        self.get('/problem/pdfpublico/pdf/en')
        self.get('/problem/pdfpublico/pdf/es')
        self.assertIn('Time limit:', self.calls[0]['html'])
        self.assertIn('Límite de tiempo:', self.calls[1]['html'])
        self.assertEqual(sorted(os.listdir(self.folder)), ['pdfpublico.en.pdf', 'pdfpublico.es.pdf'])
        self.assertEqual(self.get('/problem/pdfpublico/pdf/xx').status_code, 404)

    def test_a_private_problem_is_not_rendered_for_whoever_cannot_see_it(self):
        self.assertEqual(self.get('/problem/pdfprivado/pdf').status_code, 404)
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(self.folder), [])

    def test_saving_the_problem_removes_its_pdfs(self):
        self.get('/problem/pdfpublico/pdf/en')
        self.get('/problem/pdfpublico/pdf/es')
        self.public.description = 'Ahora con otro texto.'
        self.public.save()
        self.assertEqual(os.listdir(self.folder), [])
        self.get('/problem/pdfpublico/pdf/es')
        self.assertIn('Ahora con otro texto.', self.calls[-1]['html'])

    def test_a_failed_render_leaves_nothing_in_the_cache(self):
        # Upstream opened the file before rendering: a failure (pdfoid down, MathJax not loading) left an empty PDF
        # that was then served to everybody until the problem was saved again.
        def broken(*, title, html, footer=False):
            raise RuntimeError('PDF rendering timed out')

        with mock.patch.object(problem_views, 'render_pdf', broken):
            with self.assertRaises(RuntimeError):
                self.get('/problem/pdfpublico/pdf')
        self.assertEqual(os.listdir(self.folder), [])
        # The next request renders it properly.
        response = self.get('/problem/pdfpublico/pdf')
        self.assertEqual(response.content, FAKE_PDF)
        self.assertEqual(os.listdir(self.folder), ['pdfpublico.es.pdf'])

    def test_the_problem_page_links_the_pdf(self):
        page = self.get('/problem/pdfpublico').content.decode()
        self.assertIn('/problem/pdfpublico/pdf', page)
        self.assertIn('view-pdf', page)


class PdfDisabledTests(TestCase):
    """As today: without pdfoid there is neither link nor PDF."""

    @classmethod
    def setUpTestData(cls):
        create_problem(code='pdfapagado', name='Apagado', is_public=True, description='Texto.')

    def test_no_link_and_no_pdf(self):
        cache.clear()
        self.assertFalse(problem_views.PDF_RENDERING_ENABLED)
        self.assertNotIn('view-pdf', self.client.get('/problem/pdfapagado').content.decode())
        self.assertEqual(self.client.get('/problem/pdfapagado/pdf').status_code, 404)

"""Interactive problems from the problem data page; run only with isolated SQLite or disposable MariaDB settings.

Covers the interactor upload field end to end in Django: the generated init.yml,
testlib detection and the bundled header, validation, what happens on later
saves, replacing and clearing files, renaming the problem, the pages and who may
see them. Grading itself is up to the judge and is not exercised here.
"""
import io
import os
import re
import zipfile

import yaml

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from judge.models import Problem, ProblemData, problem_data_storage
from judge.models.tests.util import create_problem, create_problem_group, create_problem_type, create_user
from judge.utils.problem_data import INTERACTIVE_EXAMPLE_DIR, TESTLIB_PATH

CASE_FIELDS = ('id', 'order', 'type', 'input_file', 'output_file', 'points', 'is_pretest',
               'output_prefix', 'output_limit', 'checker', 'checker_args', 'generator_args',
               'batch_dependencies', 'DELETE')


def build_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def payload(rows, initial=0, **data):
    """The POST the data page sends: the data form plus the case formset."""
    result = {
        'problem-data-output_limit': '',
        'problem-data-output_prefix': '',
        'problem-data-checker': '',
        'problem-data-checker_args': '',
        'problem-data-unicode': 'unknown',
        'problem-data-nobigmath': 'unknown',
        'cases-TOTAL_FORMS': str(len(rows)),
        'cases-INITIAL_FORMS': str(initial),
        'cases-MIN_NUM_FORMS': '0',
        'cases-MAX_NUM_FORMS': '1000',
    }
    for key, value in data.items():
        result['problem-data-' + key] = value
    for index, row in enumerate(rows):
        for field in CASE_FIELDS:
            value = row.get(field, '')
            if value is None or value is False:
                continue
            result['cases-%d-%s' % (index, field)] = str(value)
    return result


def case(order, name, points=None, **extra):
    row = {'order': order, 'type': 'C', 'input_file': name + '.in', 'output_file': name + '.out'}
    if points is not None:
        row['points'] = points
    row.update(extra)
    return row


def example(name):
    with open(os.path.join(INTERACTIVE_EXAMPLE_DIR, name), 'rb') as f:
        return f.read()


def example_cases():
    folder = os.path.join(INTERACTIVE_EXAMPLE_DIR, 'casos')
    cases = {}
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), 'rb') as f:
            cases[name] = f.read()
    return cases


CASE_ROWS = [case(i, str(i), 25) for i in range(1, 5)]


class InteractorTests(TestCase):
    fixtures = ['language_small.json']

    def setUp(self):
        create_problem_group(name='group')
        create_problem_type(name='type')
        create_user(username='author')
        self.client.force_login(create_user(username='admin', is_superuser=True, is_staff=True))

    def make(self, code):
        problem = create_problem(code=code, authors=['author'], types=['type'], allowed_languages=['PY3'],
                                 is_public=True)
        ProblemData.objects.create(problem=problem)
        return problem, reverse('problem_data', args=[code])

    def post(self, url, data, expect=302):
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, expect, response.content.decode()[:3000])
        return response

    def upload(self, url, name, source, rows=CASE_ROWS, **extra):
        data = payload(rows, zipfile=SimpleUploadedFile('casos.zip', build_zip(example_cases())),
                       interactor=SimpleUploadedFile(name, source), **extra)
        return self.post(url, data)

    def saved_rows(self, problem):
        return [dict(row, id=row_id) for row, row_id in
                zip(CASE_ROWS, problem.cases.order_by('order').values_list('id', flat=True))]

    def init(self, problem):
        with problem_data_storage.open('%s/init.yml' % problem.code, 'rb') as f:
            return yaml.safe_load(f)

    def directory(self, problem):
        return sorted(os.listdir(problem_data_storage.path(problem.code)))

    # --- what the page writes ----------------------------------------------------

    def test_simple_interactor(self):
        problem, url = self.make('intsimple')
        self.upload(url, 'interactor.cpp', example('interactor.cpp'))
        init = self.init(problem)
        self.assertEqual(init['interactive'], {'files': ['interactor.cpp'], 'feedback': False})
        self.assertEqual(init['archive'], 'casos.zip')
        self.assertEqual(len(init['test_cases']), 4)
        self.assertEqual(self.directory(problem), ['casos.zip', 'init.yml', 'interactor.cpp'])
        with problem_data_storage.open('intsimple/interactor.cpp', 'rb') as f:
            self.assertEqual(f.read(), example('interactor.cpp'))

    def test_testlib_is_detected_and_supplied(self):
        problem, url = self.make('inttestlib')
        self.upload(url, 'interactor_testlib.cpp', example('interactor_testlib.cpp'))
        self.assertEqual(self.init(problem)['interactive'],
                         {'files': ['interactor_testlib.cpp', 'testlib.h'], 'feedback': False, 'type': 'testlib'})
        with problem_data_storage.open('inttestlib/testlib.h', 'rb') as f, open(TESTLIB_PATH, 'rb') as bundled:
            self.assertEqual(f.read(), bundled.read())

    def test_testlib_is_detected_with_windows_bom_and_line_endings(self):
        problem, url = self.make('intbom')
        body = example('interactor_testlib.cpp').replace(b'#include "testlib.h"\n', b'')
        # The only include, on the first line, right after the byte order mark.
        source = b'\xef\xbb\xbf#include "testlib.h"\r\n' + body.replace(b'\n', b'\r\n')
        self.assertIsNone(re.search(br'^[ \t]*#[ \t]*include[ \t]*[<"]testlib\.h[>"]', source, re.M))
        self.upload(url, 'interactor.cpp', source)
        self.assertEqual(self.init(problem)['interactive']['type'], 'testlib')

    def test_testlib_mentioned_in_a_comment_is_not_testlib(self):
        problem, url = self.make('intcomment')
        self.upload(url, 'interactor.cpp', b'// do not #include "testlib.h" here\nint main() { return 0; }\n')
        self.assertNotIn('type', self.init(problem)['interactive'])

    def test_python_interactor_is_pinned_to_py3(self):
        problem, url = self.make('intpython')
        self.upload(url, 'interactor.py', example('interactor.py'))
        self.assertEqual(self.init(problem)['interactive'],
                         {'files': ['interactor.py'], 'feedback': False, 'lang': 'PY3'})

    def test_feedback_can_be_enabled(self):
        problem, url = self.make('intfeedback')
        self.upload(url, 'interactor.cpp', example('interactor.cpp'), interactor_feedback='on')
        self.assertIs(self.init(problem)['interactive']['feedback'], True)

    # --- later saves, replacing and clearing -------------------------------------

    def test_saving_again_keeps_the_interactor(self):
        problem, url = self.make('intresave')
        self.upload(url, 'interactor.cpp', example('interactor.cpp'))
        before = self.init(problem)
        rows = self.saved_rows(problem)
        self.post(url, payload(rows, initial=len(rows), checker='standard'))
        after = self.init(problem)
        self.assertEqual(after['interactive'], before['interactive'])
        self.assertEqual(after['checker'], 'standard')

    def test_clearing_turns_it_back_into_a_normal_problem(self):
        problem, url = self.make('intclear')
        self.upload(url, 'interactor_testlib.cpp', example('interactor_testlib.cpp'))
        rows = self.saved_rows(problem)
        self.post(url, payload(rows, initial=len(rows), **{'interactor-clear': 'on'}))
        self.assertNotIn('interactive', self.init(problem))
        self.assertFalse(ProblemData.objects.get(problem=problem).interactor)
        self.assertNotIn('interactor_testlib.cpp', self.directory(problem))

    def test_replacing_with_the_same_name_keeps_the_new_file(self):
        problem, url = self.make('intsame')
        self.upload(url, 'interactor.cpp', b'// first version\nint main() { return 1; }\n')
        self.upload(url, 'interactor.cpp', example('interactor.cpp'))
        self.assertEqual(ProblemData.objects.get(problem=problem).interactor.name, 'intsame/interactor.cpp')
        with problem_data_storage.open('intsame/interactor.cpp', 'rb') as f:
            self.assertEqual(f.read(), example('interactor.cpp'))

    def test_replacing_with_another_name_removes_the_old_file(self):
        problem, url = self.make('intswap')
        self.upload(url, 'old.cpp', example('interactor.cpp'))
        self.upload(url, 'interactor.py', example('interactor.py'))
        self.assertEqual(self.init(problem)['interactive']['files'], ['interactor.py'])
        self.assertNotIn('old.cpp', self.directory(problem))

    def test_a_new_zip_replaces_the_old_one(self):
        # Upstream deleted the old archive with FieldFile.delete, which also blanks the
        # field on the instance being saved: a zip uploaded over another was lost.
        for code, name in (('intzipother', 'other.zip'), ('intzipsame', 'casos.zip')):
            problem, url = self.make(code)
            self.upload(url, 'interactor.cpp', example('interactor.cpp'))
            files = dict(example_cases(), **{'5.in': b'7 7\n', '5.out': b'7\n'})
            rows = self.saved_rows(problem)
            self.post(url, payload(rows + [case(5, '5', 0)], initial=len(rows),
                                   zipfile=SimpleUploadedFile(name, build_zip(files))))
            data = ProblemData.objects.get(problem=problem)
            self.assertEqual(data.zipfile.name, '%s/%s' % (code, name))
            init = self.init(problem)
            self.assertEqual(init['archive'], name)
            self.assertEqual(len(init['test_cases']), 5)
            with zipfile.ZipFile(problem_data_storage.path(data.zipfile.name)) as archive:
                self.assertIn('5.in', archive.namelist())
            self.assertEqual(self.directory(problem), sorted({name, 'init.yml', 'interactor.cpp'}))

    def test_clearing_the_zip_removes_it(self):
        problem, url = self.make('intzipclear')
        self.upload(url, 'interactor.cpp', example('interactor.cpp'))
        self.post(url, payload([], **{'zipfile-clear': 'on'}))
        self.assertFalse(ProblemData.objects.get(problem=problem).zipfile)
        self.assertNotIn('casos.zip', self.directory(problem))

    def test_renaming_the_problem_moves_the_interactor(self):
        problem, url = self.make('intrename')
        self.upload(url, 'interactor.cpp', example('interactor.cpp'))
        # Read again, as the admin does: the object from make() caches the data row
        # from before the upload and would write it back.
        problem = Problem.objects.get(pk=problem.pk)
        problem.code = 'intrenamed'
        problem.save()
        data = ProblemData.objects.get(problem=problem)
        self.assertEqual(data.interactor.name, 'intrenamed/interactor.cpp')
        self.assertTrue(problem_data_storage.exists('intrenamed/interactor.cpp'))
        self.assertTrue(problem_data_storage.exists('intrenamed/casos.zip'))

    def test_code_without_the_new_columns_can_still_insert(self):
        # Rolling back the code but not the migration leaves both columns in place.
        problem = create_problem(code='intoldcode', authors=['author'], types=['type'], allowed_languages=['PY3'])
        old_columns = [field.column for field in ProblemData._meta.concrete_fields
                       if field.name not in ('id', 'interactor', 'interactor_feedback')]
        values = {'problem_id': problem.id, 'zipfile': '', 'generator': '', 'feedback': '', 'checker': '',
                  'checker_args': ''}
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO judge_problemdata (%s) VALUES (%s)' % (
                ', '.join(old_columns), ', '.join(['%s'] * len(old_columns))),
                [values.get(column) for column in old_columns])
        data = ProblemData.objects.get(problem=problem)
        self.assertFalse(data.interactor)
        self.assertIs(data.interactor_feedback, False)

    # --- validation ----------------------------------------------------------------

    def rejected(self, code, name, source, **extra):
        problem, url = self.make(code)
        response = self.post(url, payload(CASE_ROWS, zipfile=SimpleUploadedFile('casos.zip',
                                                                                  build_zip(example_cases())),
                                          interactor=SimpleUploadedFile(name, source), **extra), expect=200)
        self.assertFalse(problem_data_storage.exists('%s/init.yml' % code))
        self.assertFalse(ProblemData.objects.get(problem=problem).interactor)
        return response

    def test_wrong_extension_is_rejected(self):
        response = self.rejected('intext', 'interactor.txt', b'x')
        self.assertContains(response, 'The interactor must be a source file ending in .c, .cc, .cpp, .py.')

    def test_file_without_extension_is_rejected(self):
        self.rejected('intbin', 'interactor', b'\x7fELF')

    def test_oversized_interactor_is_rejected(self):
        response = self.rejected('intbig', 'interactor.cpp', b'/' * ((1 << 20) + 1))
        self.assertContains(response, 'The interactor must not be larger than 1 MB.')

    def test_same_name_as_the_generator_is_rejected(self):
        response = self.rejected('intgen', 'gen.py', example('interactor.py'),
                                 generator=SimpleUploadedFile('gen.py', b'print(1)\n'))
        self.assertContains(response, 'The generator and the interactor need different file names.')

    # --- pages -----------------------------------------------------------------------

    def test_data_page_shows_the_mode_and_the_guide_link(self):
        problem, url = self.make('intpage')
        response = self.client.get(url)
        self.assertContains(response, reverse('problem_interactive_guide', args=['intpage']))
        self.assertNotContains(response, 'This problem is interactive')
        self.upload(url, 'interactor_testlib.cpp', example('interactor_testlib.cpp'))
        self.assertContains(self.client.get(url), 'with a testlib interactor')
        os.remove(problem_data_storage.path('intpage/interactor_testlib.cpp'))
        self.assertContains(self.client.get(url), 'The interactor file is missing')

    def test_data_page_in_spanish(self):
        problem, url = self.make('intes')
        self.upload(url, 'interactor.cpp', example('interactor.cpp'))
        response = self.client.get(url, HTTP_ACCEPT_LANGUAGE='es')
        for text in ('Cómo hacer un problema interactivo', 'Este problema es interactivo. El verificador no se usa.',
                     'Mostrar mensajes del interactor', 'Convierte el problema en interactivo'):
            self.assertContains(response, text)
        translation.activate('en')

    def test_guide_and_example_download(self):
        self.make('intguide')
        response = self.client.get(reverse('problem_interactive_guide', args=['intguide']))
        self.assertContains(response, 'registerInteraction')
        self.assertContains(response, 'Otro veredicto en vez de Respuesta Incorrecta')

        response = self.client.get(reverse('problem_interactive_example', args=['intguide']))
        self.assertEqual(response['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(sorted(archive.namelist()), sorted('ejemplo-interactivo/' + name for name in (
                'casos.zip', 'interactor.cpp', 'interactor.py', 'interactor_testlib.cpp', 'solucion.cpp',
                'solucion.py', 'enunciado.md')))
            with zipfile.ZipFile(io.BytesIO(archive.read('ejemplo-interactivo/casos.zip'))) as cases:
                self.assertEqual({name: cases.read(name) for name in cases.namelist()}, example_cases())

    def test_guide_is_for_problem_editors_only(self):
        problem, url = self.make('intprivate')
        guide = reverse('problem_interactive_guide', args=['intprivate'])
        bundle = reverse('problem_interactive_example', args=['intprivate'])
        self.client.logout()
        for target in (guide, bundle):
            self.assertEqual(self.client.get(target).status_code, 302)
        create_user(username='someone')
        self.client.force_login(User.objects.get(username='someone'))
        for target in (guide, bundle, url):
            self.assertEqual(self.client.get(target).status_code, 404)

    def test_manually_managed_problem_has_no_guide(self):
        problem, url = self.make('intmanual')
        problem.is_manually_managed = True
        problem.save()
        self.assertEqual(self.client.get(reverse('problem_interactive_guide', args=['intmanual'])).status_code, 404)

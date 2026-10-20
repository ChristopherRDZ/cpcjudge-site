"""Sample tables and $...$ in problem statements.

Statements, their clarifications and editorials accept OmegaUp's table (||input / ||output / ||description /
||end) and $...$ for inline math. Everything else keeps the original syntax, and ~...~ keeps working.
"""
import re

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import translation
from lxml import html

from judge.jinja2.markdown import SAMPLE_TABLE_STYLE, markdown
from judge.jinja2.reference import reference
from judge.models.tests.util import create_problem, create_solution, create_user

USER_EXAMPLE = '''# Ejemplo

||input
6
banana
||output
6
||description
Es el ejemplo de la descripción.
||input
26
abcdefghijklmnopqrstuvwxyz
||output
26
||description
||end
'''

# Statements written the way they are written today: none of this may change.
LEGACY = [
    'Dados ~a~ y ~b~, imprime ~a+b~.\n\n## Entrada\n\nDos enteros.\n\n## Ejemplo\n\n```\n3 4\n```\n\n```\n7\n```\n',
    '$$\\sum_{i=1}^{n} i$$ en bloque, y \\(x\\) en línea.',
    '| a | b |\n|---|---|\n| 1 | 2 |\n',
    'Cuesta $5 y $10.',
    'Pagas 20$ o 30 $.',
    'Un `$` en código y otro `a$b$c`.',
    '```\n$ ./a.out\n$x$\n||input\n||end\n```\n',
    '<div class="x">||input</div>\n\nTexto con || barras.',
    '* uno\n* dos ||input\n',
    'Línea con *énfasis* y **negrita** y [enlace](https://example.com).',
    '||input\n6\nsin cierre, se queda como texto\n',
    '> cita\n\n---\n\n1. primero\n2. segundo\n',
]


def render(text, style='problem', lang='es', engine='jax'):
    with translation.override(lang):
        return str(markdown(text, style, engine))


def tree(text, **kwargs):
    result = render(text, **kwargs)
    return html.fromstring('<div>%s</div>' % result)


class SampleTableTests(SimpleTestCase):
    def test_user_example(self):
        result = render(USER_EXAMPLE)
        doc = html.fromstring('<div>%s</div>' % result)
        self.assertEqual([th.text_content() for th in doc.xpath('//table[@class="sample-table"]/thead//th')],
                         ['Entrada', 'Salida', 'Descripción'])
        rows = doc.xpath('//table[@class="sample-table"]/tbody/tr')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].xpath('td[@class="sample-input"]/pre/code')[0].text, '6\nbanana')
        self.assertEqual(rows[0].xpath('td[@class="sample-output"]/pre')[0].text, '6')
        self.assertEqual(rows[0].xpath('td[@class="sample-description"]')[0].text_content().strip(),
                         'Es el ejemplo de la descripción.')
        self.assertEqual(rows[1].xpath('td[@class="sample-input"]/pre/code')[0].text,
                         '26\nabcdefghijklmnopqrstuvwxyz')
        self.assertEqual(rows[1].xpath('td[@class="sample-output"]/pre')[0].text, '26')
        self.assertEqual(rows[1].xpath('td[@class="sample-description"]')[0].text_content(), '')
        # The heading the author wrote stays (as h3, like every # in a statement).
        self.assertEqual(doc.xpath('//h3')[0].text, 'Ejemplo')

    def test_headers_follow_the_language(self):
        doc = tree(USER_EXAMPLE, lang='en')
        self.assertEqual([th.text_content() for th in doc.xpath('//thead//th')], ['Input', 'Output', 'Description'])

    def test_no_description_column_when_all_are_empty(self):
        doc = tree('||input\n1 2\n||output\n3\n||input\n4 5\n||output\n9\n||end\n')
        self.assertEqual([th.get('class') for th in doc.xpath('//thead//th')], ['sample-input', 'sample-output'])
        self.assertEqual(len(doc.xpath('//tbody/tr')), 2)
        self.assertFalse(doc.xpath('//td[@class="sample-description"]'))

    def test_input_and_output_are_verbatim(self):
        text = '||input\n<b>&amp; **no** ~x~ $y$\n  dos espacios\n\nlínea tras vacía\n||output\n</pre>\n||end\n'
        doc = tree(text)
        self.assertEqual(doc.xpath('//td[@class="sample-input"]/pre/code')[0].text,
                         '<b>&amp; **no** ~x~ $y$\n  dos espacios\n\nlínea tras vacía')
        self.assertEqual(doc.xpath('//td[@class="sample-output"]/pre')[0].text, '</pre>')
        self.assertFalse(doc.xpath('//td//b'))

    def test_description_is_markdown(self):
        doc = tree('||input\n1\n||output\n1\n||description\n**Sí**: con ~n = 1~ y $m$.\n\n* uno\n* dos\n||end\n')
        cell = doc.xpath('//td[@class="sample-description"]')[0]
        self.assertEqual(cell.xpath('.//strong')[0].text, 'Sí')
        self.assertIn('\\(n = 1\\)', cell.text_content())
        self.assertIn('\\(m\\)', cell.text_content())
        self.assertEqual(len(cell.xpath('.//li')), 2)

    def test_markers_are_lenient(self):
        doc = tree('Texto pegado.\n||Input  \n1\n  ||OUTPUT\n2\n||end   \nDespués.')
        self.assertEqual(doc.xpath('//p')[0].text, 'Texto pegado.')
        self.assertEqual(doc.xpath('//td[@class="sample-input"]/pre/code')[0].text, '1')
        self.assertEqual(doc.xpath('//td[@class="sample-output"]/pre')[0].text, '2')
        self.assertEqual(doc.xpath('//p')[-1].text, 'Después.')

    def test_empty_parts(self):
        doc = tree('||input\n||output\n||end\n')
        self.assertEqual(doc.xpath('//td[@class="sample-input"]/pre/code')[0].text, None)
        self.assertEqual(doc.xpath('//td[@class="sample-output"]/pre')[0].text, None)

    def test_two_tables_one_style(self):
        result = render('||input\n1\n||output\n1\n||end\n\nEntre medias.\n\n||input\n2\n||output\n2\n||end\n')
        self.assertEqual(result.count('<table class="sample-table">'), 2)
        self.assertEqual(result.count('<style>'), 1)
        self.assertTrue(result.endswith(SAMPLE_TABLE_STYLE))

    def test_short_samples_make_an_even_pair(self):
        # The longest line of the user's example has 26 characters: input and output get that minimum width.
        doc = tree(USER_EXAMPLE)
        styles = [pre.get('style') for pre in doc.xpath('//td[@class="sample-input" or @class="sample-output"]/pre')]
        self.assertEqual(styles, [None] * 4)
        # Never narrower than the headers.
        doc = tree('||input\n1\n||output\n2\n||end\n')
        self.assertEqual({pre.get('style') for pre in doc.xpath('//pre')}, {None})

    def test_a_long_sample_keeps_its_own_width(self):
        doc = tree('||input\n%s\n||output\n1\n||end\n' % ('9' * 31))
        self.assertEqual([pre.get('style') for pre in doc.xpath('//pre')], [None, None])
        doc = tree('||input\n%s\n||output\n1\n||end\n' % ('9' * 30))
        self.assertEqual([pre.get('style') for pre in doc.xpath('//pre')], [None, None])

    def test_no_style_without_a_table(self):
        self.assertNotIn('<style>', render('Sin tabla, con $x$.'))

    def test_every_statement_style_and_math_engine(self):
        for style in ('problem', 'problem-full', 'solution'):
            for engine in ('jax', 'svg', 'mml', 'tex', None):
                with self.subTest(style=style, engine=engine):
                    doc = tree(USER_EXAMPLE, style=style, engine=engine)
                    self.assertEqual(len(doc.xpath('//table[@class="sample-table"]/tbody/tr')), 2)

    def test_bleached_style_keeps_the_table(self):
        # 'problem' goes through bleach: the same markup as without it.
        self.assertEqual(render(USER_EXAMPLE, style='problem'), render(USER_EXAMPLE, style='problem-full'))
        doc = tree(USER_EXAMPLE, style='problem')
        self.assertEqual(doc.xpath('//div[@class="sample-table-wrap"]/table')[0].get('class'), 'sample-table')
        self.assertEqual(len(doc.xpath('//td[@class="sample-description"]/div[@class="sample-text"]')), 2)

    def test_reference_keeps_the_style_in_the_body(self):
        # lxml would move a leading <style> into a <head> and reference() would return a whole document.
        with translation.override('es'):
            result = str(reference(markdown('||input\n1\n||output\n1\n||end\n', 'problem', 'jax')))
        self.assertNotIn('<html', result)
        self.assertNotIn('<head', result)
        self.assertIn('<style>', result)
        self.assertIn('<table class="sample-table">', result)

    def test_other_styles_do_not_change(self):
        for style in ('default', 'comment', 'blog', 'contest', 'self-description', 'ticket', 'flatpage'):
            with self.subTest(style=style):
                result = render(USER_EXAMPLE + '\nY $x$.', style=style)
                self.assertNotIn('sample-table', result)
                self.assertIn('||input', result)
                self.assertIn('$x$', result)


class DollarMathTests(SimpleTestCase):
    def assertMath(self, text, formulas, style='problem'):
        result = render(text, style=style)
        self.assertEqual(re.findall(r'\\\((.*?)\\\)', result), formulas, result)
        return result

    def test_inline(self):
        self.assertMath('Sea $n$ el tamaño y $1 \\le a_i \\le 10^9$.', ['n', '1 \\le a_i \\le 10^9'])

    def test_with_tilde_and_display(self):
        result = self.assertMath('$a$, ~b~ y $$c$$.', ['a', 'b'])
        self.assertIn('\\[c\\]', result)

    def test_prices_stay_text(self):
        result = self.assertMath('Cuesta $5 y $10, o $7.50.', [])
        self.assertIn('Cuesta $5 y $10, o $7.50.', result)
        result = self.assertMath('Pagas 20$ o 30 $.', [])
        self.assertIn('Pagas 20$ o 30 $.', result)

    def test_known_limit_of_the_rule(self):
        # Pandoc reads this as a formula too; the statement has to write \$ for the price.
        self.assertMath('De $10 a 20$ pesos.', ['10 a 20'])
        self.assertMath('De \\$10 a 20\\$ pesos.', [])

    def test_spaces_inside_the_delimiters(self):
        self.assertMath('$ x$ y $x $ no son fórmulas', [])

    def test_closing_dollar_followed_by_a_digit(self):
        self.assertMath('$x$5', [])

    def test_escaped_dollar(self):
        result = self.assertMath('Pagas \\$3 y \\$x\\$, pero $y$ sí.', ['y'])
        self.assertIn('Pagas $3 y $x$, pero', result)

    def test_escaped_dollar_inside_a_formula(self):
        self.assertMath('$a\\$b$', ['a\\$b'])

    def test_code_is_left_alone(self):
        result = self.assertMath('El prompt `$ ./a.out` y `$x$`.', [])
        self.assertIn('<code>$ ./a.out</code>', result)
        self.assertIn('<code>$x$</code>', result)

    def test_html_is_escaped(self):
        result = self.assertMath('$a<b$', ['a&lt;b'])
        self.assertNotIn('<b', result.replace('<br', ''))

    def test_in_links_and_emphasis(self):
        self.assertMath('[el valor $k$](https://example.com) y *con $m$*', ['k', 'm'])

    def test_clarification_and_editorial_styles(self):
        for style in ('problem', 'problem-full', 'solution'):
            with self.subTest(style=style):
                self.assertMath('Sea $n$.', ['n'], style=style)


class LegacyStatementTests(SimpleTestCase):
    def test_statements_without_the_new_syntax_render_as_before(self):
        # 'contest' uses the same settings as 'problem' (bleach included) but not the new syntax, so it renders
        # exactly what the old code rendered.
        for text in LEGACY:
            with self.subTest(text=text):
                self.assertEqual(render(text, 'problem'), render(text, 'contest'))

    def test_the_full_markup_style_too(self):
        for text in LEGACY:
            with self.subTest(text=text):
                self.assertEqual(render(text, 'problem-full'), render(text, 'flatpage'))


class ProblemPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.problem = create_problem(code='ejemplos', name='Ejemplos', is_public=True,
                                     description='Sea $n$ la longitud.\n\n' + USER_EXAMPLE)
        cls.user = create_user(username='lectora')

    def setUp(self):
        cache.clear()

    def page(self, lang):
        response = self.client.get('/problem/ejemplos', HTTP_ACCEPT_LANGUAGE=lang)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_page_shows_the_table_in_each_language(self):
        spanish = self.page('es')
        self.assertIn('<th class="sample-input">Entrada</th>', spanish)
        self.assertIn('\\(n\\)', spanish)
        self.assertEqual(spanish.count('.sample-table-wrap{'), 1)
        # The cached fragment is per language: English must not get the Spanish headers.
        english = self.page('en')
        self.assertIn('>Input</th>', english)
        self.assertNotIn('>Entrada</th>', english)

    def test_saving_the_problem_refreshes_the_cached_statement(self):
        self.assertIn('sample-table', self.page('es'))
        self.problem.description = 'Ahora sin tabla.'
        self.problem.save()
        page = self.page('es')
        self.assertNotIn('sample-table', page)
        self.assertIn('Ahora sin tabla.', page)

    def test_editorial_page(self):
        create_solution(problem=self.problem, content='Con $k$ pasos.\n\n' + USER_EXAMPLE)
        response = self.client.get('/problem/ejemplos/editorial', HTTP_ACCEPT_LANGUAGE='es')
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn('<table class="sample-table">', page)
        self.assertIn('\\(k\\)', page)

    def test_preview_shows_the_table(self):
        self.client.force_login(self.user)
        response = self.client.post('/widgets/preview/problem', {'content': USER_EXAMPLE})
        self.assertEqual(response.status_code, 200)
        self.assertIn('<table class="sample-table">', response.content.decode())


class ArenaStatementTests(TestCase):
    fixtures = ['language_small.json']

    def setUp(self):
        cache.clear()

    def test_the_player_gets_the_table(self):
        # The arena inserts the same cached statement; the table and its style travel with it.
        from django.urls import reverse
        from arena_testutil import duel, online_judge, organizer, player, problem, skip_countdown, start
        task = problem('arenaejemplos')
        task.description = 'Sea $n$ la longitud.\n\n' + USER_EXAMPLE
        task.save()
        online_judge(task)
        boss, alice, bob = organizer(), player('alicia'), player('bruno')
        game = skip_countdown(start(boss, duel(boss, alice, bob, [task])), seconds_ago=30)
        self.client.force_login(alice)
        response = self.client.get(reverse('arena_statement', args=[game.pk, 0]), HTTP_ACCEPT_LANGUAGE='es')
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn('<table class="sample-table">', page)
        self.assertEqual(page.count('.sample-table-wrap{'), 1)
        self.assertIn('\\(n\\)', page)


# ---------------------------------------------------------------------------------------------------------------
# Each column its own width, rows a little taller
# ---------------------------------------------------------------------------------------------------------------

class SampleTableLookTests(SimpleTestCase):
    def render(self, text):
        with translation.override('es'):
            return str(markdown(text, 'problem', 'jax'))

    def test_a_short_output_is_not_as_wide_as_a_long_input(self):
        result = self.render('||input\n4 5 sjidowkdkwjf e djwue\n||output\n9\n||description\nSuma.\n'
                             '||input\n5 10\n||output\n15\n||end\n')
        self.assertNotIn('min-width: ', result.split('<style>')[0])
        self.assertIn('<td class="sample-output"><pre>9</pre></td>', result)
        self.assertIn('<td class="sample-input"><pre><code>4 5 sjidowkdkwjf e djwue</code></pre></td>', result)
        # Nor does it keep the room of the copy button, which only the input has.
        self.assertNotIn('td.sample-output pre{\npadding-right', SAMPLE_TABLE_STYLE)
        self.assertIn('.copy-clipboard + pre{padding-right:2.6em}', SAMPLE_TABLE_STYLE)

    def test_rows_leave_room_above_the_text_for_the_copy_icon(self):
        self.assertIn('pre{margin:0;padding:1.2em .8em .7em;', SAMPLE_TABLE_STYLE)
        self.assertIn('td.sample-description{padding:1.2em .8em .7em !important}', SAMPLE_TABLE_STYLE)
        self.assertIn('.btn-clipboard{border-width:0 0 1px 1px;border-radius:0 0 0 3px;font-size:0;\n'
                      'padding:3px 6px;line-height:1}', SAMPLE_TABLE_STYLE)

    def test_the_bleached_style_keeps_the_same_table(self):
        # problem-full skips bleach and problem goes through it: both give the same table.
        text = '||input\n1 2\n||output\n3\n||end\n'
        with translation.override('es'):
            self.assertEqual(str(markdown(text, 'problem', 'jax')), str(markdown(text, 'problem-full', 'jax')))

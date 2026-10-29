import json
import logging
import re
from html import unescape
from urllib.parse import urlparse

import mistune
from bleach.css_sanitizer import CSSSanitizer
from bleach.sanitizer import Cleaner
from django.conf import settings
from django.utils.translation import gettext
from lxml import html
from lxml.etree import ParserError, XMLSyntaxError
from markupsafe import Markup

from judge.highlight_code import highlight_code
from judge.jinja2.markdown.lazy_load import lazy_load as lazy_load_processor
from judge.jinja2.markdown.math import DollarMathInlineGrammar, DollarMathInlineLexer, MathInlineGrammar, \
    MathInlineLexer, MathRenderer
from judge.utils.camo import client as camo_client
from judge.utils.texoid import TEXOID_ENABLED, TexoidRenderer
from .bleach_whitelist import all_styles, mathml_attrs, mathml_tags
from .. import registry

logger = logging.getLogger('judge.html')

NOFOLLOW_WHITELIST = settings.NOFOLLOW_EXCLUDED

# Styles of problem statements, their clarifications and editorials. These also accept the OmegaUp sample table
# (||input ... ||end) and $...$ for inline math; everything else (comments, blog posts, profiles...) keeps the
# original syntax. ~...~ and the fenced code blocks keep working everywhere.
STATEMENT_STYLES = frozenset({'problem', 'problem-full', 'solution'})


class CodeSafeInlineGrammar(mistune.InlineGrammar):
    double_emphasis = re.compile(r'^\*{2}([\s\S]+?)()\*{2}(?!\*)')  # **word**
    emphasis = re.compile(r'^\*((?:\*\*|[^\*])+?)()\*(?!\*)')  # *word*


class AwesomeInlineGrammar(MathInlineGrammar, CodeSafeInlineGrammar):
    pass


class AwesomeInlineLexer(MathInlineLexer, mistune.InlineLexer):
    grammar_class = AwesomeInlineGrammar


class StatementInlineGrammar(DollarMathInlineGrammar, CodeSafeInlineGrammar):
    pass


class StatementInlineLexer(DollarMathInlineLexer, mistune.InlineLexer):
    grammar_class = StatementInlineGrammar


# OmegaUp's sample table:
#
#   ||input
#   6
#   banana
#   ||output
#   6
#   ||description
#   Markdown, optional.
#   ||input
#   ...
#   ||end
#
# Each ||input starts a row. Input and output are shown verbatim; the description is Markdown. Without a closing
# ||end nothing changes: the lines stay ordinary text, as before.
SAMPLE_MARKER = re.compile(r'^ {0,3}\|\|(input|output|description)[ \t]*$', re.I)


class SampleTableBlockGrammar(mistune.BlockGrammar):
    sample_table = re.compile(r'^ {0,3}\|\|input[ \t]*(?:\n([\s\S]*?))?\n {0,3}\|\|end[ \t]*(?:\n+|$)', re.I)
    # A paragraph stops before a table, so that it does not need a blank line in front.
    paragraph = re.compile(mistune.BlockGrammar.paragraph.pattern.replace(
        '(?!', r'(?!(?i: {0,3}\|\|input[ \t]*(?:\n|$))|', 1))


class SampleTableRule:
    """The sample_table pattern, tried at most once per text without an ||end below it (markdown-enunciados-1,
    2026-10-28).

    The paragraph stops before every ||input line, so the lexer tries the table at each of them, and each try
    searched the rest of the text for an ||end: with thousands of ||input lines and no ||end, a preview kept a worker
    busy for minutes (quadratic). The lexer always tries a rule on what is left of the text it is parsing, so once a
    try finds no ||end, a shorter tail of that same text has none either. `tails` keeps that length for each text
    being parsed: the descriptions inside a table, block quotes and list items are parsed as texts of their own.
    """
    start = re.compile(r' {0,3}\|\|input[ \t]*(?:\n|$)', re.I)

    def __init__(self, pattern):
        self.pattern = pattern
        self.tails = []

    def match(self, text):
        if not self.start.match(text) or (self.tails and len(text) <= self.tails[-1]):
            return None
        found = self.pattern.match(text)
        if found is None and self.tails:
            self.tails[-1] = len(text)
        return found


class SampleTableBlockLexer(mistune.BlockLexer):
    grammar_class = SampleTableBlockGrammar
    default_rules = ['sample_table'] + mistune.BlockLexer.default_rules

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not isinstance(self.rules.sample_table, SampleTableRule):
            self.rules.sample_table = SampleTableRule(self.rules.sample_table)

    def parse(self, text, rules=None):
        self.rules.sample_table.tails.append(-1)
        try:
            return super().parse(text, rules)
        finally:
            self.rules.sample_table.tails.pop()

    def parse_sample_table(self, m):
        rows = []
        section = None
        for line in (m.group(1) or '').split('\n'):
            marker = SAMPLE_MARKER.match(line)
            kind = marker.group(1).lower() if marker else None
            if kind == 'input' or not rows:
                rows.append({'input': [], 'output': [], 'description': []})
                section = 'input'
                if kind == 'input':
                    continue
            if kind:
                section = kind
            else:
                rows[-1][section].append(line)
        rows = [{key: '\n'.join(lines).strip('\n') for key, lines in row.items()} for row in rows]

        descriptions = any(row['description'].strip() for row in rows)
        self.tokens.append({'type': 'sample_table_start', 'descriptions': descriptions})
        for row in rows:
            self.tokens.append({'type': 'sample_row_start', 'input': row['input'], 'output': row['output']})
            if descriptions and row['description'].strip():
                self.parse(row['description'])
            self.tokens.append({'type': 'sample_row_end'})
        self.tokens.append({'type': 'sample_table_end'})


class StatementMarkdown(mistune.Markdown):
    def output_sample_table(self):
        descriptions = self.token['descriptions']
        rows = []
        while self.pop()['type'] != 'sample_table_end':
            row = self.token
            description = self.renderer.placeholder()
            while self.pop()['type'] != 'sample_row_end':
                description += self.tok()
            rows.append((row['input'], row['output'], description if descriptions else None))
        return self.renderer.sample_table(rows, descriptions)


# The sample table brings its own style, so that it looks the same wherever a statement is shown (problem page,
# arena, projector, admin preview, PDF) without touching the compressed site stylesheets. markdown() appends it
# once, after everything else: a <style> at the start of the fragment would be moved into a <head> by lxml.
# The dark palette repeats the conditions under which base.html loads the dark stylesheet.
# The table is as wide as its samples, like OmegaUp's, and each column as wide as its own samples (2026-10-19: an
# output no longer takes the width of a longer input). A sample line too long for the page scrolls inside its own
# cell, and the whole table scrolls in its box only if even that does not fit. Descriptions wrap past 26em. The copy
# button of the problem page becomes an icon in the corner of the input, with room kept so that it never covers
# text; the rows leave a little room above the text, so that the icon sits higher than the first line. When
# printing (the PDF) long lines wrap instead, since nothing can scroll on paper: an input wraps at its spaces first,
# and an output never splits a word, so that its column is at least as wide as its longest number.
# Every rule starts with ".sample-table-wrap .sample-table" so that it wins over the generic table and pre rules of
# the pages around it, such as "form .martor-preview pre" in the admin preview. The admin theme (wpadmin) also
# styles every cell with "#content table th/td", which no class can beat: the cell properties it touches are
# !important here. The colours still come from the variables, so the dark palette keeps working.
SAMPLE_TABLE_STYLE = '''<style>
.sample-table-wrap{margin:1em 0 1.5em;max-width:100%;overflow-x:auto}
.sample-table-wrap .sample-table{--sample-line:#ccc;--sample-head-bg:#eee;--sample-head-fg:#111;
--sample-code-bg:#f8f8f8;--sample-code-fg:#000;width:auto;border-collapse:separate;border-spacing:0;margin:0;
background:transparent}
.sample-table-wrap .sample-table tr{background:transparent}
.sample-table-wrap .sample-table th,.sample-table-wrap .sample-table td{border:solid var(--sample-line) !important;
border-width:0 1px 1px 0 !important;box-shadow:none;font-family:inherit;font-size:1em !important;
line-height:1.5 !important}
.sample-table-wrap .sample-table th:first-child,.sample-table-wrap .sample-table td:first-child{
border-left-width:1px !important}
.sample-table-wrap .sample-table th{border-top-width:1px !important;background:var(--sample-head-bg) !important;
color:var(--sample-head-fg) !important;font-weight:600 !important;letter-spacing:normal;
text-transform:none !important;padding:.4em 1em !important;text-align:center;vertical-align:middle;
white-space:nowrap}
.sample-table-wrap .sample-table th:first-child{border-top-left-radius:4px}
.sample-table-wrap .sample-table th:last-child{border-top-right-radius:4px}
.sample-table-wrap .sample-table td{vertical-align:top;text-align:left;padding:0 !important;
background:transparent}
.sample-table-wrap .sample-table td.sample-input,.sample-table-wrap .sample-table td.sample-output{
background:var(--sample-code-bg);color:var(--sample-code-fg)}
.sample-table-wrap .sample-table pre{margin:0;padding:1.2em .8em .7em;min-width:3em;max-width:min(36em,60vw);border:0;
border-radius:0;box-shadow:none;background:transparent;color:var(--sample-code-fg);white-space:pre;
word-wrap:normal;overflow-x:auto;line-height:1.4}
.sample-table-wrap .sample-table pre code{margin:0;padding:0;border:0;background:transparent;color:inherit;
font-size:1em;line-height:inherit;white-space:inherit}
.sample-table-wrap .sample-table td.sample-description{padding:1.2em .8em .7em !important}
.sample-table-wrap .sample-table .sample-text{min-width:8em;max-width:26em}
.sample-table-wrap .sample-table .sample-text p{margin:.5em 0 !important}
.sample-table-wrap .sample-table .sample-text p:first-child{margin-top:0 !important}
.sample-table-wrap .sample-table .sample-text p:last-child{margin-bottom:0 !important}
.sample-table-wrap .sample-table .copy-clipboard{margin:0}
.sample-table-wrap .sample-table .copy-clipboard + pre{padding-right:2.6em}
.sample-table-wrap .sample-table .btn-clipboard{border-width:0 0 1px 1px;border-radius:0 0 0 3px;font-size:0;
padding:3px 6px;line-height:1}
.sample-table-wrap .sample-table .btn-clipboard::before{font:normal 12px/1 FontAwesome;content:"\\f0c5"}
body[data-theme="dark"] .sample-table-wrap .sample-table{--sample-line:#3b3b3b;--sample-head-bg:#181818;
--sample-head-fg:#eee;--sample-code-bg:#111;--sample-code-fg:#f8f8f8}
@media (prefers-color-scheme:dark){body[data-theme="auto"] .sample-table-wrap .sample-table{
--sample-line:#3b3b3b;--sample-head-bg:#181818;--sample-head-fg:#eee;--sample-code-bg:#111;
--sample-code-fg:#f8f8f8}}
@media (max-width:600px){.sample-table-wrap .sample-table th{padding:.4em .6em !important}
.sample-table-wrap .sample-table .sample-text{min-width:5em}
.sample-table-wrap .sample-table pre{min-width:3em !important}}
@media print{.sample-table-wrap .sample-table pre{max-width:none;overflow:visible;white-space:pre-wrap;
overflow-wrap:anywhere}.sample-table-wrap .sample-table td.sample-output pre{overflow-wrap:break-word}}
</style>'''


class AwesomeRenderer(MathRenderer, mistune.Renderer):
    def __init__(self, *args, **kwargs):
        self.nofollow = kwargs.pop('nofollow', True)
        self.texoid = TexoidRenderer() if kwargs.pop('texoid', False) else None
        self.sample_tables = 0
        super(AwesomeRenderer, self).__init__(*args, **kwargs)

    def sample_table(self, rows, descriptions):
        self.sample_tables += 1
        columns = [('sample-input', gettext('Input')), ('sample-output', gettext('Output'))]
        if descriptions:
            columns.append(('sample-description', gettext('Description')))
        head = ''.join('<th class="%s">%s</th>' % (css, mistune.escape(label)) for css, label in columns)
        # Each column as wide as its own samples (and its header): a short output stays narrow next to a long input.
        body = ''.join(self.sample_row(input, output, description) for input, output, description in rows)
        return ('<div class="sample-table-wrap">\n<table class="sample-table">\n<thead><tr>%s</tr></thead>\n'
                '<tbody>\n%s</tbody>\n</table>\n</div>\n') % (head, body)

    def sample_row(self, input, output, description):
        row = ('<tr><td class="sample-input"><pre><code>%s</code></pre></td>'
               '<td class="sample-output"><pre>%s</pre></td>') % (mistune.escape(input, smart_amp=False),
                                                                  mistune.escape(output, smart_amp=False))
        if description is not None:
            row += '<td class="sample-description"><div class="sample-text">%s</div></td>' % description
        return row + '</tr>\n'

    def _link_rel(self, href):
        if href:
            try:
                url = urlparse(href)
            except ValueError:
                return ' rel="nofollow"'
            else:
                if url.netloc and url.netloc not in NOFOLLOW_WHITELIST:
                    return ' rel="nofollow"'
        return ''

    def autolink(self, link, is_email=False):
        text = link = mistune.escape(link)
        if is_email:
            link = 'mailto:%s' % link
        return '<a href="%s"%s>%s</a>' % (link, self._link_rel(link), text)

    def table(self, header, body):
        return (
            '<div class="h-scrollable-table">\n<table class="table">\n<thead>%s</thead>\n'
            '<tbody>\n%s</tbody>\n</table>\n</div>\n'
        ) % (header, body)

    def link(self, link, title, text):
        link = mistune.escape_link(link)
        if not title:
            return '<a href="%s"%s>%s</a>' % (link, self._link_rel(link), text)
        title = mistune.escape(title, quote=True)
        return '<a href="%s" title="%s"%s>%s</a>' % (link, title, self._link_rel(link), text)

    def block_code(self, code, lang=None):
        if not lang:
            return '\n<pre><code>%s</code></pre>\n' % mistune.escape(code).rstrip()
        return highlight_code(code, lang)

    def block_html(self, html):
        if self.texoid and html.startswith('<latex'):
            attr = html[6:html.index('>')]
            latex = html[html.index('>') + 1:html.rindex('<')]
            latex = unescape(latex)
            result = self.texoid.get_result(latex)
            if not result:
                return '<pre>%s</pre>' % mistune.escape(latex, smart_amp=False)
            elif 'error' not in result:
                img = ('''<img src="%(svg)s" onerror="this.src='%(png)s';this.onerror=null"'''
                       'class="tex-full" width="%(width)s" height="%(height)s"%(tail)s>') % {
                    'svg': result['svg'], 'png': result['png'],
                    'width': result['meta']['width'], 'height': result['meta']['height'],
                    'tail': ' /' if self.options.get('use_xhtml') else '',
                }
                style = ['max-width: 100%',
                         'height: %s' % result['meta']['height'],
                         'max-height: %s' % result['meta']['height'],
                         'width: %s' % result['meta']['width']]
                if 'inline' in attr:
                    tag = 'span'
                else:
                    tag = 'div'
                    style += ['text-align: center']
                return '<%s style="%s">%s</%s>' % (tag, ';'.join(style), img, tag)
            else:
                return '<pre>%s</pre>' % mistune.escape(result['error'], smart_amp=False)
        return super(AwesomeRenderer, self).block_html(html)

    def header(self, text, level, *args, **kwargs):
        return super(AwesomeRenderer, self).header(text, level + 2, *args, **kwargs)


cleaner_cache = {}


def get_cleaner(name, params):
    if name in cleaner_cache:
        return cleaner_cache[name]

    styles = params.pop('styles', None)
    if styles:
        params['css_sanitizer'] = CSSSanitizer(allowed_css_properties=all_styles if styles is True else styles)

    if params.pop('mathml', False):
        params['tags'] = params.get('tags', []) + mathml_tags
        params['attributes'] = params.get('attributes', {}).copy()
        params['attributes'].update(mathml_attrs)

    cleaner = cleaner_cache[name] = Cleaner(**params)
    return cleaner


def fragments_to_tree(fragment):
    tree = html.Element('div')
    try:
        parsed = html.fragments_fromstring(fragment, parser=html.HTMLParser(recover=True))
    except (XMLSyntaxError, ParserError) as e:
        if fragment and (not isinstance(e, ParserError) or e.args[0] != 'Document is empty'):
            logger.exception('Failed to parse HTML string')
        return tree

    if parsed and isinstance(parsed[0], str):
        tree.text = parsed[0]
        parsed = parsed[1:]
    tree.extend(parsed)
    return tree


def strip_paragraphs_tags(tree):
    for p in tree.xpath('.//p'):
        for child in p.iterchildren(reversed=True):
            p.addnext(child)
        parent = p.getparent()
        prev = p.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or '') + (p.text or '')
        else:
            parent.text = (parent.text or '') + (p.text or '')
        parent.remove(p)


def fragment_tree_to_str(tree):
    return html.tostring(tree, encoding='unicode')[len('<div>'):-len('</div>')]


@registry.filter
def markdown(value, style, math_engine=None, lazy_load=False, strip_paragraphs=False):
    styles = settings.MARKDOWN_STYLES.get(style, settings.MARKDOWN_DEFAULT_STYLE)
    escape = styles.get('safe_mode', True)
    nofollow = styles.get('nofollow', True)
    texoid = TEXOID_ENABLED and styles.get('texoid', False)
    math = getattr(settings, 'MATHOID_URL') and styles.get('math', False)
    bleach_params = styles.get('bleach', {})

    post_processors = []
    if styles.get('use_camo', False) and camo_client is not None:
        post_processors.append(camo_client.update_tree)
    if lazy_load:
        post_processors.append(lazy_load_processor)

    renderer = AwesomeRenderer(escape=escape, nofollow=nofollow, texoid=texoid,
                               math=math and math_engine is not None, math_engine=math_engine)
    if style in STATEMENT_STYLES:
        markdown = StatementMarkdown(renderer=renderer, inline=StatementInlineLexer, block=SampleTableBlockLexer,
                                     parse_block_html=1, parse_inline_html=1)
    else:
        markdown = mistune.Markdown(renderer=renderer, inline=AwesomeInlineLexer,
                                    parse_block_html=1, parse_inline_html=1)
    result = markdown(value)

    if post_processors or strip_paragraphs:
        tree = fragments_to_tree(result)
        for processor in post_processors:
            processor(tree)
        if strip_paragraphs:
            strip_paragraphs_tags(tree)
        result = fragment_tree_to_str(tree)
    if bleach_params:
        result = get_cleaner(style, bleach_params).clean(result)
    if renderer.sample_tables:
        result += SAMPLE_TABLE_STYLE
    return Markup(result)


# MathJax typesets ~...~ and \(...\) in the browser: MATHOID_URL is off, so the TeX of every Markdown text reaches the
# page as it was written. It runs with its safe extension (markdown-enunciados-2, 2026-10-28): \href keeps only http
# and https links, and \class, \cssId and \style only what is harmless. Without it, ~\href{javascript:...}{...}~ in
# the about of a profile or in a comment ran a script when clicked. Every page takes its configuration from here:
# templates/mathjax-load.html, arena/_mathjax.html, the two PDF pages, and the previews of the editors through
# /widgets/mathjax-config.js. resources/mathjax_config.js, a collected static file, is no longer used.
MATHJAX_CONFIG = {
    'loader': {'load': ['ui/safe']},
    'tex': {'inlineMath': [['~', '~'], ['\\(', '\\)']]},
    'options': {
        'enableMenu': False,
        # No formula makes a link: the check ui/safe does on each address is not enough for what users write
        # (round 3 of the adversarial review, 2026-10-28). The protocols stay as a second line. No formula sets a
        # style either: some styles ui/safe allows load an address of their own (round 5). No published text used
        # \style, \bbox, \fcolorbox or \enclose on 2026-10-28.
        'safeOptions': {'allow': {'URLs': 'none', 'styles': 'none'},
                        'safeProtocols': {'http': True, 'https': True, 'file': False, 'javascript': False,
                                          'data': False}},
    },
}


@registry.function
def mathjax_config(typeset=True):
    """The configuration, as a JavaScript object. The arena typesets each statement on its own (typeset=False)."""
    return Markup(json.dumps(MATHJAX_CONFIG if typeset else dict(MATHJAX_CONFIG, startup={'typeset': False})))

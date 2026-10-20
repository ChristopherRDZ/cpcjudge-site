import re

import mistune

from judge.utils.mathoid import MathoidMathParser

mistune._pre_tags.append('latex')


class MathInlineGrammar(mistune.InlineGrammar):
    block_math = re.compile(r'^\$\$(.*?)\$\$|^\\\[(.*?)\\\]', re.DOTALL)
    math = re.compile(r'^~(.*?)~|^\\\((.*?)\\\)', re.DOTALL)
    text = re.compile(r'^[\s\S]+?(?=[\\<!\[_*`~$]|\\[\[(]|https?://| {2,}\n|$)')


class MathInlineLexer(mistune.InlineLexer):
    grammar_class = MathInlineGrammar

    def __init__(self, *args, **kwargs):
        self.default_rules = self.default_rules[:]
        self.inline_html_rules = self.default_rules
        self.default_rules.insert(self.default_rules.index('strikethrough') + 1, 'math')
        self.default_rules.insert(self.default_rules.index('strikethrough') + 1, 'block_math')
        super(MathInlineLexer, self).__init__(*args, **kwargs)

    def output_block_math(self, m):
        return self.renderer.block_math(m.group(1) or m.group(2))

    def output_math(self, m):
        return self.renderer.math(m.group(1) or m.group(2))

    def output_inline_html(self, m):
        tag = m.group(1)
        text = m.group(3)
        if self._parse_inline_html and text:
            if tag == 'a':
                self._in_link = True
                text = self.output(text)
                self._in_link = False
            else:
                text = self.output(text)
            extra = m.group(2) or ''
            html = '<%s%s>%s</%s>' % (tag, extra, text, tag)
        else:
            html = m.group(0)
        return self.renderer.inline_html(html)


class DollarMathInlineGrammar(MathInlineGrammar):
    # Same as mistune's, plus \$ for a literal dollar sign.
    escape = re.compile(r'^\\([\\`*{}\[\]()#+\-.!_>~|$])')
    # $...$ as inline math, next to ~...~. Pandoc's rules, so that prices stay text: the opening $ is followed by
    # a non-space, the closing $ follows a non-space and is not followed by a digit ("$5 and $10" is not math).
    # A formula cannot hold a backtick, so `$` in inline code never opens one.
    dollar_math = re.compile(r'^\$(?![\s$])((?:\\[\s\S]|[^\\$`])+?)(?<!\s)\$(?!\d)')


class DollarMathInlineLexer(MathInlineLexer):
    grammar_class = DollarMathInlineGrammar

    def __init__(self, *args, **kwargs):
        super(DollarMathInlineLexer, self).__init__(*args, **kwargs)
        # After $$...$$, so that display math keeps its meaning. inline_html_rules is the same list.
        self.default_rules.insert(self.default_rules.index('block_math') + 1, 'dollar_math')

    def output_dollar_math(self, m):
        return self.renderer.math(m.group(1))


class MathRenderer(mistune.Renderer):
    def __init__(self, *args, **kwargs):
        if kwargs.pop('math', False):
            self.mathoid = MathoidMathParser(kwargs.pop('math_engine', None) or 'svg')
        else:
            self.mathoid = None
        super(MathRenderer, self).__init__(*args, **kwargs)

    def block_math(self, math):
        if self.mathoid is None or not math:
            return r'\[%s\]' % mistune.escape(str(math))
        return self.mathoid.display_math(math)

    def math(self, math):
        if self.mathoid is None or not math:
            return r'\(%s\)' % mistune.escape(str(math))
        return self.mathoid.inline_math(math)

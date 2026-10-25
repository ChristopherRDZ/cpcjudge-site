"""Emails to many users: who receives them and what they look like.

Recipients. Only accounts that confirmed their email (`User.is_active`) can receive one; accounts that never
confirmed it, or that an administrator deactivated, never do. Among them, the email says who (`audience`):

* all of them;
* only who meets the filters: each filter narrows the list and all of them must hold; inside a filter with several
  choices (contests, organizations, groups) one is enough; "always include" adds people the filters left out;
* only the people chosen by name ("always include" is then the whole list).

"Never include" wins over everything, and two accounts with the same address get a single email. Until 2026-10-23
there was no `audience`: an email without filters went to everybody even if it named people to include, which
looked like "only these people" and was not. Those emails keep that meaning (effective_audience).

Content. Markdown, as in news posts, with HTML allowed and then cleaned. Email programs ignore most of a page's
CSS, so every element gets its style inline, links and images become absolute, and the text goes inside the same
frame as the site's other emails (registration/email_base.html). MathJax does not exist in email: formulas with
~...~ arrive as plain text.
"""
import re
from datetime import datetime, time, timedelta
from functools import reduce
from operator import and_
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.contrib.auth.models import Group
from django.db.models import Q
from django.template import loader
from django.utils import formats, timezone, translation
from django.utils.html import escape
from django.utils.translation import gettext, ngettext
from markupsafe import Markup

from judge.jinja2.markdown import fragment_tree_to_str, fragments_to_tree, markdown
from judge.models import Contest, ContestParticipation, MassEmail, Organization, Profile
from judge.models.team import ContestTeamMember

FILTER_FIELDS = ('audience', 'contests', 'include_virtual', 'organizations', 'groups', 'active_within_days',
                 'joined_after', 'joined_before', 'min_problems', 'min_rating', 'max_rating', 'include_users',
                 'exclude_users')
MANY_FIELDS = ('contests', 'organizations', 'groups', 'include_users', 'exclude_users')
# The filters that narrow the list, used only when the email goes to who meets the filters.
NARROWING_FIELDS = ('contests', 'organizations', 'groups', 'active_within_days', 'joined_after', 'joined_before',
                    'min_problems', 'min_rating', 'max_rating')
# A rating of 0 is a rating; the other numbers mean nothing at 0, as before.
ZERO_COUNTS = ('min_rating', 'max_rating')

# What the author writes to greet each person by name, and what Resend replaces for each contact.
NAME_PLACEHOLDERS = ('{{usuario}}', '{{username}}')
RESEND_NAME = '{{{contact.first_name}}}'
RESEND_UNSUBSCRIBE_URL = '{{{RESEND_UNSUBSCRIBE_URL}}}'

EMAIL_SHAPE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# --- recipients ------------------------------------------------------------------------------------------------

def empty_filters():
    filters = {name: ([] if name in MANY_FIELDS else None) for name in FILTER_FIELDS}
    filters['audience'] = ''
    return filters


def filters_of(mail):
    """The filters of a saved email."""
    filters = {name: getattr(mail, name) for name in FILTER_FIELDS if name not in MANY_FIELDS}
    for name in MANY_FIELDS:
        filters[name] = list(getattr(mail, name).values_list('pk', flat=True))
    return filters


def filters_from_form(cleaned_data):
    """The filters as they are in a form, saved or not."""
    filters = empty_filters()
    for name in FILTER_FIELDS:
        value = cleaned_data.get(name)
        if name in MANY_FIELDS:
            filters[name] = [obj.pk for obj in value] if value else []
        else:
            filters[name] = value
    return filters


def is_set(filters, name):
    value = filters.get(name)
    return value is not None if name in ZERO_COUNTS else bool(value)


def has_filters(filters):
    return any(is_set(filters, name) for name in NARROWING_FIELDS)


def effective_audience(filters):
    """all, filters or users. An email saved before `audience` existed has it empty: as then, with filters it goes
    to who meets them, and without them to everybody."""
    audience = filters.get('audience')
    if audience in (MassEmail.ALL, MassEmail.FILTERS, MassEmail.USERS):
        return audience
    return MassEmail.FILTERS if has_filters(filters) else MassEmail.ALL


def _start_of(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def confirmed_profiles():
    return Profile.objects.filter(user__is_active=True, user__email__contains='@')


def recipient_profiles(filters):
    audience = effective_audience(filters)
    profiles = confirmed_profiles()
    if audience == MassEmail.USERS:
        profiles = profiles.filter(id__in=filters['include_users'] or [])
    elif audience == MassEmail.FILTERS:
        # Who meets every filter, plus who is always included. With no filter at all, nobody: the form asks for
        # one, and "all" is said with the first choice, not by leaving the filters empty.
        selected = reduce(and_, _conditions(filters), Q()) if has_filters(filters) else Q(pk__in=[])
        if filters['include_users']:
            selected |= Q(id__in=filters['include_users'])
        profiles = profiles.filter(selected)
    if filters['exclude_users']:
        profiles = profiles.exclude(id__in=filters['exclude_users'])
    return profiles


def _conditions(filters):
    conditions = []
    if filters['contests']:
        participations = ContestParticipation.objects.filter(contest_id__in=filters['contests'])
        if filters['include_virtual']:
            participations = participations.filter(virtual__gte=ContestParticipation.LIVE)
        else:
            participations = participations.filter(virtual=ContestParticipation.LIVE)
        members = ContestTeamMember.objects.filter(participation__in=participations).values('profile_id')
        conditions.append(Q(id__in=participations.values('user_id')) | Q(id__in=members))
    if filters['organizations']:
        conditions.append(Q(id__in=Profile.organizations.through.objects
                            .filter(organization_id__in=filters['organizations']).values('profile_id')))
    if filters['groups']:
        conditions.append(Q(user_id__in=User.groups.through.objects
                            .filter(group_id__in=filters['groups']).values('user_id')))
    if filters['active_within_days']:
        conditions.append(Q(last_access__gte=timezone.now() - timedelta(days=filters['active_within_days'])))
    if filters['joined_after']:
        conditions.append(Q(user__date_joined__gte=_start_of(filters['joined_after'])))
    if filters['joined_before']:
        conditions.append(Q(user__date_joined__lt=_start_of(filters['joined_before'] + timedelta(days=1))))
    if filters['min_problems']:
        conditions.append(Q(problem_count__gte=filters['min_problems']))
    if is_set(filters, 'min_rating'):
        conditions.append(Q(rating__gte=filters['min_rating']))
    if is_set(filters, 'max_rating'):
        conditions.append(Q(rating__lte=filters['max_rating']) | Q(rating__isnull=True))
    return conditions


def _names(model, ids, field, limit=3):
    names = list(model.objects.filter(pk__in=ids).order_by(field).values_list(field, flat=True)[:limit])
    if len(ids) > limit:
        names.append(ngettext('%(count)d more', '%(count)d more', len(ids) - limit) % {'count': len(ids) - limit})
    return ', '.join(names)


def describe(filters):
    """Who receives an email, in words, one line per part: what the counter, the send page and a sent email say."""
    audience = effective_audience(filters)
    people = len(filters['include_users'] or [])
    if audience == MassEmail.ALL:
        lines = [gettext('All accounts with a confirmed email.')]
    elif audience == MassEmail.USERS:
        lines = [ngettext('Only %(count)d chosen person.', 'Only %(count)d chosen people.', people) %
                 {'count': people}]
    else:
        lines = [gettext('Accounts with a confirmed email that:')]
        if filters['contests']:
            lines.append((gettext('took part in any of these contests, virtual participations included: %(names)s')
                          if filters['include_virtual'] else
                          gettext('took part in any of these contests: %(names)s')) %
                         {'names': _names(Contest, filters['contests'], 'name')})
        if filters['organizations']:
            lines.append(gettext('are members of any of these organizations: %(names)s') %
                         {'names': _names(Organization, filters['organizations'], 'name')})
        if filters['groups']:
            lines.append(gettext('are in any of these permission groups: %(names)s') %
                         {'names': _names(Group, filters['groups'], 'name')})
        if filters['active_within_days']:
            lines.append(gettext('visited the site in the last %(days)d days') %
                         {'days': filters['active_within_days']})
        if filters['joined_after']:
            lines.append(gettext('signed up on or after %(date)s') %
                         {'date': formats.date_format(filters['joined_after'], 'SHORT_DATE_FORMAT')})
        if filters['joined_before']:
            lines.append(gettext('signed up on or before %(date)s') %
                         {'date': formats.date_format(filters['joined_before'], 'SHORT_DATE_FORMAT')})
        if filters['min_problems']:
            lines.append(gettext('solved at least %(count)d problems') % {'count': filters['min_problems']})
        if is_set(filters, 'min_rating'):
            lines.append(gettext('have a rating of at least %(rating)d') % {'rating': filters['min_rating']})
        if is_set(filters, 'max_rating'):
            lines.append(gettext('have a rating of at most %(rating)d, or no rating') %
                         {'rating': filters['max_rating']})
        if not has_filters(filters):
            lines.append(gettext('(no filter chosen yet)'))
        if people:
            lines.append(ngettext('Also %(count)d person chosen by name.', 'Also %(count)d people chosen by name.',
                                  people) % {'count': people})
    excluded = len(filters['exclude_users'] or [])
    if excluded:
        lines.append(ngettext('Never: %(count)d person.', 'Never: %(count)d people.', excluded) % {'count': excluded})
    return lines


def recipient_rows(filters):
    """[(profile id, username, email)], one per address, in account order."""
    rows, seen = [], set()
    for profile_id, username, email in recipient_profiles(filters).order_by('id') \
            .values_list('id', 'user__username', 'user__email'):
        email = (email or '').strip()
        key = email.lower()
        if not EMAIL_SHAPE.match(email) or key in seen:
            continue
        seen.add(key)
        rows.append((profile_id, username, email))
    return rows


# --- rendering -------------------------------------------------------------------------------------------------

FONT = "font-family:'Segoe UI','Lucida Grande',Arial,sans-serif;"
MONO = "font-family:Consolas,'Courier New',monospace;"
# The site's Markdown moves every heading two levels down (# is <h3>), because the page already has its own <h1>
# and <h2>; the sizes follow what the author wrote: # 20px, ## 17px, ### 15px.
TAG_STYLES = {
    'h1': 'margin:24px 0 12px;font-size:22px;line-height:1.3;font-weight:bold;color:#222222;',
    'h2': 'margin:22px 0 10px;font-size:20px;line-height:1.3;font-weight:bold;color:#222222;',
    'h3': 'margin:22px 0 10px;font-size:20px;line-height:1.3;font-weight:bold;color:#222222;',
    'h4': 'margin:20px 0 8px;font-size:17px;line-height:1.3;font-weight:bold;color:#222222;',
    'h5': 'margin:18px 0 8px;font-size:15px;line-height:1.3;font-weight:bold;color:#222222;',
    'h6': 'margin:16px 0 6px;font-size:14px;font-weight:bold;color:#444444;',
    'p': 'margin:0 0 16px;',
    'a': 'color:#1958c1;',
    'img': 'max-width:100%;height:auto;border:0;',
    'ul': 'margin:0 0 16px;padding-left:24px;',
    'ol': 'margin:0 0 16px;padding-left:24px;',
    'li': 'margin:0 0 6px;',
    'blockquote': 'margin:0 0 16px;padding:8px 14px;border-left:4px solid #cccccc;color:#555555;',
    'pre': 'margin:0 0 16px;padding:10px 12px;background-color:#f8f8f8;border:1px solid #cccccc;border-radius:3px;'
           'white-space:pre-wrap;font-size:13px;line-height:1.45;' + MONO,
    'code': 'padding:1px 4px;background-color:#f3f3f3;border-radius:3px;font-size:90%;' + MONO,
    'table': 'margin:0 0 16px;border-collapse:collapse;',
    'th': 'padding:6px 10px;border:1px solid #cccccc;background-color:#f8f8f8;text-align:left;',
    'td': 'padding:6px 10px;border:1px solid #cccccc;',
    'hr': 'margin:20px 0;border:0;border-top:1px solid #cccccc;',
}
CODE_IN_PRE = MONO
# Shortcuts for the author, written as class="..." on any element. Spanish names work too.
CLASS_STYLES = {
    'button': 'display:inline-block;padding:12px 24px;background-color:#1958c1;color:#ffffff;font-weight:bold;'
              'line-height:1.2;text-decoration:none;border-radius:3px;',
    'note': 'display:block;margin:0 0 16px;padding:12px 14px;background-color:#f8f8f8;border:1px solid #cccccc;'
            'border-radius:3px;color:#444444;',
    'alert': 'display:block;margin:0 0 16px;padding:12px 14px;background-color:#ffffff;border:1px solid #cccccc;'
             'border-left:4px solid #d52020;border-radius:3px;font-weight:bold;',
    'center': 'margin:0 0 16px;text-align:center;',
}
CLASS_ALIASES = {'boton': 'button', 'nota': 'note', 'aviso': 'alert', 'centro': 'center'}


def site_url():
    """https://domain of the site, without the final slash: links in an email must be absolute."""
    configured = getattr(settings, 'DMOJ_MASS_EMAIL_SITE_URL', None)
    if configured:
        return configured.rstrip('/')
    return 'https://' + Site.objects.get_current().domain


def render_body(content, base_url=None):
    """The content as email HTML: cleaned, styled inline, with absolute links."""
    base_url = (base_url or site_url()) + '/'
    tree = fragments_to_tree(str(markdown(content or '', 'email')))
    for element in tree.iter():
        if not isinstance(element.tag, str) or element is tree:
            continue
        for attribute in ('href', 'src'):
            value = (element.get(attribute) or '').strip()
            if value and not value.startswith(('mailto:', '#', '{{')):
                element.set(attribute, urljoin(base_url, value))
        style = TAG_STYLES.get(element.tag, '')
        parent = element.getparent()
        if element.tag == 'code' and parent is not None and parent.tag == 'pre':
            style = CODE_IN_PRE
        classes = [CLASS_ALIASES.get(name, name) for name in (element.get('class') or '').lower().split()]
        if 'button' in classes:
            style = ''
        for name in classes:
            style += CLASS_STYLES.get(name, '')
        own = (element.get('style') or '').strip()
        if own and not own.endswith(';'):
            own += ';'
        if style or own:
            element.set('style', style + own)
    return fragment_tree_to_str(tree)


PRE_MARK = '\x00'
BLOCKS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'table', 'tr', 'blockquote', 'pre',
          'hr', 'section', 'details', 'summary', 'dl', 'dt', 'dd', 'address', 'center'}


def html_to_text(fragment):
    """A readable plain-text version, for the programs that show no HTML."""
    tree = fragments_to_tree(fragment)
    parts = []

    def text_of(value):
        # Whitespace with a line break only separates blocks in the HTML: it is not part of the text.
        if value and not value.strip() and '\n' in value:
            return ' '
        return value or ''

    def walk(element):
        tag = element.tag if isinstance(element.tag, str) else None
        if tag in BLOCKS:
            parts.append('\n\n' if tag not in ('li', 'tr') else '\n')
        if tag == 'li':
            parts.append('- ')
        if tag == 'br':
            parts.append('\n')
        if tag == 'hr':
            parts.append('----------')
        if tag == 'img' and element.get('alt'):
            parts.append(element.get('alt'))
        if tag == 'pre':
            # Marked so that its spaces survive; every other line has its runs of spaces collapsed.
            parts.append('\n'.join(PRE_MARK + line for line in element.text_content().splitlines()))
        else:
            if tag is not None:
                parts.append(text_of(element.text))
            for child in element:
                walk(child)
            if tag == 'a' and element.get('href'):
                label = element.text_content().strip()
                href = element.get('href')
                if href.startswith('mailto:'):
                    href = href[len('mailto:'):]
                if href != label:
                    parts.append(' (%s)' % href)
            if tag in ('td', 'th'):
                parts.append('  ')
        if tag in BLOCKS:
            parts.append('\n\n' if tag not in ('li', 'tr') else '')
        if element is not tree:
            parts.append(text_of(element.tail))

    walk(tree)
    lines = []
    for line in ''.join(parts).splitlines():
        if line.startswith(PRE_MARK):
            lines.append(line[len(PRE_MARK):].rstrip())
        else:
            lines.append(' '.join(line.split()))
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip('\n')


def _greet(text, name):
    for placeholder in NAME_PLACEHOLDERS:
        text = text.replace(placeholder, name)
    return text


def render_email(mail, username='', for_resend=False):
    """(subject, html, text) of an email, saved or not.

    For Resend the name and the unsubscribe link are Resend's variables, filled in for each contact. Otherwise
    (preview, test) the name is `username` and the unsubscribe link goes to the site.
    """
    base_url = site_url()
    parts = urlsplit(base_url)
    body = render_body(mail.content, base_url)
    site_name = Site.objects.get_current().name
    unsubscribe_url = RESEND_UNSUBSCRIBE_URL if for_resend else base_url + '/'
    with translation.override(mail.language or 'es'):
        page = loader.render_to_string('mass_email/email.html', {
            'protocol': parts.scheme, 'domain': parts.netloc, 'site_name': site_name,
            'site_admin_email': getattr(settings, 'SITE_ADMIN_EMAIL', ''),
            'preheader': mail.preheader, 'heading': mail.heading, 'body': Markup(body),
            'unsubscribe_url': unsubscribe_url,
        })
        footer = [gettext('You receive this email because you have an account on %(site_name)s.') %
                  {'site_name': site_name},
                  gettext('CPC-UAEH is an independent student club; it is not an official UAEH service.'),
                  gettext('To stop receiving these emails: %(url)s') % {'url': unsubscribe_url},
                  gettext('Privacy notice: %(url)s') % {'url': base_url + '/privacidad/'},
                  base_url]
    text = '\n\n'.join(filter(None, [mail.heading, html_to_text(body)])) + '\n\n--\n' + '\n'.join(footer) + '\n'
    name_html = RESEND_NAME if for_resend else str(escape(username))
    name_text = RESEND_NAME if for_resend else username
    # The subject has no name: the form refuses it there, because Resend may not fill variables in subjects.
    return mail.subject, _greet(page, name_html), _greet(text, name_text)


def preview_fragment(content):
    """What the editor's preview pane shows: the body styled as in the email."""
    return Markup('<div style="%sfont-size:15px;line-height:1.55;color:#333333;max-width:600px;">%s</div>' %
                  (FONT, render_body(content)))


def sender():
    return getattr(settings, 'DMOJ_MASS_EMAIL_FROM', None) or settings.DEFAULT_FROM_EMAIL


def reply_to():
    return getattr(settings, 'DMOJ_MASS_EMAIL_REPLY_TO', None) or getattr(settings, 'SITE_ADMIN_EMAIL', '') or None

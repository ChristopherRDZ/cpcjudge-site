"""Keeping a player inside the arena while their battle lasts.

Called from ContestMiddleware on every request, so it must be cheap and must never break the site: one read from
the shared cache, and any failure (cache down, key missing, anything unexpected) lets the request through. It is a
convenience for the player, not a security barrier; ProblemSubmit has its own check against the database.

The cached value maps each held profile id to the UNIX time until which it is held (engine.lock_entries_from_db):
a battle left open by mistake stops holding its players after a while without activity, even if nothing else
rewrites the entry.
"""
import logging
import re
import time

from django.core.cache import cache
from django.http import HttpResponseRedirect, JsonResponse
from django.utils.translation import gettext as _

logger = logging.getLogger('judge.arena')

LOCK_KEY = 'arena:locked'
# The page of the duels; /arena/ was its address until 2026-10-19 and still forwards there (arena.legacy_redirect).
HOME = '/duelos/'

ALLOWED_PREFIXES = (
    HOME,
    '/arena/',
    '/accounts/logout/',
    '/custom-test/run/',
    '/announcements/active',
    '/widgets/template',
    '/i18n/',
    '/jsi18n',
    '/static/',
    '/media/',
    # base.html and its team include poll these on every page, the arena included.
    '/teams/invitations/count/',
)
# Icons the browser asks for on its own (favicon.ico, apple-touch-icon-180x180.png, manifest.json, ...).
ROOT_FILE = re.compile(r'^/[A-Za-z0-9_.-]+\.(?:png|ico|svg|xml|json|webmanifest)$')


def _wants_page(request):
    if request.method not in ('GET', 'HEAD'):
        return False
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return False
    accept = request.headers.get('accept', '')
    return not accept or 'text/html' in accept or '*/*' == accept.strip()


def is_held(profile_id):
    """Whether this profile is held inside a battle right now. Same reading and same leniency as the middleware: any
    failure says no. Everything under /duelos/ is let through by locked_response, so the pages of the history
    (judge/views/arena_history.py) ask this themselves and send the player back to the battle."""
    if not profile_id:
        return False
    try:
        locked = cache.get(LOCK_KEY)
        if not locked or not isinstance(locked, dict):
            return False
        until = locked.get(profile_id)
        return bool(until and until > time.time())
    except Exception:
        logger.warning('Arena lock check failed; treating the player as free.', exc_info=True)
        return False


def locked_response(request):
    """A response that sends a player back to the arena, or None to let the request through. Never raises."""
    try:
        profile = getattr(request, 'profile', None)
        if profile is None:
            return None
        path = request.path_info or ''
        if path in ('/duelos', '/arena') or path.startswith(ALLOWED_PREFIXES) or ROOT_FILE.match(path):
            return None
        locked = cache.get(LOCK_KEY)
        if not locked or not isinstance(locked, dict):
            return None
        until = locked.get(profile.id)
        if not until or until <= time.time():
            return None
        if _wants_page(request):
            return HttpResponseRedirect(HOME)
        return JsonResponse({'error': _('You are in a battle. Go back to the arena.'), 'arena': HOME},
                            status=403)
    except Exception:
        logger.warning('Arena lock check failed; letting the request through.', exc_info=True)
        return None

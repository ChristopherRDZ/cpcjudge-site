"""Keeping a player inside the arena while their battle lasts.

Called from ContestMiddleware on every request, so it must be cheap and must never break the site: one read from
the shared cache (and, only for whoever it says is held, a look at the database), and any failure (cache down, key
missing, anything unexpected) lets the request through. It is a convenience for the player, not a security barrier;
ProblemSubmit has its own check against the database.

The cached value maps each held profile id to the UNIX time until which it is held (engine.lock_entries_from_db):
a battle left open by mistake stops holding its players after a while without activity, even if nothing else
rewrites the entry.
"""
import logging
import re
import time

from django.core.cache import cache
from django.db.models import Q
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
    # The pictures of the players (judge/jinja2/gravatar.py).
    '/avatar/',
    # base.html and its team include poll these on every page, the arena included.
    '/teams/invitations/count/',
)
# Where judge.middleware.DMOJLoginMiddleware sends an account that still owes its second factor, or whose password
# was found in a leak (cuentas-correo-ajustes-1, 2026-10-28): sending a held player back to the arena from there,
# and the middleware back there from the arena, looped until the browser gave up.
ALLOWED_PATHS = (
    '/accounts/2fa/',
    '/accounts/2fa/webauthn/assert/',
    '/accounts/password/change/',
    '/accounts/password/change/done/',
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


def _still_held(profile_id):
    """Whether the database agrees that this profile is held, asked only for whoever the cache says is
    (duelos-gestion-js-1, 2026-10-28). The cache is rewritten whole after every change of a battle, so two changes
    at once could leave an old picture, which kept the players of a finished battle out of the site for an hour. When
    the database disagrees, the cache is rewritten from it."""
    from judge.arena import engine
    from judge.models.arena import ArenaMatch

    # Every battle that holds them, as the cache is built (engine.lock_entries_from_db): a player can be in two.
    for match in ArenaMatch.objects.filter(status__in=ArenaMatch.LOCKING_STATUSES) \
            .filter(Q(player_a_id=profile_id) | Q(player_b_id=profile_id)):
        games = list(match.games.only('status', 'started_at', 'finished_at', 'ends_at'))
        if engine.lock_until(match, games).timestamp() > time.time():
            return True
    engine.refresh_lock()
    return False


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
        return bool(until and until > time.time() and _still_held(profile_id))
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
        if path in ('/duelos', '/arena') or path in ALLOWED_PATHS or path.startswith(ALLOWED_PREFIXES) or \
                ROOT_FILE.match(path):
            return None
        locked = cache.get(LOCK_KEY)
        if not locked or not isinstance(locked, dict):
            return None
        until = locked.get(profile.id)
        if not until or until <= time.time() or not _still_held(profile.id):
            return None
        if _wants_page(request):
            return HttpResponseRedirect(HOME)
        return JsonResponse({'error': _('You are in a battle. Go back to the arena.'), 'arena': HOME},
                            status=403)
    except Exception:
        logger.warning('Arena lock check failed; letting the request through.', exc_info=True)
        return None

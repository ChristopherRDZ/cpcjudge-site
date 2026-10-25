"""Arena pages for players and for the projector screens. On the site the arena is called «Duelos» (since 2026-10-19).

Players get one page, /duelos/, that follows them through the whole battle on its own: waiting room, countdown,
play and result. It asks the server for its state every second or two; there is no other channel.

Screens are opened with a secret link and no login. After the first load the token travels in a header, not in
the URL, so it does not end up in access logs with every poll.
"""
import json
import logging

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from judge.arena import access, engine, live_code, serialize
from judge.arena.engine import ArenaError
from judge.models import ProblemTranslation
from judge.models.arena import ArenaGame, ArenaGameProblem, ArenaScreen

logger = logging.getLogger('judge.arena')

MAX_BODY = 200 * 1024
SCREEN_HEADER = 'X-Arena-Screen'


def _limit(name, default):
    """Request limits, adjustable from the private settings file without touching the code; 0 turns one off.

    The defaults leave room to spare: a player asks for its state about once a second and sends its code about three
    times a second while typing, and every open tab of a screen asks for the code about three times a second.
    """
    return engine.arena_setting(name, default)


__all__ = ['arena_home', 'arena_state', 'arena_statement', 'arena_submit', 'arena_code', 'arena_code_mine',
           'arena_submission', 'arena_screen', 'arena_screen_state', 'arena_screen_code', 'arena_screen_statement',
           'json_response', 'json_error', 'read_json', 'can_manage', 'legacy_redirect']


class TemporaryRedirect(HttpResponseRedirect):
    status_code = 307


@csrf_exempt
def legacy_redirect(request, rest=None):
    """Everything under /arena/, the address before 2026-10-19, goes to the same place under /duelos/: the link of a
    projector screen saved before, a page left open that keeps polling. 307 keeps the method and the body, so an open
    player page keeps sending its code (the target checks CSRF as always; this view does nothing else). Temporary on
    purpose: a permanent one would stay in the browsers even if the change were rolled back."""
    target = '/duelos' + (rest or '/')
    query = request.META.get('QUERY_STRING', '')
    return TemporaryRedirect(target + ('?' + query if query else ''))


def json_response(data, status=200):
    response = JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})
    response['Cache-Control'] = 'no-store'
    return response


def json_error(message, status=400):
    return json_response({'error': str(message)}, status=status)


class BodyTooLarge(ArenaError):
    def __init__(self):
        super().__init__(_('The request is too large.'), status=413)


def read_json(request, limit=MAX_BODY):
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        length = 0
    if length > limit:
        raise BodyTooLarge()
    body = request.body
    if len(body) > limit:
        raise BodyTooLarge()
    try:
        data = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise ArenaError(_('Invalid request.'))
    if not isinstance(data, dict):
        raise ArenaError(_('Invalid request.'))
    return data


def can_manage(user):
    """May open the battle panel: with either permission (judge/arena/access.py says which battles)."""
    return access.can_manage(user)


def _ace_context():
    return {
        'ACE_URL': settings.ACE_URL,
        'ace_light_theme': settings.ACE_DEFAULT_LIGHT_THEME,
        'ace_dark_theme': settings.ACE_DEFAULT_DARK_THEME,
    }


# ---------------------------------------------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------------------------------------------

@login_required
@require_GET
def arena_home(request):
    state = serialize.player_state(request.profile, request.LANGUAGE_CODE)
    context = {
        'title': _('Arena'),
        'state': state,
        'can_manage': can_manage(request.user),
        'ace_theme': request.profile.resolved_ace_theme,
        'default_language_key': request.profile.language.key if request.profile.language_id else '',
    }
    context.update(_ace_context())
    return render(request, 'arena/play.html', context)


@login_required
@require_GET
@never_cache
def arena_state(request):
    request.no_profile_update = True
    if not live_code.allow('state', request.profile.pk, _limit('CPC_ARENA_STATE_PER_SECOND', 6)):
        return json_error(_('Too many requests.'), status=429)
    return json_response(serialize.player_state(request.profile, request.LANGUAGE_CODE))


def _player_game(request, game_id):
    game = ArenaGame.objects.select_related('match', 'match__tournament', 'contest').filter(pk=game_id).first()
    if game is None:
        raise Http404()
    slot = game.match.slot_of(request.profile.pk)
    if slot is None:
        raise Http404()
    return game, slot


def _statement_context(request, game, order):
    if game.started_at is None or timezone.now() < game.started_at or game.status == ArenaGame.PREPARED:
        raise Http404()
    gp = ArenaGameProblem.objects.filter(game=game, order=order).select_related('problem').first()
    if gp is None:
        raise Http404()
    problem = gp.problem
    try:
        translation = problem.translations.get(language=request.LANGUAGE_CODE)
    except ProblemTranslation.DoesNotExist:
        title, description, language = problem.name, problem.description, settings.LANGUAGE_CODE
    else:
        title, description, language = translation.name, translation.description, request.LANGUAGE_CODE
    return {'problem': problem, 'game_problem': gp, 'title': title, 'description': description,
            'language': language}


@login_required
@require_GET
def arena_statement(request, game, order):
    game = ArenaGame.objects.select_related('match').filter(pk=game).first()
    if game is None:
        raise Http404()
    # Besides its two players, only who organizes that battle: the problems of a game are not for every organizer.
    if game.match.slot_of(request.profile.pk) is None and not access.can_manage_match(request.user, game.match):
        raise Http404()
    response = render(request, 'arena/statement.html', _statement_context(request, game, order))
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_POST
def arena_submit(request, game):
    try:
        if not live_code.allow('submit', request.profile.pk, _limit('CPC_ARENA_SUBMISSIONS_PER_10_SECONDS', 4),
                               window=10):
            raise ArenaError(_('You are submitting too fast. Wait a few seconds.'), status=429)
        data = read_json(request)
        order = data.get('problem')
        submission = engine.submit(request.profile, request.user, game, order, data.get('language'),
                                   data.get('source'), data.get('nonce', ''))
        game_obj, slot = _player_game(request, game)
        if isinstance(order, int):
            live_code.store(game_obj.pk, slot, order, submission.language.key, submission.source.source,
                            reason='submit')
        return json_response({'id': submission.pk})
    except ArenaError as error:
        return json_error(error.message, error.status)


@login_required
@require_POST
def arena_code(request, game):
    request.no_profile_update = True
    try:
        if not live_code.allow('code', request.profile.pk, _limit('CPC_ARENA_CODE_PER_SECOND', 4)):
            raise ArenaError(_('Too many requests.'), status=429)
        data = read_json(request)
        game_obj, slot = _player_game(request, game)
        if game_obj.status not in ArenaGame.OPEN_STATUSES:
            return json_response({'seq': None, 'accepted': False})
        order, text, language = data.get('problem'), data.get('text'), data.get('language') or ''
        if isinstance(order, bool) or not isinstance(order, int) or not isinstance(text, str) or \
                not isinstance(language, str) or len(language) > 6:
            raise ArenaError(_('Invalid request.'))
        if len(text) > live_code.MAX_TEXT:
            raise BodyTooLarge()
        if not ArenaGameProblem.objects.filter(game=game_obj, order=order).exists():
            raise ArenaError(_('That problem is not part of this game.'))
        reason = data.get('reason') if data.get('reason') in ('template', 'restore') else ''
        seq = live_code.store(game_obj.pk, slot, order, language, text, data.get('cursor'),
                              data.get('counters'), reason)
        return json_response({'seq': seq, 'accepted': True})
    except ArenaError as error:
        return json_error(error.message, error.status)


@login_required
@require_GET
def arena_code_mine(request, game):
    request.no_profile_update = True
    game_obj, slot = _player_game(request, game)
    if game_obj.status not in ArenaGame.ACTIVE_STATUSES and not (
            game_obj.status in ArenaGame.DONE_STATUSES and serialize.own_account(game_obj.match, slot)):
        # Drafts are for picking up a game in progress on another machine, and for a player on their own account
        # to look over a finished one. Never for the next guest on a shared club laptop.
        return json_response({'drafts': {}})
    orders = list(ArenaGameProblem.objects.filter(game=game_obj).values_list('order', flat=True))
    found = live_code.drafts(game_obj.pk, slot, orders)
    return json_response({'drafts': {str(order): {'language': draft.get('language', ''), 'text': draft.get('text', '')}
                                     for order, draft in found.items()}})


@login_required
@require_GET
def arena_submission(request, game, submission):
    request.no_profile_update = True
    game_obj, slot = _player_game(request, game)
    data = serialize.player_submission(request.profile, game_obj, submission)
    if data is None:
        raise Http404()
    return json_response(data)


# ---------------------------------------------------------------------------------------------------------------
# Projector screens
# ---------------------------------------------------------------------------------------------------------------

def _screen_from_header(request):
    token = request.headers.get(SCREEN_HEADER, '')
    if not token or len(token) > 64:
        raise Http404()
    screen = ArenaScreen.objects.select_related('match', 'match__tournament', 'tournament').filter(token=token) \
        .first()
    if screen is None:
        raise Http404()
    return screen


def _private(response):
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@require_GET
def arena_screen(request, token):
    if len(token) > 64:
        raise Http404()
    screen = ArenaScreen.objects.filter(token=token).first()
    if screen is None:
        raise Http404()
    request.no_profile_update = True
    context = {
        'title': screen.name,
        'screen_token': screen.token,
        'state': serialize.screen_state(screen, request.LANGUAGE_CODE),
        'view': request.GET.get('view', 'combined'),
        'forced_theme': request.GET.get('theme') if request.GET.get('theme') in ('dark', 'light') else '',
    }
    context.update(_ace_context())
    return _private(render(request, 'arena/screen.html', context))


@require_GET
def arena_screen_state(request):
    request.no_profile_update = True
    screen = _screen_from_header(request)
    if not live_code.allow('screen', screen.pk, _limit('CPC_ARENA_SCREEN_PER_SECOND', 30)):
        return json_error(_('Too many requests.'), status=429)
    return _private(json_response(serialize.screen_state(screen, request.LANGUAGE_CODE)))


def _since(request):
    since = {}
    for slot in ('a', 'b'):
        try:
            since[slot] = max(0, int(request.GET.get(slot, 0)))
        except (TypeError, ValueError):
            since[slot] = 0
    return since


@require_GET
def arena_screen_code(request):
    request.no_profile_update = True
    screen = _screen_from_header(request)
    if not live_code.allow('screen-code', screen.pk, _limit('CPC_ARENA_SCREEN_PER_SECOND', 30)):
        return json_error(_('Too many requests.'), status=429)
    game = serialize.screen_game(screen)
    if game is None or game.started_at is None:
        return _private(json_response({'enabled': False}))
    return _private(json_response(serialize.code_payload(game, _since(request), enforce_screen_rule=True)))


@require_GET
def arena_screen_statement(request, order):
    request.no_profile_update = True
    screen = _screen_from_header(request)
    game = serialize.screen_game(screen)
    if game is None:
        raise Http404()
    response = render(request, 'arena/statement.html', _statement_context(request, game, order))
    return _private(response)

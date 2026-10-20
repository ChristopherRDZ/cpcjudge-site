"""The organizer side of the arena: the panel, quick battles, tournaments, problem banks and projector screens.

Everything here requires `judge.manage_arena`; anybody else is told the page does not exist. Every change is a
JSON POST carrying the version of the object it was made on, so two organizers working at once get a conflict
instead of overwriting each other, and the panel refreshes itself every couple of seconds.
"""
import logging
from functools import wraps

from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from judge.arena import engine, rules as arena_rules, serialize, tournament as arena_tournament
from judge.arena.engine import ArenaError
from judge.jinja2.gravatar import gravatar
from judge.models import Language, Profile, Submission
from judge.models.arena import ArenaBankProblem, ArenaGame, ArenaMatch, ArenaProblemBank, ArenaScreen, \
    ArenaTournament, SLOT_A, SLOT_B
from judge.views.arena import _ace_context, can_manage, json_error, json_response, read_json

logger = logging.getLogger('judge.arena')

SEARCH_LIMIT = 20

__all__ = ['manage_guide', 'manage_dashboard', 'manage_overview', 'manage_duel_create', 'manage_match', 'manage_match_state',
           'manage_match_action', 'manage_game_code', 'manage_submission_source', 'manage_problem_search',
           'manage_user_search', 'manage_tournament_create', 'manage_tournament', 'manage_tournament_state',
           'manage_tournament_action', 'manage_banks', 'manage_bank_action', 'manage_screen_action', 'manage_draw']


def _languages():
    return list(Language.objects.order_by('name', 'key').values('key', 'name'))


def manager_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not can_manage(request.user):
            raise Http404()
        return view(request, *args, **kwargs)
    return wrapped


def arena_errors(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except ArenaError as error:
            return json_error(error.message, error.status)
    return wrapped


def _profile(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int):
        return None
    return Profile.objects.filter(pk=value, user__is_active=True).select_related('user').first()


def _id(value, required=False):
    """An object id from a JSON body: a positive int, or None when absent. Anything else is refused."""
    if value in (None, '') and not required:
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArenaError(_('Invalid request.'))
    return value


def _slot(value):
    if value not in (SLOT_A, SLOT_B):
        raise ArenaError(_('Choose a player.'))
    return value


# ---------------------------------------------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------------------------------------------

@manager_required
@require_GET
def manage_dashboard(request):
    # A plain read: the first screen is created with the first battle or tournament, which are POSTs.
    return render(request, 'arena/manage/dashboard.html', {
        'title': _('Battle panel'),
        'state': serialize.overview(),
        'defaults': arena_rules.DEFAULTS,
        'languages': _languages(),
        'banks': list(ArenaProblemBank.objects.order_by('name').values('id', 'name')),
    })


@manager_required
@require_GET
def manage_overview(request):
    request.no_profile_update = True
    return json_response(serialize.overview())


@manager_required
@require_GET
def manage_match(request, match):
    match = get_object_or_404(ArenaMatch, pk=match)
    context = {
        'title': _('Battle: %(name)s') % {'name': str(match)},
        'state': serialize.organizer_match_state(match, request.LANGUAGE_CODE),
        'defaults': arena_rules.DEFAULTS,
        'languages': _languages(),
    }
    context.update(_ace_context())
    return render(request, 'arena/manage/match.html', context)


@manager_required
@require_GET
def manage_tournament(request, slug):
    tournament = get_object_or_404(ArenaTournament, slug=slug)
    return render(request, 'arena/manage/tournament.html', {
        'title': _('Tournament: %(name)s') % {'name': tournament.name},
        'state': serialize.tournament_state(tournament, request.LANGUAGE_CODE),
        'defaults': arena_rules.DEFAULTS,
        'languages': _languages(),
        'banks': list(ArenaProblemBank.objects.order_by('name').values('id', 'name')),
    })


@manager_required
@require_GET
def manage_guide(request):
    return render(request, 'arena/manage/guide.html', {'title': _('Guide for organizers')})


@manager_required
@require_GET
def manage_banks(request):
    banks = ArenaProblemBank.objects.order_by('name')
    selected = None
    if request.GET.get('bank', '').isdigit():
        selected = ArenaProblemBank.objects.filter(pk=int(request.GET['bank'])).first()
    entries = []
    if selected is not None:
        entries = [{'id': e.problem_id, 'code': e.problem.code, 'name': e.problem.name, 'level': e.level,
                    'note': e.note, 'public': e.problem.is_public}
                   for e in ArenaBankProblem.objects.filter(bank=selected).select_related('problem')]
    return render(request, 'arena/manage/bank.html', {
        'title': _('Problem banks'),
        'banks': banks,
        'selected': selected,
        'entries': entries,
    })


# ---------------------------------------------------------------------------------------------------------------
# Quick battles and matches
# ---------------------------------------------------------------------------------------------------------------

@manager_required
@require_POST
@arena_errors
def manage_duel_create(request):
    data = read_json(request)
    games = data.get('games')
    match = engine.create_duel(
        request.profile, _profile(data.get('player_a')), _profile(data.get('player_b')), games,
        best_of=data.get('best_of', 1), rules=data.get('rules') or {}, display_a=data.get('display_a', ''),
        display_b=data.get('display_b', ''), title=data.get('title', ''), call=data.get('call', True) is not False,
    )
    return json_response({'id': match.pk, 'url': reverse('arena_manage_match', args=[match.pk])})


@manager_required
@require_GET
def manage_match_state(request, match):
    request.no_profile_update = True
    match = get_object_or_404(ArenaMatch, pk=match)
    return json_response(serialize.organizer_match_state(match, request.LANGUAGE_CODE))


@manager_required
@require_POST
@arena_errors
def manage_match_action(request, match):
    data = read_json(request)
    action = data.get('action')
    version = data.get('version')
    actor = request.profile
    game = _id(data.get('game'))
    result = {}
    if action == 'call':
        engine.call_match(actor, match, version)
    elif action == 'start':
        outcome = engine.start_game(actor, match, game, force=bool(data.get('force')), version=version)
        if not outcome['started']:
            result['warnings'] = outcome['warnings']
    elif action == 'extend':
        engine.extend_game(actor, match, game, data.get('minutes'), version)
    elif action == 'cancel_game':
        engine.cancel_game(actor, match, game, version)
    elif action == 'declare':
        engine.declare_winner(actor, match, game, _slot(data.get('slot')), version)
    elif action == 'finalize':
        engine.finalize_verification(actor, match, game, version)
    elif action == 'walkover':
        engine.walkover(actor, match, _slot(data.get('slot')), version)
    elif action == 'set_winner':
        arena_tournament.set_winner(actor, match, _slot(data.get('slot')), version)
    elif action == 'cancel':
        engine.cancel_match(actor, match, version)
    elif action == 'problems':
        # A game by its id, or by its number in the series (it is created if it does not exist yet).
        engine.set_game_problems(actor, match, game, data.get('problems'), version, number=_id(data.get('number')))
    elif action == 'add_game':
        engine.add_game(actor, match, version)
    elif action == 'update':
        engine.update_match(actor, match, version, best_of=data.get('best_of'), rules=data.get('rules'),
                            title=data.get('title'), display_a=data.get('display_a'),
                            display_b=data.get('display_b'))
    elif action == 'replace':
        engine.replace_player(actor, match, _slot(data.get('slot')), _profile(data.get('profile')),
                              data.get('display', ''), version)
    else:
        raise ArenaError(_('Unknown action.'))
    match_obj = ArenaMatch.objects.get(pk=match)
    result['state'] = serialize.organizer_match_state(match_obj, request.LANGUAGE_CODE)
    return json_response(result)


@manager_required
@require_GET
def manage_game_code(request, game):
    request.no_profile_update = True
    game = get_object_or_404(ArenaGame, pk=game)
    since = {}
    for slot in (SLOT_A, SLOT_B):
        try:
            since[slot] = max(0, int(request.GET.get(slot, 0)))
        except (TypeError, ValueError):
            since[slot] = 0
    return json_response(serialize.code_payload(game, since, enforce_screen_rule=False))


@manager_required
@require_GET
def manage_submission_source(request, submission):
    submission = get_object_or_404(Submission.objects.select_related('language', 'source', 'contest_object'),
                                   pk=submission)
    game = ArenaGame.objects.filter(contest_id=submission.contest_object_id).first() \
        if submission.contest_object_id else None
    if game is None:
        # Only arena submissions: this is not a way around the normal submission permissions.
        raise Http404()
    return json_response({'id': submission.pk, 'language': submission.language.name,
                          'source': submission.source.source, 'status': submission.status,
                          'result': submission.result, 'error': submission.error or ''})


# ---------------------------------------------------------------------------------------------------------------
# Searches
# ---------------------------------------------------------------------------------------------------------------

@manager_required
@require_GET
def manage_problem_search(request):
    request.no_profile_update = True
    term = request.GET.get('term', '').strip()[:100]
    problems = engine.visible_problems(request.user)
    if term:
        problems = problems.filter(Q(code__icontains=term) | Q(name__icontains=term))
    problems = problems.order_by('code')[:SEARCH_LIMIT]
    players = []
    for slot in (SLOT_A, SLOT_B):
        players.append((slot, _profile(request.GET.get(slot))))
    used = set()
    match = None
    if request.GET.get('match', '').isdigit():
        match = ArenaMatch.objects.filter(pk=int(request.GET['match'])).first()
        if match is not None and match.tournament_id:
            used = engine.problems_used_in_tournament(match.tournament)
    results = []
    for problem in problems:
        warnings = engine.problem_warnings(problem, players)
        if problem.id in used:
            warnings.append('used')
        results.append({'id': problem.id, 'code': problem.code, 'name': problem.name, 'points': problem.points,
                        'warnings': warnings})
    return json_response({'results': results})


@manager_required
@require_GET
def manage_user_search(request):
    request.no_profile_update = True
    term = request.GET.get('term', '').strip()[:100]
    if not term:
        return json_response({'results': []})
    profiles = Profile.objects.filter(user__is_active=True) \
        .filter(Q(user__username__icontains=term) | Q(username_display_override__icontains=term)) \
        .select_related('user').order_by('user__username')[:SEARCH_LIMIT]
    return json_response({'results': [{
        'id': profile.pk, 'username': profile.username, 'name': profile.display_name,
        'avatar': gravatar(profile, 48), 'rating': profile.rating,
        'privileged': bool(profile.user.is_staff or profile.user.is_superuser),
        'busy': engine._busy_elsewhere(profile.pk),
    } for profile in profiles]})


# ---------------------------------------------------------------------------------------------------------------
# Tournaments
# ---------------------------------------------------------------------------------------------------------------

def _bank(value):
    if value in (None, '', 0):
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArenaError(_('Invalid problem bank.'))
    bank = ArenaProblemBank.objects.filter(pk=value).first()
    if bank is None:
        raise ArenaError(_('That bank does not exist.'))
    return bank


def _profiles(values):
    if not isinstance(values, list) or len(values) > arena_tournament.MAX_PARTICIPANTS * 2:
        raise ArenaError(_('Invalid list of participants.'))
    profiles = []
    for value in values:
        profile = _profile(value)
        if profile is None:
            raise ArenaError(_('One of the participants does not exist.'))
        profiles.append(profile)
    return profiles


@manager_required
@require_POST
@arena_errors
def manage_tournament_create(request):
    data = read_json(request)
    tournament = arena_tournament.create_tournament(
        request.profile, data.get('name'), _profiles(data.get('participants') or []),
        seeding=data.get('seeding', ArenaTournament.SEED_MANUAL), third_place=bool(data.get('third_place')),
        rules=data.get('rules') or {}, description=data.get('description', ''), bank=_bank(data.get('bank')),
    )
    return json_response({'slug': tournament.slug,
                          'url': reverse('arena_manage_tournament', args=[tournament.slug])})


@manager_required
@require_GET
def manage_tournament_state(request, slug):
    request.no_profile_update = True
    tournament = get_object_or_404(ArenaTournament, slug=slug)
    return json_response(serialize.tournament_state(tournament, request.LANGUAGE_CODE))


@manager_required
@require_POST
@arena_errors
def manage_tournament_action(request, slug):
    tournament = get_object_or_404(ArenaTournament, slug=slug)
    data = read_json(request)
    action = data.get('action')
    version = data.get('version')
    actor = request.profile
    result = {}
    if action == 'update':
        arena_tournament.update_tournament(
            actor, tournament.pk, version, name=data.get('name'), description=data.get('description'),
            rules=data.get('rules'), bank=_bank(data.get('bank')) if 'bank' in data else False,
            third_place=data.get('third_place'), draw=data.get('draw'))
    elif action == 'participants':
        arena_tournament.set_participants(actor, tournament.pk, _profiles(data.get('participants') or []),
                                          seeding=data.get('seeding'), version=version)
    elif action == 'round':
        number = data.get('number')
        if isinstance(number, bool) or not isinstance(number, int):
            raise ArenaError(_('That round does not exist.'), status=404)
        arena_tournament.set_round(actor, tournament.pk, number, best_of=data.get('best_of'),
                                   rules=data.get('rules'), name=data.get('name'), version=version,
                                   draw=data.get('draw'))
    elif action == 'generate':
        arena_tournament.generate_bracket(actor, tournament.pk, version)
    elif action == 'start':
        arena_tournament.start_tournament(actor, tournament.pk, version)
    elif action == 'archive':
        arena_tournament.archive_tournament(actor, tournament.pk, version)
    elif action == 'fill':
        result['filled'] = arena_tournament.fill_ready_matches(actor, tournament.pk)
    else:
        raise ArenaError(_('Unknown action.'))
    tournament.refresh_from_db()
    result['state'] = serialize.tournament_state(tournament, request.LANGUAGE_CODE)
    return json_response(result)


@manager_required
@require_POST
@arena_errors
def manage_draw(request, match):
    """Suggest problems for one game from the tournament bank, with the count and difficulties of its round.
    Nothing is saved: the suggestion goes into the list, and the organizer saves it or changes it."""
    data = read_json(request)
    match_obj = get_object_or_404(ArenaMatch.objects.select_related('tournament', 'round'), pk=match)
    game_id, number = _id(data.get('game')), _id(data.get('number'))
    games = ArenaGame.objects.filter(match=match_obj)
    game = games.filter(pk=game_id).first() if game_id else (games.filter(number=number).first() if number else None)
    rules = arena_rules.match_rules(match_obj)
    settings = arena_tournament.draw_settings(match_obj)
    problems, warnings = arena_tournament.draw_problems(
        request.profile, match_obj, game, settings['count'], level_min=settings['level_min'],
        level_max=settings['level_max'])
    players = [(slot, match_obj.player(slot)) for slot in (SLOT_A, SLOT_B)]
    return json_response({'problems': [{'id': p.id, 'code': p.code, 'name': p.name,
                                        'points': arena_rules.DEFAULT_POINTS,
                                        'warnings': engine.problem_warnings(p, players)} for p in problems],
                          'warnings': warnings, 'partial': rules['partial']})


# ---------------------------------------------------------------------------------------------------------------
# Banks and screens
# ---------------------------------------------------------------------------------------------------------------

@manager_required
@require_POST
@arena_errors
def manage_bank_action(request):
    data = read_json(request)
    action = data.get('action')
    if action == 'create':
        bank = arena_tournament.create_bank(request.profile, data.get('name'), data.get('description', ''))
    elif action == 'problems':
        bank = _bank(data.get('bank'))
        if bank is None:
            raise ArenaError(_('That bank does not exist.'))
        bank = arena_tournament.set_bank_problems(request.profile, bank.pk, data.get('problems'))
    else:
        raise ArenaError(_('Unknown action.'))
    return json_response({'id': bank.pk, 'url': reverse('arena_manage_banks') + '?bank=%d' % bank.pk})


def _match_arg(value):
    value = _id(value)
    if value is None:
        return None
    match = ArenaMatch.objects.filter(pk=value).first()
    if match is None:
        raise ArenaError(_('That battle does not exist.'), status=404)
    return match


def _tournament_arg(value):
    value = _id(value)
    if value is None:
        return None
    tournament = ArenaTournament.objects.filter(pk=value).first()
    if tournament is None:
        raise ArenaError(_('That tournament does not exist.'), status=404)
    return tournament


@manager_required
@require_POST
@arena_errors
def manage_screen_action(request):
    data = read_json(request)
    action = data.get('action')
    actor = request.profile
    if action == 'create':
        engine.create_screen(actor, data.get('name'))
    elif action == 'show':
        engine.show_on_screen(actor, _id(data.get('screen'), True), data.get('mode'), match=_match_arg(data.get('match')),
                              tournament=_tournament_arg(data.get('tournament')), message=data.get('message', ''),
                              follow_live=data.get('follow_live'), version=data.get('version'))
    elif action == 'rename':
        engine.rename_screen(actor, _id(data.get('screen'), True), data.get('name'))
    elif action == 'regenerate':
        engine.regenerate_screen_token(actor, _id(data.get('screen'), True))
    else:
        raise ArenaError(_('Unknown action.'))
    return json_response({'screens': serialize.screens_overview()})

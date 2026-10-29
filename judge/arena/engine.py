"""Every change to the arena goes through here, inside a transaction holding the row lock of the match.

The lock order is fixed: the tournament of the match, if it has one (lock_match takes it first, 2026-10-28), then
the match, then its games, then (when a result moves a player forward) the next matches of the bracket, which always
belong to a later round, so two locks can never wait on each other.

`tick()` is how games move on by themselves. Nothing runs on a timer: every page that shows a game asks for its
state every second or two, and asking runs `tick()`, which works the outcome out from the submissions (see
scoring.py) and records it only if it changed. Asking twice, or from many pages at once, records it once.
"""
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Max, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from judge.arena import access, rules as arena_rules, scoring
from judge.models import Contest, ContestParticipation, ContestProblem, ContestSubmission, Judge, Problem, \
    Solution, Submission, SubmissionSource
from judge.models.arena import ArenaAction, ArenaGame, ArenaGameProblem, ArenaMatch, ArenaScreen, ArenaTournament, \
    MAX_DISPLAY_NAME, MAX_PROBLEMS_PER_GAME, RANKING, RANKING_PENDING, SLOT_A, SLOT_B

logger = logging.getLogger('judge.arena')

LOCK_KEY = 'arena:locked'
LOCK_TIMEOUT = 6 * 3600
SEEN_TIMEOUT = 30
CONNECTED_SECONDS = 8
MAX_TOTAL_MINUTES = 300
SUBMISSION_NONCE_SECONDS = 120


def arena_setting(name, default, low=0, high=10 ** 6):
    """A whole number from the settings (the private file may set CPC_ARENA_*), or `default` if it is absent or
    makes no sense, so a typo there can never break the arena."""
    value = getattr(settings, name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        return default
    return value


def lock_idle_minutes():
    """After this long without anything happening in a battle (nobody called, no game started or finished), its
    players are no longer sent back to the arena: a battle left open by mistake cannot lock anybody out for good."""
    return arena_setting('CPC_ARENA_LOCK_IDLE_MINUTES', 60, low=5, high=24 * 60)


class ArenaError(Exception):
    """A refusal meant for the person who asked, already translated."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class Conflict(ArenaError):
    def __init__(self, message=None):
        super().__init__(message or _('Somebody else changed this battle a moment ago. The page has been updated; '
                                      'check it and try again.'), status=409)


def other(slot):
    return SLOT_B if slot == SLOT_A else SLOT_A


def _log(action, actor=None, match=None, game=None, tournament=None, **data):
    ArenaAction.objects.create(action=action, actor=actor, match=match, game=game,
                               tournament=tournament or (match.tournament if match is not None else None),
                               data=data)


def _check_version(obj, version):
    if version is None or version == '':
        return
    try:
        version = int(version)
    except (TypeError, ValueError):
        raise Conflict()
    if version != obj.version:
        raise Conflict()


def _bump(match, *fields, system=False):
    """Save `fields` of a match. An organizer's change also moves its version, so another organizer acting on what
    they saw before gets a conflict; what the game does by itself (going live, ending) does not, and is guarded by
    the state checks of every action instead."""
    if system:
        if fields:
            match.save(update_fields=list(fields))
        return
    match.version += 1
    match.save(update_fields=list(fields) + ['version'])


def lock_match(match_id):
    """The match, locked. A match of a tournament takes the lock of its tournament first (duelos-nucleo-5,
    2026-10-28): a change of the tournament (a round, a corrected winner) locks the tournament and then its matches,
    and a game that decides a match locked the match, then the next one, then the tournament, to see whether it had
    finished. Two of them at once waited on each other until MariaDB killed one (a 500). The plain read of the
    tournament before taking its lock assumes READ COMMITTED, the isolation of Django on MariaDB (and of the site,
    which does not change it; the deployer checks it): under REPEATABLE READ it would fix the snapshot before waiting
    for the lock, and the plain reads after it would see the data from before."""
    tournament_id = ArenaMatch.objects.filter(pk=match_id).values_list('tournament_id', flat=True).first()
    if tournament_id is not None:
        list(ArenaTournament.objects.select_for_update().filter(pk=tournament_id).values_list('pk', flat=True))
    try:
        return ArenaMatch.objects.select_for_update().select_related('tournament', 'round').get(pk=match_id)
    except ArenaMatch.DoesNotExist:
        raise ArenaError(_('That battle does not exist.'), status=404)


# ---------------------------------------------------------------------------------------------------------------
# Who is locked inside the arena
# ---------------------------------------------------------------------------------------------------------------

def lock_until(match, games):
    """Until when the players of a called or playing match are kept inside the arena: the last thing that happened
    in it (called, a game started or finished, or the end of the game being played) plus lock_idle_minutes()."""
    moments = [match.called_at or match.created_at]
    for game in games:
        moments += [game.started_at, game.finished_at]
        if game.status in ArenaGame.ACTIVE_STATUSES:
            moments.append(game.ends_at)
    return max(moment for moment in moments if moment is not None) + timedelta(minutes=lock_idle_minutes())


def lock_entries_from_db(now=None):
    """{profile id: UNIX time until which that player is kept inside the arena}, for every player still held."""
    now = now or timezone.now()
    matches = list(ArenaMatch.objects.filter(status__in=ArenaMatch.LOCKING_STATUSES)
                   .only('id', 'player_a_id', 'player_b_id', 'called_at', 'created_at'))
    games = {}
    for game in ArenaGame.objects.filter(match__in=matches).only('match_id', 'status', 'started_at', 'finished_at',
                                                                    'ends_at'):
        games.setdefault(game.match_id, []).append(game)
    entries = {}
    for match in matches:
        until = lock_until(match, games.get(match.pk, ()))
        if until <= now:
            continue
        for pk in (match.player_a_id, match.player_b_id):
            if pk:
                entries[pk] = max(entries.get(pk, 0), round(until.timestamp(), 3))
    return entries


def locked_profile_ids_from_db():
    return set(lock_entries_from_db())


def refresh_lock():
    """Rewrite the cached players kept inside the arena from the database. Never raises."""
    try:
        cache.set(LOCK_KEY, lock_entries_from_db(), LOCK_TIMEOUT)
    except Exception:
        logger.warning('Arena lock set could not be written; the rest of the site will not send players back.')


def refresh_lock_on_commit():
    transaction.on_commit(refresh_lock)


def ensure_lock():
    """Rebuild the cached set if it is gone, for instance after the cache restarted."""
    try:
        if cache.get(LOCK_KEY) is None:
            refresh_lock()
    except Exception:
        pass


def active_match_for(profile_id):
    return ArenaMatch.objects.filter(status__in=ArenaMatch.LOCKING_STATUSES) \
        .filter(Q(player_a_id=profile_id) | Q(player_b_id=profile_id)) \
        .order_by('-called_at', '-id').first()


def is_live_arena_player(profile):
    """Whether this profile is playing a game right now. Read from the database, not from the cache.

    Only until the game's end time: a game nobody looked at after its time ran out is still recorded as live
    (nothing moves on by itself until a page asks), and that must not keep its players from submitting elsewhere.
    """
    if profile is None:
        return False
    return ArenaGame.objects.filter(status__in=ArenaGame.OPEN_STATUSES, ends_at__gt=timezone.now()) \
        .filter(Q(match__player_a=profile) | Q(match__player_b=profile)).exists()


def _busy_elsewhere(profile_id, match_id=None):
    matches = ArenaMatch.objects.filter(status__in=ArenaMatch.LOCKING_STATUSES) \
        .filter(Q(player_a_id=profile_id) | Q(player_b_id=profile_id))
    if match_id is not None:
        matches = matches.exclude(pk=match_id)
    return matches.exists()


def busy_profile_ids(profile_ids):
    """Which of `profile_ids` are in a battle (called or being played), in one query: _busy_elsewhere for a list."""
    wanted = set(profile_ids)
    pairs = ArenaMatch.objects.filter(status__in=ArenaMatch.LOCKING_STATUSES) \
        .filter(Q(player_a_id__in=wanted) | Q(player_b_id__in=wanted)).values_list('player_a_id', 'player_b_id')
    return {pk for pair in pairs for pk in pair if pk in wanted}


# ---------------------------------------------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------------------------------------------

def mark_seen(match_id, slot):
    try:
        cache.set('arena:seen:%d:%s' % (match_id, slot), timezone.now().timestamp(), SEEN_TIMEOUT)
    except Exception:
        pass


def last_seen(match_id, slot):
    try:
        return cache.get('arena:seen:%d:%s' % (match_id, slot))
    except Exception:
        return None


def is_connected(match_id, slot, now=None):
    seen = last_seen(match_id, slot)
    if seen is None:
        return False
    now = now or timezone.now()
    return now.timestamp() - seen <= CONNECTED_SECONDS


# ---------------------------------------------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------------------------------------------

def visible_problems(user):
    # Custom tests are throwaway problems of their own; they never belong in a game.
    return Problem.get_visible_problems(user).exclude(code__startswith='ct_')


def _clean_problem_list(actor, entries, used_elsewhere=(), kept=()):
    """[(problem id, points)] → [(Problem, points)], refusing anything the organizer cannot see, except the problems
    in `kept`, which the game already has (duelos-web-4, 2026-10-28): every organizer of a battle sees its problems,
    so a co-organizer who cannot see one that another chose (or that the bank gave) still saves the list with it."""
    if not isinstance(entries, (list, tuple)):
        raise ArenaError(_('Invalid list of problems.'))
    if len(entries) > MAX_PROBLEMS_PER_GAME:
        raise ArenaError(_('A game can have at most %(count)s problems.') % {'count': MAX_PROBLEMS_PER_GAME})
    ids, points = [], []
    for entry in entries:
        if isinstance(entry, dict):
            problem_id, value = entry.get('id'), entry.get('points', arena_rules.DEFAULT_POINTS)
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            problem_id, value = entry
        else:
            raise ArenaError(_('Invalid list of problems.'))
        if isinstance(problem_id, bool) or not isinstance(problem_id, int):
            # isdecimal, not isdigit (2026-10-28): a superscript digit passed isdigit and int() failed with a 500.
            if isinstance(problem_id, str) and problem_id.isdecimal():
                problem_id = int(problem_id)
            else:
                raise ArenaError(_('Invalid list of problems.'))
        try:
            value = arena_rules.clean_points(value)
        except ValidationError as error:
            raise ArenaError(error.messages[0])
        ids.append(problem_id)
        points.append(value)
    if len(set(ids)) != len(ids):
        raise ArenaError(_('The same problem cannot appear twice in a game.'))
    repeated = set(ids) & set(used_elsewhere)
    if repeated:
        raise ArenaError(_('Each game of a battle must use different problems.'))
    found = {problem.id: problem for problem in visible_problems(actor.user).filter(id__in=ids)}
    hidden = (set(ids) & set(kept)) - set(found)
    if hidden:
        found.update((problem.id, problem) for problem in Problem.objects.filter(id__in=hidden))
    if len(found) != len(ids):
        raise ArenaError(_('One of the problems does not exist or you cannot see it.'))
    return [(found[problem_id], value) for problem_id, value in zip(ids, points)]


def _set_problems(game, problems):
    ArenaGameProblem.objects.filter(game=game).delete()
    ArenaGameProblem.objects.bulk_create([
        ArenaGameProblem(game=game, problem=problem, order=index, points=value)
        for index, (problem, value) in enumerate(problems)
    ])


def voided_before_start(game):
    """A game voided during its countdown (by voiding it, by a walkover or by cancelling the battle): nobody ever saw
    its problems, and nobody sees them later (duelos-nucleo-2, 2026-10-28). Until then such a game showed its
    statements to the players and to the projector as soon as its start time came."""
    return game is not None and game.status == ArenaGame.CANCELLED and game.started_at is not None and \
        game.finished_at is not None and game.finished_at < game.started_at


# The games whose problems nobody saw: only those can give their problems again. A game voided once it had started
# (the guide says to void it and choose other problems) showed them to the players and to the room.
UNSEEN_GAMES = Q(status=ArenaGame.CANCELLED, finished_at__lt=F('started_at'))


def _problems_used_in_match(match, exclude_game=None):
    games = ArenaGame.objects.filter(match=match).exclude(UNSEEN_GAMES)
    if exclude_game is not None:
        games = games.exclude(pk=exclude_game.pk)
    return set(ArenaGameProblem.objects.filter(game__in=games).values_list('problem_id', flat=True))


def problems_used_in_tournament(tournament, exclude_game=None):
    if tournament is None:
        return set()
    games = ArenaGame.objects.filter(match__tournament=tournament).exclude(UNSEEN_GAMES)
    if exclude_game is not None:
        games = games.exclude(pk=exclude_game.pk)
    return set(ArenaGameProblem.objects.filter(game__in=games).values_list('problem_id', flat=True))


def game_languages(game_problems, rules):
    """Languages allowed on each problem: what the problem allows, narrowed by the rules if they list any."""
    wanted = set(rules.get('languages') or ())
    result = {}
    for gp in game_problems:
        # Like the normal submit page: what an online judge can run. If no judge is online right now, what the
        # problem allows, so the page still works and the submission waits in the queue.
        languages = list(gp.problem.usable_languages.order_by('name', 'key')) or \
            list(gp.problem.allowed_languages.all().order_by('name', 'key'))
        if wanted:
            languages = [language for language in languages if language.key in wanted]
        result[gp.order] = languages
    return result


def cached_game_languages(game, game_problems, rules):
    """game_languages() as plain dictionaries, kept a minute in the cache: every player asks every second."""
    key = 'arena:langs:%d:%s' % (game.pk, ','.join(sorted(rules.get('languages') or ())))
    try:
        found = cache.get(key)
    except Exception:
        found = None
    if found is not None:
        return {int(order): rows for order, rows in found.items()}
    result = {order: [{'key': lang.key, 'name': lang.name, 'ace': lang.ace, 'id': lang.pk} for lang in languages]
              for order, languages in game_languages(game_problems, rules).items()}
    try:
        cache.set(key, {str(order): rows for order, rows in result.items()}, 60)
    except Exception:
        pass
    return result


def problem_warnings(problem, players=()):
    """Plain facts an organizer should know before picking a problem. Never a refusal."""
    warnings = []
    if problem.is_public:
        warnings.append('public')
    if Solution.objects.filter(problem=problem, is_public=True).exists():
        warnings.append('editorial')
    if not Judge.objects.filter(online=True, problems=problem).exists():
        warnings.append('no_judge')
    for slot, profile in players:
        if profile is None:
            continue
        attempts = Submission.objects.filter(user=profile, problem=problem)
        if attempts.filter(result='AC', case_points__gte=F('case_total')).exists():
            warnings.append('solved_' + slot)
        elif attempts.exists():
            warnings.append('tried_' + slot)
    return warnings


# ---------------------------------------------------------------------------------------------------------------
# Creating battles
# ---------------------------------------------------------------------------------------------------------------

def _clean_display(value, allowed):
    value = (value or '').strip()
    if not value:
        return ''
    if not allowed:
        raise ArenaError(_('Display names are only for quick battles; tournament players use their own accounts.'))
    if len(value) > MAX_DISPLAY_NAME:
        raise ArenaError(_('A display name can have at most %(count)s characters.') % {'count': MAX_DISPLAY_NAME})
    return value


def _refuse_self(actor, *players):
    """Whoever runs a battle does not play it (duelos-web-1, 2026-10-28): its panel is closed to its players until
    it is decided (access.can_manage_match), so a battle they put themselves in would be left with nobody to run it.
    A tournament may have an organizer among its participants, put there by another of its organizers
    (tournament._refuse_self); the others run it while they play it."""
    if actor is not None and any(player is not None and player.pk == actor.pk for player in players):
        raise ArenaError(_('You cannot play in a battle you organize. Another organizer has to set it up and run '
                           'it.'))


def clean_ranking(value):
    """A decision about the ranking of the duels (judge/arena/rating.py); whoever may take it is checked by the
    page that asks."""
    if value not in dict(RANKING):
        raise ArenaError(_('Invalid decision.'))
    return value


@transaction.atomic
def create_duel(actor, player_a, player_b, games, best_of=1, rules=None, display_a='', display_b='', title='',
                call=True, ranking=RANKING_PENDING):
    """A quick battle: a match without a tournament, its first game, and any later games prepared in advance.
    `ranking`: whether it counts for the ranking of the duels, if whoever creates it may decide that."""
    if player_a is None or player_b is None:
        raise ArenaError(_('Choose both players.'))
    ranking = clean_ranking(ranking)
    if player_a.pk == player_b.pk:
        raise ArenaError(_('A player cannot face themselves.'))
    _refuse_self(actor, player_a, player_b)
    try:
        best_of = arena_rules.clean_best_of(best_of)
        rules = arena_rules.clean_rules(rules)
    except ValidationError as error:
        raise ArenaError(error.messages[0])
    display_a = _clean_display(display_a, True)
    display_b = _clean_display(display_b, True)
    title = (title or '').strip()[:100]
    for profile in (player_a, player_b):
        if _busy_elsewhere(profile.pk):
            raise ArenaError(_('%(name)s is already in another battle.') % {'name': profile.display_name})
    if not isinstance(games, (list, tuple)) or not games:
        raise ArenaError(_('Choose the problems of the first game.'))
    if len(games) > best_of:
        raise ArenaError(_('You can prepare at most as many games as the series has.'))
    used, cleaned = set(), []
    for entries in games:
        problems = _clean_problem_list(actor, entries, used)
        used.update(problem.id for problem, _points in problems)
        cleaned.append(problems)
    if not cleaned[0]:
        raise ArenaError(_('Choose the problems of the first game.'))

    now = timezone.now()
    match = ArenaMatch.objects.create(
        player_a=player_a, player_b=player_b, display_name_a=display_a, display_name_b=display_b, title=title,
        best_of=best_of, rules=rules, status=ArenaMatch.CALLED if call else ArenaMatch.READY,
        called_at=now if call else None, created_by=actor, ranking=ranking,
    )
    for number, problems in enumerate(cleaned, 1):
        game = ArenaGame.objects.create(match=match, number=number)
        _set_problems(game, problems)
    _log('create', actor, match, best_of=best_of, games=len(cleaned), ranking=ranking)
    ensure_default_screen(actor)
    screen = _free_screen(actor)
    if screen is not None:
        # The projector moves on to the new battle by itself, unless it is busy with another one still going.
        screen.mode, screen.match = ArenaScreen.MATCH, match
        screen.message = ''
        screen.version += 1
        screen.save(update_fields=['mode', 'match', 'message', 'version', 'updated_at'])
        _log('screen', actor, match, screen=screen.pk)
    refresh_lock_on_commit()
    return match


def _free_screen(actor=None):
    """The first screen with nothing to show for now: waiting, or still showing a battle that is over. Screens on
    a bracket or a message, and screens with a battle still going, are left alone. With an `actor`, only among the
    screens they manage: a new battle never takes the projector of another organizer."""
    screens = access.screens_of_actor(actor)
    # Only the screen rows are locked; the status of the battles they show is a plain read.
    screens = list(screens.select_for_update().order_by('name', 'id'))
    statuses = dict(ArenaMatch.objects.filter(pk__in=[s.match_id for s in screens if s.match_id])
                    .values_list('pk', 'status'))
    for screen in screens:
        if screen.mode == ArenaScreen.IDLE:
            return screen
        if screen.mode == ArenaScreen.MATCH and statuses.get(screen.match_id, ArenaMatch.DECIDED) in \
                (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
            return screen
    return None


def _require_running_tournament(match):
    if match.tournament_id and match.tournament.status != 'running':
        raise ArenaError(_('Start the tournament before calling its matches.'))


@transaction.atomic
def call_match(actor, match_id, version=None):
    match = lock_match(match_id)
    _check_version(match, version)
    if match.status in (ArenaMatch.CALLED, ArenaMatch.PLAYING):
        return match
    if match.status != ArenaMatch.READY or not match.player_a_id or not match.player_b_id:
        raise ArenaError(_('This battle cannot be called yet.'))
    _require_running_tournament(match)
    for slot in (SLOT_A, SLOT_B):
        if _busy_elsewhere(match.player_id(slot), match.pk):
            raise ArenaError(_('%(name)s is already in another battle.') % {'name': match.shown_name(slot)})
    match.status = ArenaMatch.CALLED
    match.called_at = timezone.now()
    _bump(match, 'status', 'called_at')
    _from_bank(match, _ensure_next_game(match), actor)
    _log('call', actor, match)
    refresh_lock_on_commit()
    return match


def _from_bank(match, game, actor=None):
    """A tournament that gives problems from its bank fills the game about to be played, if nobody chose them."""
    from judge.arena import tournament as arena_tournament
    return arena_tournament.fill_from_bank(match, game, actor)


def _current_prepared_game(match):
    return ArenaGame.objects.filter(match=match, status=ArenaGame.PREPARED).order_by('number').first()


def _ensure_next_game(match):
    game = _current_prepared_game(match)
    if game is not None:
        return game
    number = (ArenaGame.objects.filter(match=match).aggregate(n=Max('number'))['n'] or 0) + 1
    return ArenaGame.objects.create(match=match, number=number)


def max_game_number(match):
    """How many games the series can still need: its length, plus one more for every tie or void so far."""
    extra = ArenaGame.objects.filter(match=match, status__in=(ArenaGame.TIE, ArenaGame.CANCELLED)).count()
    return match.best_of + extra


def _game_number(match, number):
    """Game `number` of the series, creating it (and any before it) empty if it does not exist yet."""
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= max_game_number(match):
        raise ArenaError(_('That game does not exist.'), status=404)
    games = {game.number: game for game in ArenaGame.objects.filter(match=match)}
    if number not in games:
        for missing in range(max(games, default=0) + 1, number + 1):
            games[missing] = ArenaGame.objects.create(match=match, number=missing)
    return games[number]


@transaction.atomic
def set_game_problems(actor, match_id, game_id, entries, version=None, number=None):
    """The problems of one game. Allowed from the moment the match exists, even before its players are known, so
    a tournament can be prepared in advance: harder problems for a match between strong players, for instance."""
    match = lock_match(match_id)
    _check_version(match, version)
    if match.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        raise ArenaError(_('This battle is over.'))
    # By id, by its number in the series, or else the next game to play, which is created if needed.
    if game_id:
        game = ArenaGame.objects.filter(pk=game_id, match=match).first()
    elif number is not None:
        game = _game_number(match, number)
    else:
        game = _ensure_next_game(match)
    if game is None:
        raise ArenaError(_('That game does not exist.'), status=404)
    if game.status != ArenaGame.PREPARED:
        raise ArenaError(_('The problems of a game cannot change once it has started.'))
    present = ArenaGameProblem.objects.filter(game=game).values_list('problem_id', flat=True)
    problems = _clean_problem_list(actor, entries, _problems_used_in_match(match, exclude_game=game), kept=present)
    _set_problems(game, problems)
    _bump(match)
    _log('problems', actor, match, game, problems=[problem.code for problem, _points in problems])
    return game


@transaction.atomic
def add_game(actor, match_id, version=None):
    """A game for later: the next of the series, or a tiebreak."""
    match = lock_match(match_id)
    _check_version(match, version)
    if match.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        raise ArenaError(_('This battle is over.'))
    game = _ensure_next_game(match)
    _bump(match)
    return game


# ---------------------------------------------------------------------------------------------------------------
# Starting a game
# ---------------------------------------------------------------------------------------------------------------

def _contest_key(game):
    key = 'arena%d' % game.pk
    while Contest.objects.filter(key=key).exists():
        key = ('arena%dx%s' % (game.pk, secrets.token_hex(3)))[:20]
    return key


def start_warnings(match, game, now=None):
    now = now or timezone.now()
    warnings = []
    for slot in (SLOT_A, SLOT_B):
        if not is_connected(match.pk, slot, now):
            warnings.append({'code': 'absent', 'slot': slot,
                             'message': _('%(name)s does not have the arena open.') %
                             {'name': match.shown_name(slot)}})
    for gp in ArenaGameProblem.objects.filter(game=game).select_related('problem'):
        if not Judge.objects.filter(online=True, problems=gp.problem).exists():
            warnings.append({'code': 'no_judge', 'problem': gp.problem.code,
                             'message': _('No judge is online for %(problem)s.') % {'problem': gp.problem.name}})
    return warnings


@transaction.atomic
def start_game(actor, match_id, game_id=None, force=False, version=None):
    """Start the countdown of the next game. Without `force`, return the warnings instead if there are any."""
    match = lock_match(match_id)
    _check_version(match, version)
    if match.status not in (ArenaMatch.READY, ArenaMatch.CALLED, ArenaMatch.PLAYING):
        raise ArenaError(_('This battle cannot start a game now.'))
    if not match.player_a_id or not match.player_b_id:
        raise ArenaError(_('Both players are needed to start.'))
    _require_running_tournament(match)
    if ArenaGame.objects.filter(match=match, status__in=ArenaGame.ACTIVE_STATUSES).exists():
        raise ArenaError(_('A game of this battle is already being played.'))
    game = ArenaGame.objects.filter(match=match, status=ArenaGame.PREPARED)
    game = (game.filter(pk=game_id) if game_id else game.order_by('number')).first()
    if game is None:
        raise ArenaError(_('There is no game ready to start.'))
    for slot in (SLOT_A, SLOT_B):
        if _busy_elsewhere(match.player_id(slot), match.pk):
            raise ArenaError(_('%(name)s is already in another battle.') % {'name': match.shown_name(slot)})
    game_problems = list(ArenaGameProblem.objects.filter(game=game).select_related('problem').order_by('order'))
    if not game_problems and _from_bank(match, game, actor):
        game_problems = list(ArenaGameProblem.objects.filter(game=game).select_related('problem').order_by('order'))
    if not game_problems:
        raise ArenaError(_('Choose the problems of this game before starting it.'))
    rules = arena_rules.match_rules(match)
    languages = game_languages(game_problems, rules)
    for gp in game_problems:
        if not languages[gp.order]:
            raise ArenaError(_('%(problem)s has no language allowed by the rules of this battle.') %
                             {'problem': gp.problem.name})
    now = timezone.now()
    if not force:
        warnings = start_warnings(match, game, now)
        if warnings:
            return {'started': False, 'warnings': warnings}

    started_at = now + timedelta(seconds=rules['countdown'])
    ends_at = started_at + timedelta(minutes=rules['duration'])
    names = (match.shown_name(SLOT_A), match.shown_name(SLOT_B))
    contest = Contest.objects.create(
        key=_contest_key(game),
        # «Duelo» since 2026-10-19 (older ones say «Arena»); the key and the summary are what identify them.
        name=('Duelo: %s vs %s (%d)' % (names[0], names[1], game.number))[:100],
        start_time=started_at, end_time=ends_at,
        is_visible=False, is_rated=False, scoreboard_visibility=Contest.SCOREBOARD_HIDDEN,
        format_name='default', participation_mode=Contest.INDIVIDUAL,
        summary='Arena game %d of match %d' % (game.number, match.pk),
    )
    for gp in game_problems:
        gp.contest_problem = ContestProblem.objects.create(
            problem=gp.problem, contest=contest, points=gp.points, partial=rules['partial'], is_pretested=False,
            order=gp.order,
        )
        gp.save(update_fields=['contest_problem'])
    participations = {}
    for slot in (SLOT_A, SLOT_B):
        participations[slot] = ContestParticipation.objects.create(
            contest=contest, user_id=match.player_id(slot), real_start=started_at, virtual=ContestParticipation.LIVE,
        )
    game.contest = contest
    game.participation_a = participations[SLOT_A]
    game.participation_b = participations[SLOT_B]
    game.rules = rules
    game.started_at = started_at
    game.ends_at = ends_at
    game.status = ArenaGame.COUNTDOWN
    game.save()
    if match.status != ArenaMatch.PLAYING:
        match.status = ArenaMatch.PLAYING
        match.called_at = match.called_at or now
    _bump(match, 'status', 'called_at')
    _log('start', actor, match, game, forced=bool(force), problems=[gp.problem.code for gp in game_problems])
    refresh_lock_on_commit()
    return {'started': True, 'game': game}


# ---------------------------------------------------------------------------------------------------------------
# Working out the state of a game
# ---------------------------------------------------------------------------------------------------------------

def game_problem_rows(game):
    return list(ArenaGameProblem.objects.filter(game=game).select_related('problem').order_by('order'))


def load_attempts(game, game_problems=None):
    """The `Attempt`s of each player of a started game, keyed by slot. Problems are keyed by their order."""
    if game.contest_id is None:
        return {SLOT_A: [], SLOT_B: []}
    game_problems = game_problems if game_problems is not None else game_problem_rows(game)
    by_contest_problem = {gp.contest_problem_id: gp.order for gp in game_problems if gp.contest_problem_id}
    slots = {game.participation_a_id: SLOT_A, game.participation_b_id: SLOT_B}
    rows = Submission.objects.filter(contest_object_id=game.contest_id,
                                     contest__participation_id__in=list(slots)) \
        .values('id', 'date', 'status', 'result', 'case_points', 'case_total', 'contest__participation_id',
                'contest__problem_id').order_by('date', 'id')
    result = {SLOT_A: [], SLOT_B: []}
    for row in rows:
        slot = slots.get(row['contest__participation_id'])
        if slot is None:
            continue
        result[slot].extend(scoring.attempts_from_rows([row], lambda r: by_contest_problem.get(r['contest__problem_id'])))
    return result


def problem_points(game_problems):
    return {gp.order: gp.points for gp in game_problems}


def evaluate(game, now, game_problems=None, attempts=None):
    if game.status not in ArenaGame.ACTIVE_STATUSES or game.started_at is None:
        return None
    game_problems = game_problems if game_problems is not None else game_problem_rows(game)
    attempts = attempts if attempts is not None else load_attempts(game, game_problems)
    return scoring.decide(attempts[SLOT_A], attempts[SLOT_B], problem_points(game_problems), game.rules,
                          game.started_at, game.ends_at, now, closed=game.closed_at is not None)


def standings(game, game_problems=None, attempts=None):
    """Confirmed standings of both players, keyed by slot."""
    game_problems = game_problems if game_problems is not None else game_problem_rows(game)
    attempts = attempts if attempts is not None else load_attempts(game, game_problems)
    rules = game.rules or {}
    return {slot: scoring.confirmed(attempts[slot], problem_points(game_problems), bool(rules.get('partial')),
                                    int(rules.get('penalty') or 0), game.started_at)
            for slot in (SLOT_A, SLOT_B)}


def _result_snapshot(game, game_problems, attempts):
    order = [gp.order for gp in game_problems]
    return {slot: scoring.summary(row, game.started_at, order)
            for slot, row in standings(game, game_problems, attempts).items()}


def _close_contest(game, moment):
    if game.contest_id:
        Contest.objects.filter(pk=game.contest_id, end_time__gt=moment).update(end_time=moment)


def _expected(decision, game):
    outcome = decision.outcome
    if outcome == scoring.COUNTDOWN:
        return ArenaGame.COUNTDOWN
    if outcome == scoring.LIVE:
        return ArenaGame.LIVE
    if outcome == scoring.VERIFYING:
        return ArenaGame.VERIFYING
    return None


def _tick_locked(match, game, now):
    """Same as tick() for a game whose match is already locked by the caller."""
    if game.status in ArenaGame.ACTIVE_STATUSES and game.started_at is not None:
        game_problems = game_problem_rows(game)
        attempts = load_attempts(game, game_problems)
        decision = evaluate(game, now, game_problems, attempts)
        expected = _expected(decision, game)
        if expected is None or expected != game.status:
            _apply(match, game, decision, now, game_problems, attempts)
    return game


def tick(game, now=None):
    """Bring a game up to date and return it. Cheap when nothing changed: no lock is taken."""
    if game is None or game.status not in ArenaGame.ACTIVE_STATUSES:
        return game
    now = now or timezone.now()
    decision = evaluate(game, now)
    expected = _expected(decision, game)
    if expected is not None and expected == game.status:
        return game
    with transaction.atomic():
        match = lock_match(game.match_id)
        game = ArenaGame.objects.select_related('contest').get(pk=game.pk)
        _tick_locked(match, game, now)
    return game


def _apply(match, game, decision, now, game_problems, attempts, actor=None):
    outcome = decision.outcome
    if outcome == scoring.COUNTDOWN:
        return
    if outcome == scoring.LIVE:
        if game.status != ArenaGame.LIVE:
            game.status = ArenaGame.LIVE
            game.save(update_fields=['status'])
        return
    if outcome == scoring.VERIFYING:
        if game.status != ArenaGame.VERIFYING:
            game.status = ArenaGame.VERIFYING
            game.closed_at = game.closed_at or min(now, game.ends_at)
            game.save(update_fields=['status', 'closed_at'])
            _close_contest(game, game.closed_at)
            _log('verifying', actor, match, game)
        return
    result = _result_snapshot(game, game_problems, attempts)
    if outcome == scoring.FINISH:
        _finish(match, game, decision.winner, decision.reason, result, now, actor)
    elif outcome == scoring.TIE:
        _tie(match, game, result, now, actor)


def _finish(match, game, slot, reason, result, now, actor=None):
    game.status = ArenaGame.FINISHED
    game.winner_slot = slot
    game.reason = reason
    game.result = result
    game.finished_at = now
    game.closed_at = game.closed_at or min(now, game.ends_at or now)
    game.decided_by = actor
    game.save(update_fields=['status', 'winner_slot', 'reason', 'result', 'finished_at', 'closed_at', 'decided_by'])
    _close_contest(game, game.closed_at)
    if match.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        # A game still open when its battle was decided (since 2026-10-28 only an edit in the admin leaves one): it
        # keeps its result, but the series is over. Deciding it again moved the winner of the bracket a second time
        # and left a score like 1-1 in a best of 1 (duelos-nucleo-4).
        _log('finish', actor, match, game, winner=slot, reason=reason)
        return
    if slot == SLOT_A:
        match.wins_a += 1
    else:
        match.wins_b += 1
    _bump(match, 'wins_a', 'wins_b', system=actor is None)
    _log('finish', actor, match, game, winner=slot, reason=reason)
    if max(match.wins_a, match.wins_b) >= match.wins_needed:
        _decide_match(match, slot, 'games', now, actor)
    else:
        _from_bank(match, _ensure_next_game(match), actor)
        # The time held inside the arena counts again from the end of this game.
        refresh_lock_on_commit()


def _tie(match, game, result, now, actor=None):
    game.status = ArenaGame.TIE
    game.result = result
    game.finished_at = now
    game.closed_at = game.closed_at or min(now, game.ends_at or now)
    game.save(update_fields=['status', 'result', 'finished_at', 'closed_at'])
    _close_contest(game, game.closed_at)
    _bump(match, system=actor is None)
    _log('tie', actor, match, game)
    if match.status not in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        _from_bank(match, _ensure_next_game(match), actor)
    refresh_lock_on_commit()


def _decide_match(match, slot, reason, now, actor=None):
    match.status = ArenaMatch.DECIDED
    match.winner_slot = slot
    match.decided_reason = reason
    match.decided_at = now
    _bump(match, 'status', 'winner_slot', 'decided_reason', 'decided_at', system=actor is None)
    # Games prepared in advance and never played leave nothing behind.
    ArenaGame.objects.filter(match=match, status=ArenaGame.PREPARED, contest__isnull=True).delete()
    _log('decided', actor, match, winner=slot, reason=reason)
    from judge.arena import rating, tournament as arena_tournament
    arena_tournament.advance(match, now)
    # If it counts for the ranking of the duels, the ranking is worked out again once this is saved.
    rating.after_decided(match)
    refresh_lock_on_commit()


# ---------------------------------------------------------------------------------------------------------------
# Organizer actions during play
# ---------------------------------------------------------------------------------------------------------------

def _game_of(match, game_id):
    game = ArenaGame.objects.select_related('contest').filter(pk=game_id, match=match).first()
    if game is None:
        raise ArenaError(_('That game does not exist.'), status=404)
    return game


def _require_not_over(match):
    """No action on the games of a battle already decided or cancelled (duelos-nucleo-4, 2026-10-28)."""
    if match.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        raise ArenaError(_('This battle is over.'))


def _refuse_while_another_runs(match, game):
    """A tied game is given to a player or voided only while no later game of the series is being played: the
    tiebreak could otherwise decide the battle a second time (duelos-nucleo-4, 2026-10-28)."""
    if game.status == ArenaGame.TIE and ArenaGame.objects.filter(match=match, status__in=ArenaGame.ACTIVE_STATUSES) \
            .exclude(pk=game.pk).exists():
        raise ArenaError(_('Another game of this battle is being played; decide or void that one first.'))


@transaction.atomic
def extend_game(actor, match_id, game_id, minutes, version=None):
    match = lock_match(match_id)
    _check_version(match, version)
    _require_not_over(match)
    game = _game_of(match, game_id)
    if game.status not in ArenaGame.OPEN_STATUSES:
        raise ArenaError(_('Only a game being played can get more time.'))
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        raise ArenaError(_('Invalid number of minutes.'))
    if not 1 <= minutes <= 60:
        raise ArenaError(_('Add between 1 and 60 minutes.'))
    ends_at = game.ends_at + timedelta(minutes=minutes)
    if ends_at - game.started_at > timedelta(minutes=MAX_TOTAL_MINUTES):
        raise ArenaError(_('A game cannot last more than %(count)s minutes.') % {'count': MAX_TOTAL_MINUTES})
    game.ends_at = ends_at
    game.save(update_fields=['ends_at'])
    Contest.objects.filter(pk=game.contest_id).update(end_time=ends_at)
    _bump(match)
    _log('extend', actor, match, game, minutes=minutes)
    refresh_lock_on_commit()
    return game


@transaction.atomic
def cancel_game(actor, match_id, game_id, version=None):
    """Void a started game: it counts for nobody and the next game is prepared."""
    match = lock_match(match_id)
    _check_version(match, version)
    _require_not_over(match)
    game = _game_of(match, game_id)
    if game.status not in ArenaGame.ACTIVE_STATUSES + (ArenaGame.TIE,):
        raise ArenaError(_('Only a started game can be cancelled.'))
    _refuse_while_another_runs(match, game)
    now = timezone.now()
    game.status = ArenaGame.CANCELLED
    game.finished_at = now
    game.closed_at = game.closed_at or now
    game.decided_by = actor
    game.save(update_fields=['status', 'finished_at', 'closed_at', 'decided_by'])
    _close_contest(game, max(game.started_at, min(now, game.ends_at)))
    _bump(match)
    _log('cancel_game', actor, match, game)
    if match.status == ArenaMatch.PLAYING:
        _from_bank(match, _ensure_next_game(match), actor)
    refresh_lock_on_commit()
    return game


@transaction.atomic
def declare_winner(actor, match_id, game_id, slot, version=None):
    """The organizer calls a game: a tie, a game stuck waiting for verdicts, or a game a player abandoned."""
    if slot not in (SLOT_A, SLOT_B):
        raise ArenaError(_('Choose a winner.'))
    match = lock_match(match_id)
    _check_version(match, version)
    game = _game_of(match, game_id)
    if game.status not in ArenaGame.ACTIVE_STATUSES + (ArenaGame.TIE,):
        raise ArenaError(_('This game cannot be decided now.'))
    if match.status != ArenaMatch.PLAYING:
        raise ArenaError(_('This battle is over.'))
    _refuse_while_another_runs(match, game)
    now = timezone.now()
    game_problems = game_problem_rows(game)
    attempts = load_attempts(game, game_problems)
    result = _result_snapshot(game, game_problems, attempts)
    if game.status == ArenaGame.TIE:
        # The tiebreak game prepared with the tie stays, empty, in case the organizer changes their mind; once the
        # match is decided it is cleaned up with the rest.
        pass
    if game.status in ArenaGame.OPEN_STATUSES:
        game.closed_at = now
    _finish(match, game, slot, 'organizer', result, now, actor)
    return game


@transaction.atomic
def finalize_verification(actor, match_id, game_id, version=None):
    """Stop waiting for verdicts that do not arrive: whatever is still pending counts as failed."""
    match = lock_match(match_id)
    _check_version(match, version)
    _require_not_over(match)
    game = _game_of(match, game_id)
    if game.status != ArenaGame.VERIFYING:
        raise ArenaError(_('This game is not waiting for verdicts.'))
    now = timezone.now()
    game_problems = game_problem_rows(game)
    attempts = load_attempts(game, game_problems)
    decision = scoring.decide_ignoring_pending(attempts[SLOT_A], attempts[SLOT_B], problem_points(game_problems),
                                               game.rules, game.started_at, game.ends_at, now)
    judged = {slot: [a for a in attempts[slot] if not a.pending] for slot in attempts}
    result = _result_snapshot(game, game_problems, judged)
    if decision.outcome == scoring.FINISH:
        _finish(match, game, decision.winner, decision.reason, result, now, actor)
    else:
        _tie(match, game, result, now, actor)
    _log('finalize', actor, match, game)
    return game


@transaction.atomic
def walkover(actor, match_id, slot, version=None):
    """One player wins the whole series without playing on: the other did not show up or withdrew."""
    if slot not in (SLOT_A, SLOT_B):
        raise ArenaError(_('Choose a winner.'))
    match = lock_match(match_id)
    _check_version(match, version)
    if match.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        raise ArenaError(_('This battle is over.'))
    if not match.player_id(slot):
        raise ArenaError(_('That player is not set yet.'))
    now = timezone.now()
    for game in ArenaGame.objects.filter(match=match, status__in=ArenaGame.ACTIVE_STATUSES + (ArenaGame.TIE,)):
        game.status = ArenaGame.CANCELLED
        game.finished_at = now
        game.closed_at = game.closed_at or now
        game.decided_by = actor
        game.save(update_fields=['status', 'finished_at', 'closed_at', 'decided_by'])
        _close_contest(game, max(game.started_at, min(now, game.ends_at)))
    _decide_match(match, slot, 'walkover', now, actor)
    return match


@transaction.atomic
def cancel_match(actor, match_id, version=None):
    """Drop a quick battle altogether. Tournament matches are decided instead, by walkover if need be."""
    match = lock_match(match_id)
    _check_version(match, version)
    if match.tournament_id:
        raise ArenaError(_('A tournament match cannot be cancelled; give it to one player instead.'))
    if match.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        raise ArenaError(_('This battle is over.'))
    now = timezone.now()
    for game in ArenaGame.objects.filter(match=match, status__in=ArenaGame.ACTIVE_STATUSES + (ArenaGame.TIE,)):
        game.status = ArenaGame.CANCELLED
        game.finished_at = now
        game.closed_at = game.closed_at or now
        game.save(update_fields=['status', 'finished_at', 'closed_at'])
        _close_contest(game, max(game.started_at, min(now, game.ends_at)))
    ArenaGame.objects.filter(match=match, status=ArenaGame.PREPARED, contest__isnull=True).delete()
    match.status = ArenaMatch.CANCELLED
    match.decided_at = now
    _bump(match, 'status', 'decided_at')
    _log('cancel', actor, match)
    refresh_lock_on_commit()
    return match


@transaction.atomic
def update_match(actor, match_id, version=None, best_of=None, rules=None, title=None, display_a=None,
                 display_b=None):
    match = lock_match(match_id)
    _check_version(match, version)
    if match.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        raise ArenaError(_('This battle is over.'))
    fields = []
    if best_of is not None:
        try:
            best_of = arena_rules.clean_best_of(best_of)
        except ValidationError as error:
            raise ArenaError(error.messages[0])
        if best_of // 2 + 1 <= max(match.wins_a, match.wins_b):
            raise ArenaError(_('With that length the series would already be decided.'))
        match.best_of = best_of
        fields.append('best_of')
    if rules is not None:
        try:
            match.rules = arena_rules.clean_rules(rules)
        except ValidationError as error:
            raise ArenaError(error.messages[0])
        fields.append('rules')
    if title is not None:
        match.title = title.strip()[:100]
        fields.append('title')
    for slot, value in ((SLOT_A, display_a), (SLOT_B, display_b)):
        if value is not None:
            setattr(match, 'display_name_' + slot, _clean_display(value, match.tournament_id is None))
            fields.append('display_name_' + slot)
    _bump(match, *fields)
    _log('update', actor, match, fields=fields)
    return match


@transaction.atomic
def replace_player(actor, match_id, slot, profile, display='', version=None):
    if slot not in (SLOT_A, SLOT_B) or profile is None:
        raise ArenaError(_('Choose a player.'))
    match = lock_match(match_id)
    _check_version(match, version)
    if match.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED):
        # A decided match has already sent its winner forward in the bracket; correct the winner instead.
        raise ArenaError(_('This battle is over.'))
    if ArenaGame.objects.filter(match=match, contest__isnull=False).exists():
        raise ArenaError(_('Players cannot change once a game has been played.'))
    if match.player_id(other(slot)) == profile.pk:
        raise ArenaError(_('A player cannot face themselves.'))
    _refuse_self(actor, profile)
    if _busy_elsewhere(profile.pk, match.pk):
        raise ArenaError(_('%(name)s is already in another battle.') % {'name': profile.display_name})
    setattr(match, 'player_' + slot, profile)
    setattr(match, 'display_name_' + slot, _clean_display(display, match.tournament_id is None))
    fields = ['player_' + slot, 'display_name_' + slot]
    if match.status == ArenaMatch.PENDING and match.player_a_id and match.player_b_id:
        match.status = ArenaMatch.READY
        fields.append('status')
    _bump(match, *fields)
    _log('replace', actor, match, slot=slot, profile=profile.pk)
    refresh_lock_on_commit()
    return match


# ---------------------------------------------------------------------------------------------------------------
# Submitting from the arena
# ---------------------------------------------------------------------------------------------------------------

def _submission_limit():
    from django.conf import settings
    return getattr(settings, 'DMOJ_SUBMISSION_LIMIT', 2)


def submit(profile, user, game_id, order, language_key, source, nonce=''):
    """Create the submission of a player and send it to the judges. Returns the Submission.

    The same three rows ProblemSubmit creates (submission, contest submission, source), created under the match
    lock and after bringing the game up to date, so nothing is accepted once the game has been called.
    """
    if not isinstance(source, str) or not source.strip():
        raise ArenaError(_('Write some code before submitting.'))
    if len(source) > 65536:
        raise ArenaError(_('The code is too long.'))
    if not isinstance(language_key, str) or not isinstance(order, int) or isinstance(order, bool):
        raise ArenaError(_('Invalid submission.'))
    nonce = nonce if isinstance(nonce, str) and 0 < len(nonce) <= 64 else ''
    if nonce:
        try:
            previous = cache.get('arena:nonce:%d:%s' % (profile.pk, nonce))
        except Exception:
            previous = None
        if previous:
            existing = Submission.objects.filter(pk=previous, user=profile).first()
            if existing is not None:
                return existing

    match_id = ArenaGame.objects.filter(pk=game_id).values_list('match_id', flat=True).first()
    if match_id is None:
        raise ArenaError(_('That game does not exist.'), status=404)
    with transaction.atomic():
        match = lock_match(match_id)
        game = ArenaGame.objects.select_related('contest').get(pk=game_id)
        slot = match.slot_of(profile.pk)
        if slot is None:
            raise ArenaError(_('You are not playing this game.'), status=403)
        now = timezone.now()
        # Bring the game up to date under the lock: an accepted solution of the rival judged a moment ago may have
        # already ended it.
        _tick_locked(match, game, now)
        if game.status not in ArenaGame.OPEN_STATUSES or game.closed_at is not None or \
                game.started_at is None or now < game.started_at:
            raise ArenaError(_('This game is not accepting submissions.'), status=409)
        if now >= game.ends_at:
            raise ArenaError(_('Time is up.'), status=409)
        gp = ArenaGameProblem.objects.filter(game=game, order=order).select_related('problem', 'contest_problem') \
            .first()
        if gp is None or gp.contest_problem is None:
            raise ArenaError(_('That problem is not part of this game.'))
        language = gp.problem.allowed_languages.filter(key=language_key).first()
        wanted = set((game.rules or {}).get('languages') or ())
        if language is None or (wanted and language.key not in wanted):
            raise ArenaError(_('That language is not allowed here.'))
        if not user.is_superuser and gp.problem.banned_users.filter(pk=profile.pk).exists():
            raise ArenaError(_('You cannot submit to this problem.'), status=403)
        participation = game.participation(slot)
        max_attempts = int((game.rules or {}).get('max_attempts') or 0)
        if max_attempts:
            used = Submission.objects.filter(contest__participation=participation, contest__problem=gp.contest_problem) \
                .exclude(status__in=scoring.NOT_ATTEMPTS).count()
            if used >= max_attempts:
                raise ArenaError(_('You have no attempts left on this problem.'), status=409)
        if not user.has_perm('judge.spam_submission') and \
                Submission.objects.filter(user=profile, rejudged_date__isnull=True) \
                .exclude(status__in=['D', 'IE', 'CE', 'AB']).exclude(problem__code__startswith='ct_') \
                .count() >= _submission_limit():
            raise ArenaError(_('Wait for your previous submissions to be judged.'), status=429)

        submission = Submission(user=profile, problem=gp.problem, language=language, contest_object=game.contest)
        submission.save()
        ContestSubmission(submission=submission, problem=gp.contest_problem, participation=participation).save()
        source_row = SubmissionSource(submission=submission, source=source)
        source_row.save()
        if nonce:
            try:
                cache.set('arena:nonce:%d:%s' % (profile.pk, nonce), submission.pk, SUBMISSION_NONCE_SECONDS)
            except Exception:
                pass
    submission.source = source_row
    submission.judge(force_judge=True)
    return submission


# ---------------------------------------------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------------------------------------------

def new_screen_token():
    return secrets.token_urlsafe(32)


def ensure_default_screen(actor):
    """The first battle or tournament of an organizer comes with a projector screen, so the panel has links to open.
    Whoever organizes only their own battles only sees their own screens, so they get one of their own."""
    if not access.screens_of_actor(actor).exists():
        ArenaScreen.objects.create(name=_('Main screen'), token=new_screen_token(), created_by=actor)


@transaction.atomic
def create_screen(actor, name):
    name = (name or '').strip()[:60] or _('Screen')
    return ArenaScreen.objects.create(name=name, token=new_screen_token(), created_by=actor)


@transaction.atomic
def show_on_screen(actor, screen_id, mode, match=None, tournament=None, message='', follow_live=None,
                   version=None):
    screen = ArenaScreen.objects.select_for_update().filter(pk=screen_id).first()
    if screen is None:
        raise ArenaError(_('That screen does not exist.'), status=404)
    _check_version(screen, version)
    if mode not in dict(ArenaScreen.MODES):
        raise ArenaError(_('Invalid screen mode.'))
    screen.mode = mode
    screen.match = match if mode == ArenaScreen.MATCH else (screen.match if match is None else match)
    if tournament is not None:
        screen.tournament = tournament
    if follow_live is not None:
        screen.follow_live = bool(follow_live)
    screen.message = (message or '').strip()[:200]
    screen.version += 1
    screen.save()
    return screen


@transaction.atomic
def rename_screen(actor, screen_id, name):
    screen = ArenaScreen.objects.select_for_update().filter(pk=screen_id).first()
    if screen is None:
        raise ArenaError(_('That screen does not exist.'), status=404)
    screen.name = (name or '').strip()[:60] or screen.name
    screen.version += 1
    screen.save()
    return screen


@transaction.atomic
def regenerate_screen_token(actor, screen_id):
    screen = ArenaScreen.objects.select_for_update().filter(pk=screen_id).first()
    if screen is None:
        raise ArenaError(_('That screen does not exist.'), status=404)
    screen.token = new_screen_token()
    screen.version += 1
    screen.save()
    return screen


def default_language_for(profile, languages):
    if not languages:
        return None
    preferred = getattr(profile, 'language_id', None)
    for language in languages:
        if language.pk == preferred:
            return language
    return languages[0]

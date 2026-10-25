"""What each page is told about a battle.

Three separate builders, one per audience: the player, the organizer and the projector screens. They never share
a dictionary, so a field added for one audience cannot reach another by accident. In particular nothing about the
problems of a game (code, name, statement, languages) leaves the server before the game has started, and a player
is never sent the rival's code.

Times go out as UNIX timestamps in seconds, together with the server's own `now`, so every page counts down
against the server clock rather than its own.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from judge.arena import access, engine, live_code, rules as arena_rules, scoring
from judge.arena.tournament import draw_settings, round_count, round_name
from judge.jinja2.gravatar import gravatar
from judge.models import Language, ProblemTranslation, Submission, SubmissionTestCase
from judge.models.arena import ArenaAction, ArenaGame, ArenaGameProblem, ArenaMatch, ArenaScreen, ArenaTournament, \
    SLOT_A, SLOT_B

RECENT_RESULT = timedelta(minutes=30)
QUEUE_WARNING_SECONDS = 60
# The photos of the waiting room and the countdown fill up to ~40% of the screen height: asked at this size they
# stay sharp on a 1080p projector.
BIG_AVATAR = 512

POLL_FAST = 1000
POLL_MEDIUM = 2000
POLL_SLOW = 5000


def ts(moment):
    return None if moment is None else round(moment.timestamp(), 3)


def _started(game, now):
    return game is not None and game.started_at is not None and now >= game.started_at and \
        game.status not in (ArenaGame.PREPARED,)


def problem_names(game_problems, language):
    names = {gp.problem_id: gp.problem.name for gp in game_problems}
    if game_problems:
        for problem_id, name in ProblemTranslation.objects.filter(
                problem_id__in=list(names), language=language).values_list('problem_id', 'name'):
            names[problem_id] = name
    return names


def round_label(match):
    if not match.round_id:
        return ''
    return round_name(match.round, match.tournament.rounds.count())


def player_card(match, slot, size=128):
    profile = match.player(slot)
    return {
        'name': match.shown_name(slot) or _('To be decided'),
        'username': profile.username if profile is not None else '',
        'avatar': gravatar(profile, size) if profile is not None else None,
        'seed': getattr(match, 'seed_' + slot),
    }


def _rules_view(rules):
    return {name: rules.get(name, arena_rules.DEFAULTS[name])
            for name in ('duration', 'countdown', 'partial', 'penalty', 'max_attempts', 'block_paste', 'show_rival',
                         'screen_code')}


def _game_rules(game):
    return game.rules if game.rules else arena_rules.match_rules(game.match)


def _verdicts(game, game_problems, slots, limit=None, newest_first=True):
    """Submissions of a started game, as (slot, row) pairs."""
    if game.contest_id is None:
        return []
    by_participation = {game.participation_id(slot): slot for slot in slots}
    labels = {gp.contest_problem_id: gp for gp in game_problems if gp.contest_problem_id}
    rows = Submission.objects.filter(contest_object_id=game.contest_id,
                                     contest__participation_id__in=list(by_participation)) \
        .values('id', 'date', 'status', 'result', 'case_points', 'case_total', 'time', 'memory', 'current_testcase',
                'language__name', 'language__key', 'contest__participation_id', 'contest__problem_id') \
        .order_by('-id' if newest_first else 'id')
    if limit:
        rows = rows[:limit]
    rules = _game_rules(game)
    result = []
    for row in rows:
        gp = labels.get(row['contest__problem_id'])
        if gp is None:
            continue
        attempt = scoring.Attempt(row['id'], row['date'], row['status'], row['result'], row['case_points'] or 0,
                                  row['case_total'] or 0, gp.order)
        result.append((by_participation[row['contest__participation_id']], {
            'id': row['id'],
            'problem': gp.order,
            'label': gp.label,
            'date': ts(row['date']),
            'status': row['status'],
            'result': row['result'],
            'pending': attempt.pending,
            'points': scoring.points_of(attempt, gp.points, bool(rules.get('partial'))),
            'full': gp.points,
            'time': row['time'],
            'memory': row['memory'],
            'case': row['current_testcase'],
            'language': row['language__name'],
        }))
    return result


def _standings_view(game, game_problems, attempts=None):
    order = [gp.order for gp in game_problems]
    if game.status in ArenaGame.DONE_STATUSES and game.result:
        return game.result
    rows = engine.standings(game, game_problems, attempts)
    return {slot: scoring.summary(rows[slot], game.started_at, order) for slot in (SLOT_A, SLOT_B)}


def _series(match, games, perspective=None):
    def who(slot):
        if not slot:
            return None
        if perspective is None:
            return slot
        return 'me' if slot == perspective else 'rival'
    return {
        'best_of': match.best_of,
        'needed': match.wins_needed,
        'wins': {'a': match.wins_a, 'b': match.wins_b},
        'games': [{'number': game.number, 'status': game.status, 'winner': who(game.winner_slot),
                   'reason': game.reason} for game in games if game.status != ArenaGame.PREPARED or
                  game.game_problems.exists()],
    }


def _tick_active(match):
    active = ArenaGame.objects.filter(match=match, status__in=ArenaGame.ACTIVE_STATUSES).first()
    if active is not None:
        engine.tick(active)
        match.refresh_from_db()
    engine.ensure_lock()
    return match


def _current_games(match):
    games = list(ArenaGame.objects.filter(match=match).order_by('number'))
    active = next((game for game in games if game.status in ArenaGame.ACTIVE_STATUSES), None)
    done = [game for game in games if game.status in ArenaGame.DONE_STATUSES]
    prepared = next((game for game in games if game.status == ArenaGame.PREPARED), None)
    return games, active, (done[-1] if done else None), prepared


# ---------------------------------------------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------------------------------------------

def own_account(match, slot):
    """Whether this place is played on the player's own account, not on a club laptop with a typed name."""
    return slot in (SLOT_A, SLOT_B) and not getattr(match, 'display_name_' + slot)


def _player_match(profile, now):
    match = engine.active_match_for(profile.id)
    if match is None:
        match = ArenaMatch.objects.filter(Q(player_a=profile) | Q(player_b=profile),
                                          status__in=(ArenaMatch.DECIDED, ArenaMatch.CANCELLED),
                                          decided_at__gte=now - RECENT_RESULT) \
            .exclude(decided_reason='bye').order_by('-decided_at', '-id').first()
    return match


def player_state(profile, language, now=None):
    now = now or timezone.now()
    match = _player_match(profile, now)
    if match is None:
        return {'phase': 'idle', 'now': ts(now), 'poll': POLL_SLOW}
    match = _tick_active(match)
    now = timezone.now()
    slot = match.slot_of(profile.id)
    rival = engine.other(slot)
    if match.status in ArenaMatch.LOCKING_STATUSES:
        engine.mark_seen(match.pk, slot)
    games, active, last_done, prepared = _current_games(match)

    if match.status == ArenaMatch.CANCELLED:
        phase = 'cancelled'
    elif match.status == ArenaMatch.DECIDED:
        phase = 'decided'
    elif active is not None:
        phase = active.status
    elif last_done is not None:
        phase = 'between'
    else:
        phase = 'lobby'
    shown = active or last_done
    poll = {'countdown': POLL_FAST, 'live': POLL_FAST, 'verifying': POLL_FAST}.get(phase, POLL_MEDIUM)
    if phase in ('decided', 'cancelled'):
        poll = POLL_SLOW

    data = {
        'phase': phase,
        'now': ts(now),
        'poll': poll,
        # After a game, a player on their own account may stay to look it over: edit and run their code, read their
        # submissions. On a club laptop (a name typed by the organizer) it is cleared for the next guest instead.
        'review': phase in ('decided', 'between') and own_account(match, slot),
        'locked': match.status in ArenaMatch.LOCKING_STATUSES and engine.lock_until(match, games) > now,
        'match': {
            'id': match.pk,
            'title': match.title,
            'tournament': match.tournament.name if match.tournament_id else '',
            'round': round_label(match),
            'status': match.status,
            'winner': None if not match.winner_slot else ('me' if match.winner_slot == slot else 'rival'),
            'reason': match.decided_reason,
        },
        'me': player_card(match, slot, BIG_AVATAR),
        'rival': player_card(match, rival, BIG_AVATAR),
        'series': _series(match, games, slot),
        'game': None,
    }
    if shown is not None:
        data['game'] = _player_game(match, shown, slot, now, language)
    elif prepared is not None:
        data['game'] = {'id': prepared.pk, 'number': prepared.number, 'status': prepared.status,
                        'rules': _rules_view(arena_rules.match_rules(match))}
    return data


def _player_game(match, game, slot, now, language):
    rival = engine.other(slot)
    rules = _game_rules(game)
    data = {
        'id': game.pk,
        'number': game.number,
        'status': game.status,
        'started_at': ts(game.started_at),
        'ends_at': ts(game.ends_at),
        'closed_at': ts(game.closed_at),
        'rules': _rules_view(rules),
        'winner': None if not game.winner_slot else ('me' if game.winner_slot == slot else 'rival'),
        'reason': game.reason,
        'started': _started(game, now),
    }
    if not data['started']:
        return data
    game_problems = engine.game_problem_rows(game)
    names = problem_names(game_problems, language)
    languages = engine.cached_game_languages(game, game_problems, rules)
    attempts = engine.load_attempts(game, game_problems)
    max_attempts = int(rules.get('max_attempts') or 0)
    data['problems'] = [{
        'order': gp.order,
        'label': gp.label,
        'name': names[gp.problem_id],
        'points': gp.points,
        'time_limit': gp.problem.time_limit,
        'memory_limit': gp.problem.memory_limit,
        'languages': languages[gp.order],
        'attempts_left': scoring.attempts_left(attempts[slot], gp.order, max_attempts),
    } for gp in game_problems]
    standings = _standings_view(game, game_problems, attempts)
    data['standing'] = {'me': standings.get(slot), 'rival': standings.get(rival) if rules.get('show_rival') else None}
    data['submissions'] = [row for row_slot, row in _verdicts(game, game_problems, (slot,))]
    return data


def player_submission(profile, game, submission_id):
    """One of the player's own submissions in this game, with its compile error and its test cases."""
    slot = game.match.slot_of(profile.id)
    if slot is None or game.contest_id is None:
        return None
    submission = Submission.objects.filter(pk=submission_id, contest_object_id=game.contest_id,
                                           contest__participation_id=game.participation_id(slot)) \
        .select_related('language', 'source').first()
    if submission is None:
        return None
    cases = list(SubmissionTestCase.objects.filter(submission=submission).order_by('case')
                 .values('case', 'status', 'time', 'memory', 'points', 'total', 'batch'))
    data = {
        'id': submission.pk,
        'status': submission.status,
        'result': submission.result,
        'time': submission.time,
        'memory': submission.memory,
        'case': submission.current_testcase,
        'error': (submission.error or '') if submission.status in ('CE', 'IE') else '',
        'cases': cases,
        'language': submission.language.name,
    }
    # The player's own code, to see later what went wrong. Not on a club laptop: the next guest would read it.
    if own_account(game.match, slot):
        source = getattr(submission, 'source', None)
        data['source'] = source.source if source is not None else ''
    return data


# ---------------------------------------------------------------------------------------------------------------
# Organizer
# ---------------------------------------------------------------------------------------------------------------

def _privileged(profile):
    if profile is None:
        return False
    user = profile.user
    return bool(user.is_staff or user.is_superuser)


def organizer_player(match, slot, now):
    card = player_card(match, slot)
    profile = match.player(slot)
    seen = engine.last_seen(match.pk, slot)
    card.update({
        'id': profile.pk if profile else None,
        'display_name': getattr(match, 'display_name_' + slot),
        'connected': engine.is_connected(match.pk, slot, now),
        'seen_ago': None if seen is None else round(now.timestamp() - seen, 1),
        'privileged': _privileged(profile),
    })
    return card


def _organizer_game(game, now, language, with_submissions=False):
    game_problems = engine.game_problem_rows(game)
    names = problem_names(game_problems, language)
    rules = _game_rules(game)
    data = {
        'id': game.pk,
        'number': game.number,
        'status': game.status,
        'started_at': ts(game.started_at),
        'ends_at': ts(game.ends_at),
        'closed_at': ts(game.closed_at),
        'finished_at': ts(game.finished_at),
        'winner': game.winner_slot or None,
        'reason': game.reason,
        'rules': _rules_view(rules),
        'started': _started(game, now),
        'contest': game.contest.key if game.contest_id else None,
        'problems': [{'order': gp.order, 'label': gp.label, 'id': gp.problem_id, 'code': gp.problem.code,
                      'name': names[gp.problem_id], 'points': gp.points,
                      'url': reverse('problem_detail', args=[gp.problem.code])} for gp in game_problems],
    }
    if game.status == ArenaGame.PREPARED and not game_problems:
        # The bank was asked for problems and did not have enough: the panel says to choose them by hand.
        last = ArenaAction.objects.filter(game=game, action__in=('bank_problems', 'bank_empty')) \
            .order_by('-time', '-id').values_list('action', 'data').first()
        data['bank_empty'] = last[1].get('reason', '') if last and last[0] == 'bank_empty' else ''
    if game.started_at is not None:
        data['standing'] = _standings_view(game, game_problems)
    if with_submissions and game.contest_id:
        rows = _verdicts(game, game_problems, (SLOT_A, SLOT_B), limit=200)
        data['submissions'] = {SLOT_A: [], SLOT_B: []}
        for slot, row in rows:
            row['queued_for'] = round(now.timestamp() - row['date'], 1) if row['pending'] else None
            row['stuck'] = bool(row['pending'] and row['queued_for'] > QUEUE_WARNING_SECONDS)
            data['submissions'][slot].append(row)
        data['flags'] = {slot: live_code.flags(game.pk, slot) for slot in (SLOT_A, SLOT_B)}
        data['code_seq'] = {slot: live_code.latest_seq(game.pk, slot) for slot in (SLOT_A, SLOT_B)}
    return data


def organizer_card(profile):
    return {'id': profile.pk, 'name': profile.display_name, 'username': profile.username,
            'avatar': gravatar(profile, 48)}


def organizers_view(obj):
    """Who runs a tournament or a quick battle with the permission to organize only their own battles: its creator
    and its co-organizers. Whoever organizes all battles runs it too, without being listed."""
    return {'creator': organizer_card(obj.created_by) if obj.created_by_id else None,
            'list': [organizer_card(profile) for profile in
                     obj.organizers.select_related('user').order_by('user__username')]}


def organizer_match_state(match, language, now=None, user=None):
    """`user`: who asks; the screens are only theirs (judge/arena/access.py). Without one, all of them."""
    match = _tick_active(match)
    now = timezone.now()
    games, active, last_done, prepared = _current_games(match)
    current = active or prepared or last_done
    held_until = engine.lock_until(match, games) if match.status in ArenaMatch.LOCKING_STATUSES else None
    data = {
        'now': ts(now),
        'match': {
            'id': match.pk,
            'version': match.version,
            'title': match.title,
            'status': match.status,
            'tournament': {'id': match.tournament_id, 'name': match.tournament.name,
                           'slug': match.tournament.slug, 'status': match.tournament.status}
            if match.tournament_id else None,
            'round': round_label(match),
            'best_of': match.best_of,
            'wins': {'a': match.wins_a, 'b': match.wins_b},
            'winner': match.winner_slot or None,
            'reason': match.decided_reason,
            'quick': match.tournament_id is None,
            'rules': _rules_view(arena_rules.match_rules(match)),
            'overrides': match.rules,
            # Whether the players are still sent back to the arena from the rest of the site (see lock.py).
            'held': held_until is not None and held_until > now,
            'held_until': ts(held_until),
            'idle_minutes': engine.lock_idle_minutes(),
            # Games 1..max_games can get their problems in advance; ties and voided games add one each.
            'max_games': engine.max_game_number(match),
            'draw': _draw_view(match),
        },
        'players': {slot: organizer_player(match, slot, now) for slot in (SLOT_A, SLOT_B)},
        'games': [_organizer_game(game, now, language, with_submissions=current is not None and game.pk == current.pk)
                  for game in games],
        'current': current.pk if current is not None else None,
        'warnings': [],
        'screens': screens_overview(user),
        # A battle of a tournament is run by the organizers of the tournament: the panel shows them, read only.
        'organizers': dict(organizers_view(match.tournament if match.tournament_id else match),
                           tournament=bool(match.tournament_id)),
        'log': [{'time': ts(action.time), 'action': action.action,
                 'actor': action.actor.display_name if action.actor_id else '', 'data': action.data}
                for action in ArenaAction.objects.filter(match=match).select_related('actor__user')
                .order_by('-time', '-id')[:25]],
    }
    if prepared is not None and active is None and match.status in (ArenaMatch.READY, ArenaMatch.CALLED,
                                                                       ArenaMatch.PLAYING):
        data['warnings'] = engine.start_warnings(match, prepared, now)
    return data


def screens_overview(user=None):
    """The screens `user` manages (with their secret links), or all of them without a user."""
    screens = access.screens_for(user) if user is not None else ArenaScreen.objects.all()
    return [{'id': screen.pk, 'name': screen.name, 'mode': screen.mode, 'match': screen.match_id,
             'tournament': screen.tournament_id, 'follow_live': screen.follow_live, 'message': screen.message,
             'token': screen.token, 'version': screen.version}
            for screen in screens.order_by('name', 'id')]


def overview(now=None, user=None):
    """Everything the dashboard lists: with a `user`, only what they organize (judge/arena/access.py)."""
    now = now or timezone.now()
    matches = access.matches_for(user) if user is not None else ArenaMatch.objects.all()
    tournaments = access.tournaments_for(user) if user is not None else ArenaTournament.objects.all()
    active = matches.filter(status__in=(ArenaMatch.READY, ArenaMatch.CALLED, ArenaMatch.PLAYING),
                            tournament__isnull=True).select_related('player_a__user', 'player_b__user')
    recent = matches.filter(status__in=(ArenaMatch.DECIDED, ArenaMatch.CANCELLED),
                            tournament__isnull=True).select_related('player_a__user', 'player_b__user') \
        .order_by('-decided_at', '-id')[:15]

    def row(match):
        live = ArenaGame.objects.filter(match=match, status__in=ArenaGame.ACTIVE_STATUSES).first()
        return {'id': match.pk, 'title': match.title, 'a': match.shown_name(SLOT_A), 'b': match.shown_name(SLOT_B),
                'status': match.status, 'wins': {'a': match.wins_a, 'b': match.wins_b}, 'best_of': match.best_of,
                'winner': match.winner_slot or None, 'live': live.status if live else None,
                'decided_at': ts(match.decided_at)}
    tournaments = tournaments.exclude(status=ArenaTournament.ARCHIVED).order_by('-created_at')[:30]
    return {
        'now': ts(now),
        'duels': [row(match) for match in active.order_by('-id')],
        'recent': [row(match) for match in recent],
        'tournaments': [{'id': t.pk, 'name': t.name, 'slug': t.slug, 'status': t.status,
                         'participants': t.participants.count(),
                         'live': ArenaMatch.objects.filter(tournament=t, status=ArenaMatch.PLAYING).count()}
                        for t in tournaments],
        'screens': screens_overview(user),
    }


def _draw_view(match):
    settings = draw_settings(match)
    return {'auto': settings['auto'], 'bank': settings['bank'] is not None, 'count': settings['count']}


def _problem_readiness(tournament):
    """For every match of a tournament: how many games already have problems, and whether the next one to play
    does. Only for the organizer page: the projector never learns anything about the problems before a game."""
    games = list(ArenaGame.objects.filter(match__tournament=tournament).values('id', 'match_id', 'number', 'status'))
    counts = dict(ArenaGameProblem.objects.filter(game__match__tournament=tournament).values('game_id')
                  .annotate(n=Count('id')).values_list('game_id', 'n'))
    result = {}
    for game in sorted(games, key=lambda row: row['number']):
        row = result.setdefault(game['match_id'], {'chosen': 0, 'next_ready': None})
        if counts.get(game['id']) and game['status'] != ArenaGame.CANCELLED:
            row['chosen'] += 1
        if game['status'] == ArenaGame.PREPARED and row['next_ready'] is None:
            row['next_ready'] = bool(counts.get(game['id']))
    return result


def tournament_state(tournament, language, now=None, user=None):
    """`user`: who asks; the screens are only theirs (judge/arena/access.py). Without one, all of them."""
    now = now or timezone.now()
    for match in ArenaMatch.objects.filter(tournament=tournament, status=ArenaMatch.PLAYING):
        _tick_active(match)
    tournament.refresh_from_db()
    rounds = list(tournament.rounds.order_by('number'))
    total = len(rounds)
    participants = list(tournament.participants.select_related('profile__user').order_by('seed'))
    return {
        'now': ts(now),
        'tournament': {
            'id': tournament.pk, 'name': tournament.name, 'slug': tournament.slug, 'status': tournament.status,
            'description': tournament.description, 'version': tournament.version,
            'seeding': tournament.seeding, 'third_place': tournament.third_place_match,
            'bank': tournament.bank_id, 'rules': _rules_view(arena_rules.merge(tournament.rules)),
            'overrides': tournament.rules,
        },
        'participants': [{'id': p.profile_id, 'seed': p.seed, 'name': p.profile.display_name,
                          'username': p.profile.username, 'avatar': gravatar(p.profile, 64),
                          'rating': p.profile.rating} for p in participants],
        'rounds': [{'number': r.number, 'name': round_name(r, total), 'custom_name': r.name, 'best_of': r.best_of,
                    'overrides': r.rules, 'draw_count': (r.rules or {}).get('draw_count'),
                    'level_min': (r.rules or {}).get('level_min'), 'level_max': (r.rules or {}).get('level_max')}
                   for r in rounds],
        'draw': {'auto': bool((tournament.rules or {}).get('auto_draw')),
                 'count': (tournament.rules or {}).get('draw_count') or 1, 'bank': tournament.bank_id},
        'bracket': _with_readiness(bracket_view(tournament, rounds), _problem_readiness(tournament)),
        'screens': screens_overview(user),
        'organizers': organizers_view(tournament),
    }


def _with_readiness(bracket, readiness):
    for round_view in bracket['rounds']:
        for match in round_view['matches']:
            row = readiness.get(match['id'], {})
            match['problems'] = {'chosen': row.get('chosen', 0), 'next_ready': bool(row.get('next_ready'))}
    return bracket


def bracket_view(tournament, rounds=None):
    rounds = rounds if rounds is not None else list(tournament.rounds.order_by('number'))
    total = len(rounds)
    matches = list(ArenaMatch.objects.filter(tournament=tournament).select_related('player_a__user', 'player_b__user',
                                                                                    'round')
                   .order_by('round__number', 'is_third_place', 'position'))
    live = set(ArenaGame.objects.filter(match__tournament=tournament, status__in=ArenaGame.ACTIVE_STATUSES)
               .values_list('match_id', flat=True))

    def cell(match, slot):
        profile = match.player(slot)
        if profile is None:
            return None
        return {'name': match.shown_name(slot), 'seed': getattr(match, 'seed_' + slot),
                'avatar': gravatar(profile, 64)}

    result = []
    for r in rounds:
        result.append({
            'number': r.number,
            'name': round_name(r, total),
            'best_of': r.best_of,
            'matches': [{
                'id': m.pk, 'position': m.position, 'third_place': m.is_third_place, 'status': m.status,
                'a': cell(m, SLOT_A), 'b': cell(m, SLOT_B), 'wins': {'a': m.wins_a, 'b': m.wins_b},
                'winner': m.winner_slot or None, 'reason': m.decided_reason, 'live': m.pk in live,
                'best_of': m.best_of,
            } for m in matches if m.round_id == r.pk],
        })
    final = next((m for m in matches if m.next_match_id is None and not m.is_third_place and
                  m.round is not None and m.round.number == total), None)
    champion = None
    if final is not None and final.status == ArenaMatch.DECIDED and final.winner_slot:
        champion = cell(final, final.winner_slot)
    return {'rounds': result, 'champion': champion}


# ---------------------------------------------------------------------------------------------------------------
# Projector screens
# ---------------------------------------------------------------------------------------------------------------

def _screen_target(screen):
    """(mode, match, tournament) actually shown by a screen right now."""
    if screen.mode == ArenaScreen.MATCH and screen.match_id:
        return ArenaScreen.MATCH, screen.match, screen.match.tournament
    if screen.mode == ArenaScreen.BRACKET and screen.tournament_id:
        if screen.follow_live:
            live = ArenaMatch.objects.filter(tournament_id=screen.tournament_id,
                                             games__status__in=ArenaGame.ACTIVE_STATUSES) \
                .order_by('-games__started_at').first()
            if live is not None:
                return ArenaScreen.MATCH, live, screen.tournament
        return ArenaScreen.BRACKET, None, screen.tournament
    if screen.mode == ArenaScreen.MESSAGE:
        return ArenaScreen.MESSAGE, None, screen.tournament
    return ArenaScreen.IDLE, None, screen.tournament


def screen_state(screen, language, now=None):
    now = now or timezone.now()
    mode, match, tournament = _screen_target(screen)
    data = {'now': ts(now), 'mode': mode, 'screen': {'name': screen.name, 'version': screen.version},
            'message': screen.message if mode == ArenaScreen.MESSAGE else '',
            'tournament': tournament.name if tournament is not None else ''}
    if mode == ArenaScreen.MATCH and match is not None:
        data['match'] = screen_match(match, language)
        data['now'] = ts(timezone.now())
    # A tab can show the bracket even while the screen follows a match of the tournament.
    if tournament is not None:
        data['bracket'] = bracket_view(tournament)
    return data


def screen_match(match, language):
    match = _tick_active(match)
    now = timezone.now()
    games, active, last_done, prepared = _current_games(match)
    shown = active or last_done
    data = {
        'id': match.pk,
        'title': match.title,
        'round': round_label(match),
        'status': match.status,
        'players': {slot: player_card(match, slot, BIG_AVATAR) for slot in (SLOT_A, SLOT_B)},
        'series': _series(match, games),
        'winner': match.winner_slot or None,
        'game': None,
        'next_game': prepared.number if prepared is not None and active is None else None,
    }
    if shown is None:
        return data
    rules = _game_rules(shown)
    game = {
        'id': shown.pk,
        'number': shown.number,
        'status': shown.status,
        'started_at': ts(shown.started_at),
        'ends_at': ts(shown.ends_at),
        'closed_at': ts(shown.closed_at),
        'winner': shown.winner_slot or None,
        'reason': shown.reason,
        'started': _started(shown, now),
        'code': bool(rules.get('screen_code')),
        'partial': bool(rules.get('partial')),
        'penalty': int(rules.get('penalty') or 0),
    }
    if game['started']:
        game_problems = engine.game_problem_rows(shown)
        names = problem_names(game_problems, language)
        game['problems'] = [{'order': gp.order, 'label': gp.label, 'name': names[gp.problem_id],
                             'points': gp.points} for gp in game_problems]
        game['standing'] = _standings_view(shown, game_problems)
        game['verdicts'] = [dict(row, slot=slot) for slot, row in
                            _verdicts(shown, game_problems, (SLOT_A, SLOT_B), limit=12)]
    data['game'] = game
    return data


def screen_game(screen):
    """The game whose code and statements a screen may show, or None."""
    mode, match, _tournament = _screen_target(screen)
    if mode != ArenaScreen.MATCH or match is None:
        return None
    games, active, last_done, prepared = _current_games(match)
    return active or last_done


def code_payload(game, since, enforce_screen_rule):
    """Latest code of both players of a game, only for the players whose code changed since `since`."""
    rules = _game_rules(game)
    if enforce_screen_rule and not rules.get('screen_code'):
        return {'enabled': False}
    result = {'enabled': True}
    snapshots = {}
    for slot in (SLOT_A, SLOT_B):
        snapshot = live_code.latest(game.pk, slot)
        # Any other number than the one the page has, not only a higher one: if the cache restarted mid-game the
        # count starts again from 1, and the page must not wait for it to pass its old number.
        if snapshot is not None and snapshot.get('seq', 0) != since.get(slot, 0):
            snapshots[slot] = snapshot
    if not snapshots:
        # The usual answer while a game is watched several times a second: nothing new, and no query for it.
        return dict(result, a=None, b=None)
    game_problems = {gp.order: gp for gp in ArenaGameProblem.objects.filter(game=game)}
    modes = dict(Language.objects.filter(key__in=[s.get('language') for s in snapshots.values()])
                 .values_list('key', 'ace'))
    for slot in (SLOT_A, SLOT_B):
        snapshot = snapshots.get(slot)
        if snapshot is None:
            result[slot] = None
            continue
        gp = game_problems.get(snapshot.get('problem'))
        result[slot] = {'seq': snapshot['seq'], 'text': snapshot.get('text', ''),
                        'label': gp.label if gp is not None else '', 'problem': snapshot.get('problem'),
                        'language': snapshot.get('language') or '', 'ace': modes.get(snapshot.get('language'), 'text'),
                        'cursor': snapshot.get('cursor'), 'time': snapshot.get('time')}
    return result


__all__ = ['player_state', 'player_submission', 'organizer_match_state', 'overview', 'tournament_state',
           'bracket_view', 'screen_state', 'screen_game', 'code_payload', 'screens_overview', 'round_count',
           'ts']

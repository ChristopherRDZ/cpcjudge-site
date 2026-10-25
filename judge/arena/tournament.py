"""Single-elimination tournaments: participants, seeding, bracket generation and moving winners forward.

The bracket is filled up to the next power of two with the standard seeding order (1 against 8, 4 against 5, 2
against 7, 3 against 6 for eight places), so the empty places, the byes, always fall against the best seeds and
never against each other. A match with a bye is decided on the spot.
"""
import logging
import secrets
from random import SystemRandom

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _

from judge.arena import rules as arena_rules
from judge.arena.engine import ArenaError, _bump, _check_version, _log, ensure_default_screen, lock_match, \
    problems_used_in_tournament, refresh_lock_on_commit, visible_problems
from judge.models import Submission
from judge.models.arena import ArenaBankProblem, ArenaGame, ArenaGameProblem, ArenaMatch, ArenaParticipant, \
    ArenaProblemBank, ArenaRound, ArenaTournament, SLOT_A, SLOT_B

logger = logging.getLogger('judge.arena')

MIN_PARTICIPANTS = 2
MAX_PARTICIPANTS = 64


def seeding_order(size):
    """Seeds in bracket order for a power-of-two `size`: consecutive pairs play each other in the first round."""
    order = [1]
    while len(order) < size:
        total = len(order) * 2 + 1
        order = [seed for current in order for seed in (current, total - current)]
    return order


def bracket_size(count):
    size = 1
    while size < count:
        size *= 2
    return size


def round_count(count):
    size, rounds = bracket_size(count), 0
    while size > 1:
        size //= 2
        rounds += 1
    return rounds


def default_round_name(number, rounds):
    from_end = rounds - number
    if from_end == 0:
        return _('Final')
    if from_end == 1:
        return _('Semifinal')
    if from_end == 2:
        return _('Quarterfinal')
    if from_end == 3:
        return _('Round of 16')
    return _('Round %(number)s') % {'number': number}


def round_name(round_obj, rounds):
    return round_obj.name or default_round_name(round_obj.number, rounds)


def _unique_slug(name):
    base = slugify(name)[:50] or 'torneo'
    slug = base
    while ArenaTournament.objects.filter(slug=slug).exists():
        slug = '%s-%s' % (base, secrets.token_hex(2))
    return slug


def _clean_participants(profiles):
    seen, result = set(), []
    for profile in profiles:
        if profile is None or profile.pk in seen:
            continue
        seen.add(profile.pk)
        result.append(profile)
    if len(result) < MIN_PARTICIPANTS:
        raise ArenaError(_('A tournament needs at least %(count)s participants.') % {'count': MIN_PARTICIPANTS})
    if len(result) > MAX_PARTICIPANTS:
        raise ArenaError(_('A tournament can have at most %(count)s participants.') % {'count': MAX_PARTICIPANTS})
    return result


def _ordered_by_seeding(profiles, seeding):
    if seeding == ArenaTournament.SEED_RANDOM:
        profiles = list(profiles)
        SystemRandom().shuffle(profiles)
        return profiles
    if seeding == ArenaTournament.SEED_RATING:
        return sorted(profiles, key=lambda p: (p.rating is None, -(p.rating or 0), p.pk))
    return list(profiles)


def _lock_tournament(tournament_id):
    try:
        return ArenaTournament.objects.select_for_update().get(pk=tournament_id)
    except ArenaTournament.DoesNotExist:
        raise ArenaError(_('That tournament does not exist.'), status=404)


@transaction.atomic
def create_tournament(actor, name, profiles, seeding=ArenaTournament.SEED_MANUAL, third_place=False, rules=None,
                      description='', bank=None):
    name = (name or '').strip()[:100]
    if not name:
        raise ArenaError(_('Give the tournament a name.'))
    if seeding not in dict(ArenaTournament.SEEDING):
        raise ArenaError(_('Invalid seeding.'))
    try:
        rules = arena_rules.clean_rules(rules)
    except ValidationError as error:
        raise ArenaError(error.messages[0])
    profiles = _ordered_by_seeding(_clean_participants(profiles), seeding)
    tournament = ArenaTournament.objects.create(
        name=name, slug=_unique_slug(name), description=(description or '').strip()[:2000], seeding=seeding,
        third_place_match=bool(third_place), rules=rules, bank=bank, created_by=actor,
    )
    ArenaParticipant.objects.bulk_create([ArenaParticipant(tournament=tournament, profile=profile, seed=index)
                                          for index, profile in enumerate(profiles, 1)])
    _sync_rounds(tournament, len(profiles))
    # The bracket is there from the start, so its matches can get their problems before anything else.
    _build_bracket(actor, tournament)
    _log('tournament_create', actor, tournament=tournament, participants=len(profiles))
    ensure_default_screen(actor)
    return tournament


def _sync_rounds(tournament, count):
    """One round per level of the bracket, keeping whatever was configured on rounds that still exist."""
    rounds = round_count(count)
    ArenaRound.objects.filter(tournament=tournament, number__gt=rounds).delete()
    existing = set(ArenaRound.objects.filter(tournament=tournament).values_list('number', flat=True))
    ArenaRound.objects.bulk_create([ArenaRound(tournament=tournament, number=number)
                                    for number in range(1, rounds + 1) if number not in existing])


@transaction.atomic
def update_tournament(actor, tournament_id, version=None, name=None, description=None, rules=None, bank=False,
                      third_place=None, draw=None):
    """`draw` holds how the matches get their problems: by hand, or by themselves from the bank (see rules.py)."""
    tournament = _lock_tournament(tournament_id)
    _check_version(tournament, version)
    fields = []
    if name is not None:
        name = name.strip()[:100]
        if not name:
            raise ArenaError(_('Give the tournament a name.'))
        tournament.name = name
        fields.append('name')
    if description is not None:
        tournament.description = description.strip()[:2000]
        fields.append('description')
    if rules is not None or draw is not None:
        try:
            cleaned = arena_rules.clean_rules(rules) if rules is not None else None
            tournament.rules = arena_rules.with_draw(tournament.rules, cleaned, arena_rules.clean_draw(draw))
        except ValidationError as error:
            raise ArenaError(error.messages[0])
        fields.append('rules')
    if bank is not False:
        tournament.bank = bank
        fields.append('bank')
    rebuild = False
    if third_place is not None and bool(third_place) != tournament.third_place_match:
        if tournament.status != ArenaTournament.DRAFT:
            raise ArenaError(_('The bracket is already set; this can only change in a draft.'))
        tournament.third_place_match = bool(third_place)
        fields.append('third_place_match')
        rebuild = True
    tournament.version += 1
    tournament.save(update_fields=fields + ['version'])
    if rebuild:
        _build_bracket(actor, tournament)
    _log('tournament_update', actor, tournament=tournament, fields=fields)
    return tournament


@transaction.atomic
def set_participants(actor, tournament_id, profiles, seeding=None, version=None):
    tournament = _lock_tournament(tournament_id)
    _check_version(tournament, version)
    if tournament.status != ArenaTournament.DRAFT:
        raise ArenaError(_('Participants can only change while the tournament is a draft.'))
    if seeding is not None:
        if seeding not in dict(ArenaTournament.SEEDING):
            raise ArenaError(_('Invalid seeding.'))
        tournament.seeding = seeding
    profiles = _ordered_by_seeding(_clean_participants(profiles), tournament.seeding)
    ArenaParticipant.objects.filter(tournament=tournament).delete()
    ArenaParticipant.objects.bulk_create([ArenaParticipant(tournament=tournament, profile=profile, seed=index)
                                          for index, profile in enumerate(profiles, 1)])
    _sync_rounds(tournament, len(profiles))
    tournament.version += 1
    tournament.save(update_fields=['seeding', 'version'])
    # Saving the participants rebuilds the bracket at once: there is no separate step to forget.
    _build_bracket(actor, tournament)
    _log('participants', actor, tournament=tournament, participants=len(profiles))
    return tournament


@transaction.atomic
def set_round(actor, tournament_id, number, best_of=None, rules=None, name=None, version=None, draw=None):
    tournament = _lock_tournament(tournament_id)
    _check_version(tournament, version)
    round_obj = ArenaRound.objects.filter(tournament=tournament, number=number).first()
    if round_obj is None:
        raise ArenaError(_('That round does not exist.'), status=404)
    fields = []
    if best_of is not None:
        try:
            best_of = arena_rules.clean_best_of(best_of)
        except ValidationError as error:
            raise ArenaError(error.messages[0])
        started = ArenaGame.objects.filter(match__round=round_obj, contest__isnull=False).exists()
        if started and best_of != round_obj.best_of:
            raise ArenaError(_('This round has already started; change the length of each match instead.'))
        round_obj.best_of = best_of
        fields.append('best_of')
        ArenaMatch.objects.filter(round=round_obj).exclude(status=ArenaMatch.DECIDED).update(best_of=best_of)
    if rules is not None or draw is not None:
        try:
            cleaned = arena_rules.clean_rules(rules) if rules is not None else None
            draw = {key: value for key, value in arena_rules.clean_draw(draw).items() if key != 'auto_draw'}
            round_obj.rules = arena_rules.with_draw(round_obj.rules, cleaned, draw)
        except ValidationError as error:
            raise ArenaError(error.messages[0])
        fields.append('rules')
    if name is not None:
        round_obj.name = name.strip()[:60]
        fields.append('name')
    if fields:
        round_obj.save(update_fields=fields)
    tournament.version += 1
    tournament.save(update_fields=['version'])
    _log('round', actor, tournament=tournament, number=number, fields=fields)
    return round_obj


@transaction.atomic
def generate_bracket(actor, tournament_id, version=None):
    """Build every match of the bracket from the seeds again. Only in a draft; problems already chosen stay."""
    tournament = _lock_tournament(tournament_id)
    _check_version(tournament, version)
    if tournament.status != ArenaTournament.DRAFT:
        raise ArenaError(_('The bracket can only be built while the tournament is a draft.'))
    _build_bracket(actor, tournament)
    return tournament


def _chosen_problems(tournament):
    """Problems already chosen for the games of a draft bracket, by place: (round, position, third place, game)."""
    chosen = {}
    games = ArenaGame.objects.filter(match__tournament=tournament, contest__isnull=True).select_related('match__round')
    for game in games:
        rows = list(ArenaGameProblem.objects.filter(game=game).order_by('order').values_list('problem_id', 'points'))
        if rows and game.match.round_id:
            chosen[(game.match.round.number, game.match.position, game.match.is_third_place, game.number)] = rows
    return chosen


def _build_bracket(actor, tournament):
    """Every match of the bracket from the seeds, for a draft tournament already locked by the caller.

    Building again starts the matches over, but the problems an organizer already chose stay with their place in
    the bracket (round, position, game), so changing the participants does not undo that work.
    """
    participants = list(ArenaParticipant.objects.filter(tournament=tournament).select_related('profile')
                        .order_by('seed'))
    count = len(participants)
    if count < MIN_PARTICIPANTS:
        raise ArenaError(_('A tournament needs at least %(count)s participants.') % {'count': MIN_PARTICIPANTS})
    chosen = _chosen_problems(tournament)
    # Games point to their match with PROTECT: the prepared ones go first (in a draft none has been played).
    ArenaGame.objects.filter(match__tournament=tournament, contest__isnull=True).delete()
    ArenaMatch.objects.filter(tournament=tournament).delete()
    _sync_rounds(tournament, count)
    rounds = {r.number: r for r in ArenaRound.objects.filter(tournament=tournament)}
    total_rounds = len(rounds)
    size = bracket_size(count)

    # Build from the final backwards so every match already knows where its winner goes.
    by_round = {}
    for number in range(total_rounds, 0, -1):
        matches = []
        for position in range(size >> number):
            following = by_round.get(number + 1)
            next_match = following[position // 2] if following else None
            matches.append(ArenaMatch.objects.create(
                tournament=tournament, round=rounds[number], position=position, best_of=rounds[number].best_of,
                next_match=next_match, next_slot=(SLOT_A if position % 2 == 0 else SLOT_B) if next_match else '',
                status=ArenaMatch.PENDING, created_by=actor,
            ))
        by_round[number] = matches
    # With three players one semifinal is a bye: it has no loser, so a third place match would wait forever for its
    # second player and the tournament would never finish. From four on, both semifinals are played.
    if tournament.third_place_match and total_rounds >= 2 and count >= 4:
        third = ArenaMatch.objects.create(
            tournament=tournament, round=rounds[total_rounds], position=1, is_third_place=True,
            best_of=rounds[total_rounds].best_of, status=ArenaMatch.PENDING, created_by=actor,
        )
        for position, semifinal in enumerate(by_round[total_rounds - 1]):
            semifinal.loser_next_match = third
            semifinal.loser_next_slot = SLOT_A if position == 0 else SLOT_B
            semifinal.save(update_fields=['loser_next_match', 'loser_next_slot'])

    seeds = {participant.seed: participant.profile for participant in participants}
    order = seeding_order(size)
    now = timezone.now()
    for position, match in enumerate(by_round[1]):
        seed_a, seed_b = order[2 * position], order[2 * position + 1]
        match.player_a, match.seed_a = seeds.get(seed_a), seed_a if seed_a in seeds else None
        match.player_b, match.seed_b = seeds.get(seed_b), seed_b if seed_b in seeds else None
        if match.player_a and match.player_b:
            match.status = ArenaMatch.READY
            match.save(update_fields=['player_a', 'seed_a', 'player_b', 'seed_b', 'status'])
        else:
            match.save(update_fields=['player_a', 'seed_a', 'player_b', 'seed_b'])
    # Byes: the lone player goes straight through.
    for match in by_round[1]:
        if match.status == ArenaMatch.PENDING:
            match = lock_match(match.pk)
            slot = SLOT_A if match.player_a_id else SLOT_B
            match.status = ArenaMatch.DECIDED
            match.winner_slot = slot
            match.decided_reason = 'bye'
            match.decided_at = now
            _bump(match, 'status', 'winner_slot', 'decided_reason', 'decided_at')
            advance(match, now)
    if chosen:
        for match in ArenaMatch.objects.filter(tournament=tournament).exclude(status=ArenaMatch.DECIDED) \
                .select_related('round'):
            numbers = sorted(number for (round_number, position, third, number) in chosen
                             if (round_number, position, third) == (match.round.number, match.position,
                                                                    match.is_third_place))
            for number in range(1, max(numbers, default=0) + 1):
                game = ArenaGame.objects.create(match=match, number=number)
                rows = chosen.get((match.round.number, match.position, match.is_third_place, number), [])
                ArenaGameProblem.objects.bulk_create([ArenaGameProblem(game=game, problem_id=problem_id, order=order,
                                                                       points=points)
                                                      for order, (problem_id, points) in enumerate(rows)])
    tournament.version += 1
    tournament.save(update_fields=['version'])
    _log('bracket', actor, tournament=tournament, participants=count, size=size)
    return tournament


@transaction.atomic
def start_tournament(actor, tournament_id, version=None):
    tournament = _lock_tournament(tournament_id)
    _check_version(tournament, version)
    if tournament.status != ArenaTournament.DRAFT:
        raise ArenaError(_('This tournament has already started.'))
    if not ArenaMatch.objects.filter(tournament=tournament).exists():
        raise ArenaError(_('Build the bracket before starting.'))
    tournament.status = ArenaTournament.RUNNING
    tournament.started_at = timezone.now()
    tournament.version += 1
    tournament.save(update_fields=['status', 'started_at', 'version'])
    _log('tournament_start', actor, tournament=tournament)
    return tournament


@transaction.atomic
def archive_tournament(actor, tournament_id, version=None):
    tournament = _lock_tournament(tournament_id)
    _check_version(tournament, version)
    if ArenaMatch.objects.filter(tournament=tournament, status__in=ArenaMatch.LOCKING_STATUSES).exists():
        raise ArenaError(_('Finish or decide the matches being played first.'))
    tournament.status = ArenaTournament.ARCHIVED
    tournament.version += 1
    tournament.save(update_fields=['status', 'version'])
    _log('tournament_archive', actor, tournament=tournament)
    return tournament


def _place(match_id, slot, profile, seed):
    target = lock_match(match_id)
    if ArenaGame.objects.filter(match=target, contest__isnull=False).exists():
        raise ArenaError(_('The next match has already been played; it cannot receive another player.'))
    setattr(target, 'player_' + slot, profile)
    setattr(target, 'seed_' + slot, seed)
    fields = ['player_' + slot, 'seed_' + slot]
    if target.player_a_id and target.player_b_id and target.status == ArenaMatch.PENDING:
        target.status = ArenaMatch.READY
        fields.append('status')
    elif not (target.player_a_id and target.player_b_id) and target.status == ArenaMatch.READY:
        target.status = ArenaMatch.PENDING
        fields.append('status')
    _bump(target, *fields)
    return target


def advance(match, now=None):
    """Send the winner (and, for a semifinal with a third place match, the loser) to their next match."""
    if match.tournament_id is None or not match.winner_slot:
        return
    now = now or timezone.now()
    winner_slot = match.winner_slot
    loser_slot = SLOT_B if winner_slot == SLOT_A else SLOT_A
    if match.next_match_id:
        _place(match.next_match_id, match.next_slot, match.player(winner_slot),
               getattr(match, 'seed_' + winner_slot))
    if match.loser_next_match_id and match.player_id(loser_slot):
        _place(match.loser_next_match_id, match.loser_next_slot, match.player(loser_slot),
               getattr(match, 'seed_' + loser_slot))
    _check_finished(match.tournament_id, now)


def _check_finished(tournament_id, now):
    tournament = ArenaTournament.objects.select_for_update().get(pk=tournament_id)
    if tournament.status != ArenaTournament.RUNNING:
        return
    final = ArenaMatch.objects.filter(tournament=tournament, next_match__isnull=True, is_third_place=False) \
        .order_by('-round__number').first()
    third = ArenaMatch.objects.filter(tournament=tournament, is_third_place=True).first()
    if final is not None and final.status == ArenaMatch.DECIDED and \
            (third is None or third.status in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED)):
        tournament.status = ArenaTournament.FINISHED
        tournament.finished_at = now
        tournament.version += 1
        tournament.save(update_fields=['status', 'finished_at', 'version'])
        _log('tournament_finish', None, tournament=tournament)


@transaction.atomic
def set_winner(actor, match_id, slot, version=None):
    """Correct the winner of a decided tournament match, as long as the next match has not been played."""
    if slot not in (SLOT_A, SLOT_B):
        raise ArenaError(_('Choose a winner.'))
    match = lock_match(match_id)
    _check_version(match, version)
    if match.status != ArenaMatch.DECIDED or match.winner_slot == slot:
        raise ArenaError(_('Only the winner of a decided match can be corrected.'))
    if not match.player_id(slot):
        raise ArenaError(_('That player is not set yet.'))
    for target_id in (match.next_match_id, match.loser_next_match_id):
        if target_id and ArenaGame.objects.filter(match_id=target_id, contest__isnull=False).exists():
            raise ArenaError(_('The next match has already been played; it cannot receive another player.'))
    match.winner_slot = slot
    match.decided_reason = 'organizer'
    match.decided_at = timezone.now()
    _bump(match, 'winner_slot', 'decided_reason', 'decided_at')
    tournament = ArenaTournament.objects.select_for_update().get(pk=match.tournament_id) \
        if match.tournament_id else None
    if tournament is not None and tournament.status == ArenaTournament.FINISHED:
        tournament.status = ArenaTournament.RUNNING
        tournament.finished_at = None
        tournament.save(update_fields=['status', 'finished_at'])
    advance(match)
    _log('set_winner', actor, match, winner=slot)
    refresh_lock_on_commit()
    return match


# ---------------------------------------------------------------------------------------------------------------
# Problem banks
# ---------------------------------------------------------------------------------------------------------------

@transaction.atomic
def create_bank(actor, name, description=''):
    name = (name or '').strip()[:100]
    if not name:
        raise ArenaError(_('Give the bank a name.'))
    return ArenaProblemBank.objects.create(name=name, description=(description or '').strip()[:2000],
                                           created_by=actor)


@transaction.atomic
def set_bank_problems(actor, bank_id, entries):
    """Replace the problems of a bank. `entries` is a list of {id, level, note}.

    The problems already in the bank that `actor` cannot see are not shown to them (views/arena_manage.py,
    manage_banks), so they never come back in `entries`: they are kept as they are, after the others."""
    bank = ArenaProblemBank.objects.select_for_update().filter(pk=bank_id).first()
    if bank is None:
        raise ArenaError(_('That bank does not exist.'), status=404)
    if not isinstance(entries, list) or len(entries) > 500:
        raise ArenaError(_('Invalid list of problems.'))
    rows, ids = [], []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ArenaError(_('Invalid list of problems.'))
        problem_id = entry.get('id')
        if isinstance(problem_id, str) and problem_id.isdigit():
            problem_id = int(problem_id)
        if isinstance(problem_id, bool) or not isinstance(problem_id, int):
            raise ArenaError(_('Invalid list of problems.'))
        level = entry.get('level')
        if level in ('', None):
            level = None
        else:
            if isinstance(level, str) and level.isdigit():
                level = int(level)
            if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 5:
                raise ArenaError(_('The level must be between 1 and 5.'))
        note = entry.get('note') or ''
        if not isinstance(note, str):
            raise ArenaError(_('Invalid list of problems.'))
        ids.append(problem_id)
        rows.append((problem_id, level, note.strip()[:200], index))
    if len(set(ids)) != len(ids):
        raise ArenaError(_('The same problem cannot appear twice in a bank.'))
    existing = list(ArenaBankProblem.objects.filter(bank=bank))
    visible = set(visible_problems(actor.user).filter(id__in=ids + [entry.problem_id for entry in existing])
                  .values_list('id', flat=True))
    if not visible.issuperset(ids):
        raise ArenaError(_('One of the problems does not exist or you cannot see it.'))
    kept = [entry for entry in existing if entry.problem_id not in visible]
    ArenaBankProblem.objects.filter(bank=bank).delete()
    ArenaBankProblem.objects.bulk_create([ArenaBankProblem(bank=bank, problem_id=problem_id, level=level, note=note,
                                                           order=order)
                                          for problem_id, level, note, order in rows] +
                                         [ArenaBankProblem(bank=bank, problem_id=entry.problem_id, level=entry.level,
                                                           note=entry.note, order=len(rows) + index)
                                          for index, entry in enumerate(kept)])
    return bank


def draw_problems(actor, match, game, count=1, level_min=None, level_max=None):
    """Propose problems for a game from the tournament bank. Nothing is saved: the organizer confirms or edits.

    Never repeats a problem already used in the tournament, prefers problems neither player has solved or tried,
    and orders the proposal by level. Returns (problems, warnings).
    """
    bank = match.tournament.bank if match.tournament_id else None
    if bank is None:
        raise ArenaError(_('This battle has no problem bank to draw from.'))
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, 5))
    entries = list(ArenaBankProblem.objects.filter(bank=bank).select_related('problem'))
    if level_min:
        entries = [entry for entry in entries if entry.level and entry.level >= level_min]
    if level_max:
        entries = [entry for entry in entries if entry.level and entry.level <= level_max]
    # Drawn by the tournament itself (actor None), every problem of the bank counts: an organizer who could see
    # them put them there.
    ids = [entry.problem_id for entry in entries]
    visible = set(ids) if actor is None else set(visible_problems(actor.user).filter(id__in=ids)
                                                 .values_list('id', flat=True))
    used = problems_used_in_tournament(match.tournament, exclude_game=game) | \
        set(ArenaGameProblem.objects.filter(game__match=match).exclude(game=game).values_list('problem_id', flat=True))
    candidates = [entry for entry in entries if entry.problem_id in visible and entry.problem_id not in used]
    players = [pk for pk in (match.player_a_id, match.player_b_id) if pk]
    touched = set(Submission.objects.filter(user_id__in=players, problem_id__in=[e.problem_id for e in candidates])
                  .values_list('problem_id', flat=True))
    fresh = [entry for entry in candidates if entry.problem_id not in touched]
    warnings = []
    pool = fresh
    if len(fresh) < count:
        pool = candidates
        if candidates:
            warnings.append(_('Not enough problems neither player has tried; some of the proposal may be known.'))
    if len(pool) < count:
        raise ArenaError(_('The bank does not have enough unused problems for this game.'))
    chosen = SystemRandom().sample(pool, count)
    chosen.sort(key=lambda entry: (entry.level or 0, entry.order))
    return [entry.problem for entry in chosen], warnings


def draw_settings(match):
    """How a tournament match gets its problems: {'auto', 'bank', 'count', 'level_min', 'level_max'}.

    'auto' is on when the tournament gives problems from its bank by itself. The round can change how many
    problems each game has and which difficulties to draw from; the tournament gives the defaults.
    """
    tournament = match.tournament if match.tournament_id else None
    if tournament is None:
        return {'auto': False, 'bank': None, 'count': 1, 'level_min': None, 'level_max': None}
    stored = tournament.rules or {}
    round_rules = (match.round.rules if match.round_id else None) or {}
    return {
        'auto': bool(stored.get('auto_draw')) and tournament.bank_id is not None,
        'bank': tournament.bank_id,
        'count': round_rules.get('draw_count') or stored.get('draw_count') or 1,
        'level_min': round_rules.get('level_min'),
        'level_max': round_rules.get('level_max'),
    }


def fill_from_bank(match, game, actor=None, force=False):
    """Give `game` its problems from the tournament bank, if the tournament does that by itself (or `force`) and
    the game has none yet. Problems an organizer chose by hand are never replaced.

    Called with the match already locked. Returns True when problems were given. Never raises: a bank without
    enough unused problems leaves the game empty, the log says why, and the panel asks for them by hand.
    """
    if match.tournament_id is None or game is None or game.status != ArenaGame.PREPARED:
        return False
    settings = draw_settings(match)
    if not settings['bank'] or not (settings['auto'] or force):
        return False
    if ArenaGameProblem.objects.filter(game=game).exists():
        return False
    try:
        with transaction.atomic():
            problems, warnings = draw_problems(None, match, game, settings['count'], settings['level_min'],
                                               settings['level_max'])
            ArenaGameProblem.objects.bulk_create([
                ArenaGameProblem(game=game, problem=problem, order=index, points=arena_rules.DEFAULT_POINTS)
                for index, problem in enumerate(problems)])
            _log('bank_problems', actor, match, game, problems=[problem.code for problem in problems],
                 warnings=[str(warning) for warning in warnings])
        return True
    except ArenaError as error:
        _log('bank_empty', actor, match, game, reason=error.message)
        return False
    except Exception:
        # Drawing is a convenience: whatever goes wrong, the game simply waits for problems chosen by hand.
        logger.warning('Arena: drawing problems from the bank failed for match %s.', match.pk, exc_info=True)
        return False


def fill_ready_matches(actor, tournament_id):
    """Give the next game of every match whose players are known, and that has no problems yet, its problems from
    the bank. Returns how many games got problems. Each match in its own transaction, under its own lock."""
    tournament = ArenaTournament.objects.filter(pk=tournament_id).first()
    if tournament is None:
        raise ArenaError(_('That tournament does not exist.'), status=404)
    if tournament.bank_id is None:
        raise ArenaError(_('Choose a problem bank for the tournament first.'))
    filled = 0
    ids = ArenaMatch.objects.filter(tournament=tournament, status__in=(ArenaMatch.READY, ArenaMatch.CALLED,
                                                                         ArenaMatch.PLAYING)) \
        .order_by('round__number', 'position').values_list('id', flat=True)
    from judge.arena.engine import _ensure_next_game
    for match_id in ids:
        with transaction.atomic():
            match = lock_match(match_id)
            if ArenaGame.objects.filter(match=match, status__in=ArenaGame.ACTIVE_STATUSES).exists():
                continue
            if fill_from_bank(match, _ensure_next_game(match), actor, force=True):
                _bump(match)
                filled += 1
    return filled


__all__ = ['seeding_order', 'bracket_size', 'round_count', 'default_round_name', 'round_name', 'create_tournament',
           'update_tournament', 'set_participants', 'set_round', 'generate_bracket', 'start_tournament',
           'archive_tournament', 'advance', 'set_winner', 'create_bank', 'set_bank_problems', 'draw_problems']

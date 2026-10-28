"""The ranking of the duels (2026-10-26): Glicko-2 over the games of the battles that count.

Who decides: whoever has `judge.decide_arena_ranking` (access.decides_ranking), never just for organizing. Every
tournament and quick battle starts not decided yet and only counts once somebody marks it «counts»; a battle of a
tournament goes with its tournament and can only be left out. Some battles never count, however they are marked
(`never_reason`): cancelled ones, byes, battles won without playing on and battles played with a typed name on a club
laptop. Inside a battle that counts, every finished game with a winner counts, also the ones the organizer gave; ties
and voided games do not. An unlisted account (also every cancelled one) only leaves the table: its battles still count
for its rivals, so cancelling an account never changes a published rating.

How it counts: Glicko-2 (Glickman, 2012). Every battle is a rating period of its own for its two players, and each
game that counts is one result against the rating the rival had before the battle. Before a battle the deviation
of each player grows for every whole week since their last game that counted, up to where a newcomer starts. 1500
±350, volatility 0.06, τ 0.5. Correcting the winner of a series changes nothing: only games count.

Nothing is stored that cannot be worked out again. `recompute()` goes through every battle that counts, in the order
of its last game that counted, and leaves ArenaRating and ArenaRatingChange exactly as the result, writing only what
changed, so deciding late, leaving an old battle out or correcting one gives the same ratings as if it had been so
from the start. It runs after anything that can move a rating commits (a battle decided, a decision, a winner
corrected), one at a time (a named lock of MariaDB). A failure is logged and never undoes the battle: the page to
decide says when the ranking is not up to date and puts it right, and so does `manage.py rate_duels`.
"""
import logging
import math
from contextlib import contextmanager
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from judge.arena.engine import ArenaError, _log, arena_setting, lock_match
from judge.jinja2.gravatar import gravatar
from judge.models.arena import ArenaGame, ArenaMatch, ArenaRating, ArenaRatingChange, ArenaTournament, \
    RANKING_COUNTS, RANKING_EXCLUDED, RANKING_PENDING, SLOT_A, SLOT_B

logger = logging.getLogger('judge.arena')

SCALE = 173.7178
START_RATING = 1500.0
START_DEVIATION = 350.0
START_VOLATILITY = 0.06
TAU = 0.5
EPSILON = 1e-6
PERIOD = timedelta(days=7)
LOCK_NAME = 'cpc_arena_rating'
LOCK_SECONDS = 10
VALUES = (RANKING_PENDING, RANKING_COUNTS, RANKING_EXCLUDED)
# Why a battle never counts, whatever is decided about it.
NEVER = ('cancelled', 'bye', 'walkover', 'guest')

__all__ = ['provisional_games', 'inactive_days', 'rate', 'grown_deviation', 'never_reason', 'decision', 'counts_q',
           'compute', 'check', 'recompute', 'recompute_on_commit', 'after_decided', 'set_tournament_ranking',
           'set_match_ranking', 'battle_summary', 'table', 'top', 'pending_count', 'ratings_of', 'games_by_match',
           'never_texts', 'RankingBusy']


def provisional_games():
    """Until this many games count, a player shows «?» instead of a number (the private file may set it)."""
    return arena_setting('CPC_ARENA_PROVISIONAL_GAMES', 3, low=1, high=100)


def inactive_days():
    """After this long without a game that counts, a player leaves the main table (the private file may set it)."""
    return arena_setting('CPC_ARENA_INACTIVE_DAYS', 365, low=30, high=3650)


# ---------------------------------------------------------------------------------------------------------------
# Glicko-2
# ---------------------------------------------------------------------------------------------------------------

def _g(phi):
    return 1 / math.sqrt(1 + 3 * phi * phi / math.pi ** 2)


def _expected(mu, mu_j, phi_j):
    return 1 / (1 + math.exp(-_g(phi_j) * (mu - mu_j)))


def rate(rating, deviation, volatility, results):
    """One rating period of Glicko-2. `results`: [(rating of the rival, their deviation, 1 or 0)]. Returns the new
    (rating, deviation, volatility); with no results only the deviation grows."""
    mu, phi = (rating - START_RATING) / SCALE, deviation / SCALE
    if not results:
        return rating, min(START_DEVIATION, math.sqrt(phi * phi + volatility * volatility) * SCALE), volatility
    v_inv, delta_sum = 0.0, 0.0
    for rating_j, deviation_j, score in results:
        mu_j, phi_j = (rating_j - START_RATING) / SCALE, deviation_j / SCALE
        expected = _expected(mu, mu_j, phi_j)
        v_inv += _g(phi_j) ** 2 * expected * (1 - expected)
        delta_sum += _g(phi_j) * (score - expected)
    v = 1 / v_inv
    delta = v * delta_sum
    a = math.log(volatility * volatility)

    def f(x):
        ex = math.exp(x)
        return ex * (delta * delta - phi * phi - v - ex) / (2 * (phi * phi + v + ex) ** 2) - (x - a) / TAU ** 2

    big_a = a
    if delta * delta > phi * phi + v:
        big_b = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        big_b = a - k * TAU
    fa, fb = f(big_a), f(big_b)
    while abs(big_b - big_a) > EPSILON:
        c = big_a + (big_a - big_b) * fa / (fb - fa)
        fc = f(c)
        if fc * fb <= 0:
            big_a, fa = big_b, fb
        else:
            fa /= 2
        big_b, fb = c, fc
    new_volatility = math.exp(big_a / 2)
    phi_star = math.sqrt(phi * phi + new_volatility * new_volatility)
    new_phi = 1 / math.sqrt(1 / phi_star ** 2 + 1 / v)
    new_mu = mu + new_phi ** 2 * delta_sum
    return new_mu * SCALE + START_RATING, new_phi * SCALE, new_volatility


def grown_deviation(deviation, volatility, weeks):
    """The deviation after `weeks` whole weeks without a game that counts: Glicko-2 with no results, once a week,
    never above where a newcomer starts."""
    if weeks <= 0:
        return deviation
    phi = deviation / SCALE
    return min(START_DEVIATION, math.sqrt(phi * phi + weeks * volatility * volatility) * SCALE)


# ---------------------------------------------------------------------------------------------------------------
# What counts
# ---------------------------------------------------------------------------------------------------------------

def never_reason(match):
    """Why `match` can never count, whatever is decided about it, or None if it can."""
    if match.status == ArenaMatch.CANCELLED:
        return 'cancelled'
    if match.decided_reason == 'bye':
        return 'bye'
    if match.decided_reason == 'walkover':
        return 'walkover'
    if match.display_name_a or match.display_name_b:
        return 'guest'
    return None


def never_texts():
    """Why a battle never counts, in words, for every page that says so."""
    return {
        'cancelled': _('Cancelled: it never counts.'),
        'bye': _('A bye, nobody to play against: it never counts.'),
        'walkover': _('Won without playing on: it never counts.'),
        'guest': _('Played on a club laptop with a typed name: it never counts.'),
    }


def decision(match):
    """What was decided about `match`: a quick battle by itself; a battle of a tournament as its tournament, unless
    it was left out."""
    if match.tournament_id is None:
        return match.ranking
    if match.ranking == RANKING_EXCLUDED:
        return RANKING_EXCLUDED
    return match.tournament.ranking


def counts_q(prefix=''):
    """The battles that count, as a filter of ArenaMatch (or through `prefix`, of something pointing to one)."""
    p = prefix
    decided = Q(**{p + 'tournament__isnull': True, p + 'ranking': RANKING_COUNTS}) | \
        (Q(**{p + 'tournament__ranking': RANKING_COUNTS}) & ~Q(**{p + 'ranking': RANKING_EXCLUDED}))
    return decided & Q(**{p + 'status': ArenaMatch.DECIDED, p + 'player_a__isnull': False,
                          p + 'player_b__isnull': False, p + 'display_name_a': '', p + 'display_name_b': ''}) & \
        ~Q(**{p + 'decided_reason__in': ('bye', 'walkover')})


# ---------------------------------------------------------------------------------------------------------------
# Working it out
# ---------------------------------------------------------------------------------------------------------------

def _battles():
    """[(match id, player A, player B, [winner slot of every game that counts], time)] in the order they count: by
    the end of their last game that counted, then by id. Battles without any such game change nothing."""
    games = ArenaGame.objects.filter(counts_q('match__'), status=ArenaGame.FINISHED,
                                     winner_slot__in=(SLOT_A, SLOT_B)) \
        .values_list('match_id', 'match__player_a_id', 'match__player_b_id', 'number', 'winner_slot', 'finished_at') \
        .order_by('match_id', 'number')
    battles = {}
    for match_id, a, b, _number, winner, finished_at in games:
        battle = battles.setdefault(match_id, [match_id, a, b, [], finished_at])
        battle[3].append(winner)
        if finished_at is not None and (battle[4] is None or finished_at > battle[4]):
            battle[4] = finished_at
    ordered = [tuple(battle) for battle in battles.values() if battle[4] is not None]
    ordered.sort(key=lambda battle: (battle[4], battle[0]))
    return ordered


def compute():
    """The whole ranking from the start: ({profile id: rating fields}, {(match id, profile id): change fields})."""
    players, changes = {}, {}
    for match_id, a, b, winners, time in _battles():
        before = {}
        for profile_id in (a, b):
            state = players.get(profile_id)
            if state is None:
                before[profile_id] = (START_RATING, START_DEVIATION, START_VOLATILITY)
            else:
                weeks = int((time - state['last_played']) / PERIOD)
                before[profile_id] = (state['rating'], grown_deviation(state['deviation'], state['volatility'], weeks),
                                      state['volatility'])
        for profile_id, rival_id, slot in ((a, b, SLOT_A), (b, a, SLOT_B)):
            rival = before[rival_id]
            results = [(rival[0], rival[1], 1.0 if winner == slot else 0.0) for winner in winners]
            rating, deviation, volatility = rate(*before[profile_id], results)
            wins = sum(1 for winner in winners if winner == slot)
            state = players.setdefault(profile_id, {'games': 0, 'wins': 0, 'battles': 0})
            state.update(rating=rating, deviation=deviation, volatility=volatility, last_played=time,
                         games=state['games'] + len(winners), wins=state['wins'] + wins,
                         battles=state['battles'] + 1)
            changes[(match_id, profile_id)] = {
                'time': time, 'rating_before': before[profile_id][0], 'rating_after': rating,
                'deviation_after': deviation, 'volatility_after': volatility, 'games': len(winners), 'wins': wins,
                'games_total': state['games'],
            }
    return players, changes


RATING_FIELDS = ('rating', 'deviation', 'volatility', 'games', 'wins', 'battles', 'last_played')
CHANGE_FIELDS = ('time', 'rating_before', 'rating_after', 'deviation_after', 'volatility_after', 'games', 'wins',
                 'games_total')


def _same(stored, computed, fields):
    for field in fields:
        old, new = getattr(stored, field), computed[field]
        if isinstance(new, float):
            if old is None or abs(old - new) > 1e-7:
                return False
        elif old != new:
            return False
    return True


def _differences(players, changes):
    """What is stored that is not what `compute()` gave: ([ratings to write], [changes to write], [rating ids to
    delete], [change ids to delete])."""
    stored_ratings = {row.profile_id: row for row in ArenaRating.objects.all()}
    stored_changes = {(row.match_id, row.profile_id): row for row in ArenaRatingChange.objects.all()}
    ratings = [(stored_ratings.get(pid), pid, values) for pid, values in players.items()
               if pid not in stored_ratings or not _same(stored_ratings[pid], values, RATING_FIELDS)]
    rows = [(stored_changes.get(key), key, values) for key, values in changes.items()
            if key not in stored_changes or not _same(stored_changes[key], values, CHANGE_FIELDS)]
    gone_ratings = [row.pk for pid, row in stored_ratings.items() if pid not in players]
    gone_changes = [row.pk for key, row in stored_changes.items() if key not in changes]
    return ratings, rows, gone_ratings, gone_changes


def check():
    """How many stored rows are not what the battles give; 0 means the ranking is up to date. Only reads."""
    ratings, rows, gone_ratings, gone_changes = _differences(*compute())
    return len(ratings) + len(rows) + len(gone_ratings) + len(gone_changes)


class RankingBusy(Exception):
    pass


@contextmanager
def _one_at_a_time():
    """Only one recompute at a time, across every process: a named lock of MariaDB, taken before the transaction so
    that what is read afterwards is everything committed until then."""
    if connection.vendor != 'mysql':
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute('SELECT GET_LOCK(%s, %s)', [LOCK_NAME, LOCK_SECONDS])
        got = cursor.fetchone()[0]
    if got != 1:
        raise RankingBusy()
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute('SELECT RELEASE_LOCK(%s)', [LOCK_NAME])


def recompute():
    """Leave the stored ranking exactly as the battles give it. Returns how many rows it wrote or deleted."""
    with _one_at_a_time():
        with transaction.atomic():
            players, changes = compute()
            ratings, rows, gone_ratings, gone_changes = _differences(players, changes)
            ArenaRatingChange.objects.filter(pk__in=gone_changes).delete()
            ArenaRating.objects.filter(pk__in=gone_ratings).delete()
            for stored, profile_id, values in ratings:
                if stored is None:
                    ArenaRating.objects.create(profile_id=profile_id, **values)
                else:
                    ArenaRating.objects.filter(pk=stored.pk).update(**values)
            for stored, (match_id, profile_id), values in rows:
                if stored is None:
                    ArenaRatingChange.objects.create(match_id=match_id, profile_id=profile_id, **values)
                else:
                    ArenaRatingChange.objects.filter(pk=stored.pk).update(**values)
    return len(ratings) + len(rows) + len(gone_ratings) + len(gone_changes)


def _recompute_safely():
    try:
        recompute()
    except Exception:
        # The battle stays as it was decided; the page to decide shows that the ranking is behind.
        logger.exception('The ranking of the duels could not be worked out again')


def recompute_on_commit():
    transaction.on_commit(_recompute_safely)


def after_decided(match):
    """A battle was decided, or its winner corrected: if it counts, the ranking is worked out again once that is
    saved. A battle that does not count (yet) changes nothing."""
    if decision(match) == RANKING_COUNTS and never_reason(match) is None:
        recompute_on_commit()


# ---------------------------------------------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------------------------------------------

def _clean(value):
    if value not in VALUES:
        raise ArenaError(_('Invalid decision.'))
    return value


@transaction.atomic
def set_tournament_ranking(actor, tournament_id, value):
    """Whether every battle of a tournament counts. Its version does not move: deciding the ranking never gets in
    the way of whoever is running the tournament."""
    value = _clean(value)
    tournament = ArenaTournament.objects.select_for_update().filter(pk=tournament_id).first()
    if tournament is None:
        raise ArenaError(_('That tournament does not exist.'), status=404)
    before = tournament.ranking
    if value != before:
        tournament.ranking = value
        tournament.save(update_fields=['ranking'])
        _log('ranking', actor, tournament=tournament, ranking=value, before=before)
        recompute_on_commit()
    return tournament


@transaction.atomic
def set_match_ranking(actor, match_id, value):
    """Whether a quick battle counts; for a battle of a tournament, only whether it is left out ('excluded') or goes
    with its tournament ('pending'). Its version does not move either."""
    value = _clean(value)
    match = lock_match(match_id)
    if match.tournament_id and value == RANKING_COUNTS:
        raise ArenaError(_('A battle of a tournament counts when its tournament counts; it can only be left out.'))
    before = match.ranking
    if value != before:
        match.ranking = value
        match.save(update_fields=['ranking'])
        _log('ranking', actor, match, ranking=value, before=before)
        recompute_on_commit()
    return match


# ---------------------------------------------------------------------------------------------------------------
# What pages show
# ---------------------------------------------------------------------------------------------------------------

def battle_summary(match, profile_id):
    """What the result of a battle says about the ranking, for one of its players."""
    reason = never_reason(match)
    if reason is not None:
        return {'status': 'never', 'reason': reason}
    current = decision(match)
    if current != RANKING_COUNTS:
        return {'status': current}
    change = ArenaRatingChange.objects.filter(match=match, profile_id=profile_id).first()
    if change is None:
        # Nothing to count (no game ended with a winner), or still being worked out.
        return {'status': 'counts', 'change': None}
    needed = provisional_games()
    before, after = round(change.rating_before), round(change.rating_after)
    return {'status': 'counts', 'change': after - before, 'rating': after, 'games': change.games_total,
            'needed': needed, 'provisional': change.games_total < needed}


def ratings_of(profile_ids):
    return dict(ArenaRating.objects.filter(profile_id__in=list(profile_ids)).values_list('profile_id', 'rating'))


def _palmares():
    """{profile id: [titles, finals lost, third places]} from the finished tournaments that count and are shown in
    the history. A tournament without a match for third place gives it to both losers of the semifinals."""
    tournaments = ArenaTournament.objects.filter(ranking=RANKING_COUNTS, show_in_history=True,
                                                 status__in=(ArenaTournament.FINISHED, ArenaTournament.ARCHIVED))
    matches = list(ArenaMatch.objects.filter(tournament__in=tournaments, status=ArenaMatch.DECIDED)
                   .only('id', 'tournament_id', 'next_match_id', 'is_third_place', 'winner_slot', 'player_a_id',
                         'player_b_id', 'decided_reason'))
    by_tournament = {}
    for match in matches:
        by_tournament.setdefault(match.tournament_id, []).append(match)
    result = {}

    def add(profile_id, index):
        if profile_id:
            result.setdefault(profile_id, [0, 0, 0])[index] += 1

    for rows in by_tournament.values():
        final = next((m for m in rows if m.next_match_id is None and not m.is_third_place), None)
        if final is None or not final.winner_slot:
            continue
        loser = SLOT_B if final.winner_slot == SLOT_A else SLOT_A
        add(final.player_id(final.winner_slot), 0)
        add(final.player_id(loser), 1)
        third = next((m for m in rows if m.is_third_place), None)
        if third is not None:
            if third.winner_slot:
                add(third.player_id(third.winner_slot), 2)
        else:
            for semi in rows:
                if semi.next_match_id == final.pk and semi.winner_slot and semi.decided_reason != 'bye':
                    add(semi.player_id(SLOT_B if semi.winner_slot == SLOT_A else SLOT_A), 2)
    return result


def table(now=None):
    """The page of the ranking: who has a fixed rating, by rating, with shared places for the same number; who does
    not yet (fewer games than provisional_games()); and who has not played for inactive_days(). Unlisted and closed
    accounts (a cancelled one is both) are left out of it, though their battles count for their rivals."""
    now = now or timezone.now()
    needed = provisional_games()
    cutoff = now - timedelta(days=inactive_days())
    palmares = _palmares()
    ranked, provisional, inactive = [], [], []
    rows = ArenaRating.objects.filter(profile__is_unlisted=False, profile__user__is_active=True) \
        .select_related('profile__user')
    for row in rows:
        entry = {'profile': row.profile, 'avatar': gravatar(row.profile, 64), 'rating': round(row.rating),
                 'games': row.games, 'wins': row.wins,
                 'losses': row.games - row.wins, 'battles': row.battles, 'last_played': row.last_played,
                 'palmares': palmares.get(row.profile_id, [0, 0, 0]), 'needed': needed}
        if row.games < needed:
            provisional.append(entry)
        elif row.last_played < cutoff:
            inactive.append(entry)
        else:
            ranked.append(entry)
    ranked.sort(key=lambda e: (-e['rating'], -e['games'], e['profile'].username.lower()))
    for index, entry in enumerate(ranked):
        same = index and ranked[index - 1]['rating'] == entry['rating']
        entry['place'] = ranked[index - 1]['place'] if same else index + 1
    provisional.sort(key=lambda e: (-e['games'], e['profile'].username.lower()))
    inactive.sort(key=lambda e: (-e['rating'], e['profile'].username.lower()))
    return {'ranked': ranked, 'provisional': provisional, 'inactive': inactive, 'needed': needed,
            'inactive_days': inactive_days()}


def top(count=5, now=None):
    return table(now)['ranked'][:count]


def pending_count():
    """Tournaments and quick battles still to decide that could count: what the page to decide starts with."""
    tournaments = ArenaTournament.objects.filter(ranking=RANKING_PENDING).count()
    quick = ArenaMatch.objects.filter(tournament__isnull=True, ranking=RANKING_PENDING, display_name_a='',
                                      display_name_b='').exclude(status=ArenaMatch.CANCELLED) \
        .exclude(decided_reason='walkover').count()
    return tournaments + quick


def games_by_match(match_ids):
    """{match id: games finished with a winner}, for the page to decide."""
    return dict(ArenaGame.objects.filter(match_id__in=list(match_ids), status=ArenaGame.FINISHED,
                                         winner_slot__in=(SLOT_A, SLOT_B))
                .values('match_id').annotate(n=Count('id')).values_list('match_id', 'n'))

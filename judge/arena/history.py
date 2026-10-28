"""The public history of the duel tournaments (2026-10-25): who played whom, how each battle and each game ended, the
podium and the final standings. Read by judge/views/arena_history.py, by the player's page when there is no battle
(judge/views/arena.py) and by the list of contests, which only says that a tournament is being played.

What anybody may see, even without an account:

* a tournament that has started (in progress, finished or archived) whose organizers left «show in the history» on;
* a draft with that box on and a planned date, as upcoming, until half a day after that date: only its name, date and
  description. Its participants and bracket can still change, so they wait until it starts.

The organizers of a tournament, and whoever moderates the history (judge/arena/access.py), also see it while it is
hidden or a draft, marked as such, so they can check it and show it.

Never, to anybody here: code, submissions, the screens or their links, or the problems of a game not played yet.
The problems of the games are only named once the tournament is over, and only those the viewer may open on the site;
the others keep their letter.

Nothing here writes. The public pages never move a game forward (engine.tick): whoever watches cannot cause work or
take a lock. The pages of the players and the organizers do that, as always.
"""
from datetime import timedelta

from django.contrib.auth.models import AnonymousUser
from django.db.models import Count
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from judge.arena import access
from judge.arena.tournament import round_name
from judge.jinja2.gravatar import gravatar
from judge.models.arena import ArenaGame, ArenaGameProblem, ArenaMatch, ArenaParticipant, ArenaTournament, SLOT_A, \
    SLOT_B

__all__ = ['UPCOMING_GRACE', 'is_public', 'find', 'live', 'upcoming', 'finished', 'rows', 'summary', 'listing',
           'page', 'live_for_contests']

STARTED = (ArenaTournament.RUNNING, ArenaTournament.FINISHED, ArenaTournament.ARCHIVED)
OVER = (ArenaTournament.FINISHED, ArenaTournament.ARCHIVED)
# A tournament announced for a date still shows as upcoming for a while after it: it may start late.
UPCOMING_GRACE = timedelta(hours=12)
SUMMARY_FINISHED = 5
SUMMARY_RANKING = 5
LIST_LIMIT = 200
AVATAR = 64


def _other(slot):
    return SLOT_B if slot == SLOT_A else SLOT_A


# ---------------------------------------------------------------------------------------------------------------
# Which tournaments
# ---------------------------------------------------------------------------------------------------------------

def _shown():
    return ArenaTournament.objects.filter(show_in_history=True)


def live():
    return _shown().filter(status=ArenaTournament.RUNNING).order_by('-started_at', '-id')


def upcoming(now=None):
    now = now or timezone.now()
    return _shown().filter(status=ArenaTournament.DRAFT, planned_at__gte=now - UPCOMING_GRACE) \
        .order_by('planned_at', 'id')


def finished():
    return _shown().filter(status__in=OVER).order_by('-finished_at', '-started_at', '-id')


def is_public(tournament, now=None):
    if not tournament.show_in_history:
        return False
    if tournament.status in STARTED:
        return True
    now = now or timezone.now()
    return (tournament.status == ArenaTournament.DRAFT and tournament.planned_at is not None and
            tournament.planned_at >= now - UPCOMING_GRACE)


def find(user, slug, now=None):
    """(tournament, preview) for its public page, or None when `user` may not see it. `preview` is True when only
    its organizers and the moderators of the history see it: hidden, or a draft not announced."""
    tournament = ArenaTournament.objects.filter(slug=slug).first()
    if tournament is None:
        return None
    if is_public(tournament, now):
        return tournament, False
    if access.can_change_history(user, tournament):
        return tournament, True
    return None


def live_for_contests():
    """The tournaments being played, for the notice on the list of contests: name and address only."""
    return [{'name': t.name, 'url': reverse('arena_history_tournament', args=[t.slug])}
            for t in live().only('name', 'slug')[:3]]


# ---------------------------------------------------------------------------------------------------------------
# One row per tournament, for the lists
# ---------------------------------------------------------------------------------------------------------------

def _person(profile):
    if profile is None:
        return None
    return {'profile': profile, 'name': profile.display_name, 'avatar': gravatar(profile, AVATAR)}


def _finals(ids):
    """The final of each tournament: the only match of the last round that sends its winner nowhere."""
    return {match.tournament_id: match for match in
            ArenaMatch.objects.filter(tournament_id__in=ids, next_match__isnull=True, is_third_place=False)
            .select_related('player_a__user', 'player_b__user').order_by('round__number')}


def _champions(final):
    if final is None or final.status != ArenaMatch.DECIDED or not final.winner_slot:
        return None, None
    return final.player(final.winner_slot), final.player(_other(final.winner_slot))


def rows(tournaments):
    tournaments = list(tournaments)
    ids = [t.pk for t in tournaments]
    counts = dict(ArenaParticipant.objects.filter(tournament_id__in=ids).values('tournament_id')
                  .annotate(n=Count('id')).values_list('tournament_id', 'n'))
    finals = _finals(ids)
    result = []
    for tournament in tournaments:
        champion, runner_up = _champions(finals.get(tournament.pk)) if tournament.status in OVER else (None, None)
        result.append({
            'tournament': tournament,
            'url': reverse('arena_history_tournament', args=[tournament.slug]),
            'participants': counts.get(tournament.pk, 0),
            'champion': _person(champion),
            'runner_up': _person(runner_up),
        })
    return result


def summary(now=None):
    """What /duelos/ shows when there is no battle: what is being played, what comes next, the last ones and the
    first of the ranking of the duels (judge/arena/rating.py)."""
    from judge.arena import rating
    now = now or timezone.now()
    leaders = rating.top(SUMMARY_RANKING, now)
    for entry in leaders:
        entry['person'] = _person(entry['profile'])
    return {
        'live': rows(live()),
        'upcoming': rows(upcoming(now)[:3]),
        'finished': rows(finished()[:SUMMARY_FINISHED]),
        'more': finished().count() > SUMMARY_FINISHED,
        'ranking': leaders,
    }


def listing(user, now=None):
    """Everything for /duelos/torneos/, plus the started tournaments left out of the history that `user` still
    sees, apart and marked. Drafts are looked after from the battle panel."""
    now = now or timezone.now()
    hidden = access.history_hidden_for(user).filter(status__in=STARTED).order_by('-created_at', '-id')
    return {
        'live': rows(live()),
        'upcoming': rows(upcoming(now)),
        'finished': rows(finished()[:LIST_LIMIT]),
        'hidden': rows(hidden[:LIST_LIMIT]),
    }


# ---------------------------------------------------------------------------------------------------------------
# The page of one tournament
# ---------------------------------------------------------------------------------------------------------------

def _points(value):
    """100.0 as «100», 33.3333 as «33.333»: how points read in the results."""
    return '%g' % round(value or 0, 3)


def _clock(seconds):
    if seconds is None:
        return ''
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return '%d:%02d:%02d' % (hours, rest // 60, rest % 60) if hours else '%d:%02d' % (rest // 60, rest % 60)


def game_reason(game, winner, loser):
    """How a game ended, in words, from outside: «Ana solved every problem first.»"""
    if game.status == ArenaGame.TIE:
        return _('Tie: another game was played to break it.')
    if game.status == ArenaGame.CANCELLED:
        return _('Voided by the organizer: it did not count.')
    if game.status in ArenaGame.ACTIVE_STATUSES:
        return _('Being played now.')
    texts = {
        'all_solved': _('%(winner)s solved every problem first.'),
        'unreachable': _('%(loser)s could no longer catch up with %(winner)s.'),
        'more_points': _('%(winner)s had more points when time ran out.'),
        'earlier': _('Same points, but %(winner)s got there first.'),
        'organizer': _('The organizer gave this game to %(winner)s.'),
    }
    return texts.get(game.reason, _('%(winner)s won.')) % {'winner': winner, 'loser': loser}


def match_reason(match, winner, loser):
    """How a battle ended, in words, besides its games."""
    if match.status == ArenaMatch.CANCELLED:
        return _('Cancelled by the organizer.')
    if match.status != ArenaMatch.DECIDED:
        return ''
    if match.decided_reason == 'bye':
        return _('%(winner)s went straight to the next round: there was nobody to play against.') % {'winner': winner}
    if match.decided_reason == 'walkover':
        return _('%(winner)s won without playing on: %(loser)s did not show up or withdrew.') % \
            {'winner': winner, 'loser': loser}
    if match.decided_reason == 'organizer':
        return _('The organizer decided this battle in favor of %(winner)s.') % {'winner': winner}
    if match.best_of > 1:
        return _('%(winner)s won the series %(won)s to %(lost)s.') % {
            'winner': winner, 'won': max(match.wins_a, match.wins_b), 'lost': min(match.wins_a, match.wins_b)}
    return ''


def _cell(row):
    """What one player did on one problem of a finished game, from the result saved when it ended."""
    if not row:
        return {'kind': 'none', 'text': '—'}
    attempts = row.get('attempts') or 0
    if row.get('full'):
        return {'kind': 'full', 'text': _clock(row.get('solved_at')), 'attempts': attempts}
    if (row.get('best') or 0) > 0:
        return {'kind': 'partial', 'points': _points(row.get('best')), 'text': _clock(row.get('best_at')),
                'attempts': attempts}
    if attempts:
        return {'kind': 'failed', 'attempts': attempts}
    return {'kind': 'none', 'text': '—'}


def _problem_rows(game, game_problems, user, reveal):
    """Letter, points and what each player did on every problem of a game that ended. The name and the link only
    with `reveal`, and only of the problems the viewer may open on the site."""
    result = game.result or {}
    problems = []
    for gp in game_problems:
        named = reveal and gp.problem.is_accessible_by(user, skip_contest_problem_check=True)
        problems.append({
            'label': gp.label,
            'name': gp.problem.name if named else '',
            'url': reverse('problem_detail', args=[gp.problem.code]) if named else '',
            'points': gp.points,
            'a': _cell(((result.get(SLOT_A) or {}).get('problems') or {}).get(str(gp.order))),
            'b': _cell(((result.get(SLOT_B) or {}).get('problems') or {}).get(str(gp.order))),
        })
    return problems


def _game_view(game, match, game_problems, user, reveal):
    names = {SLOT_A: match.shown_name(SLOT_A), SLOT_B: match.shown_name(SLOT_B)}
    winner = names.get(game.winner_slot, '')
    loser = names.get(_other(game.winner_slot), '') if game.winner_slot else ''
    result = game.result or {}
    done = game.status in ArenaGame.DONE_STATUSES
    length = None
    if done and game.started_at and (game.closed_at or game.finished_at):
        length = ((game.closed_at or game.finished_at) - game.started_at).total_seconds()
    return {
        'number': game.number,
        'status': game.status,
        'live': game.status in ArenaGame.ACTIVE_STATUSES,
        'winner': game.winner_slot or None,
        'reason': game_reason(game, winner, loser),
        'length': _clock(max(length, 0)) if length is not None else '',
        'score': {SLOT_A: _points((result.get(SLOT_A) or {}).get('score')),
                  SLOT_B: _points((result.get(SLOT_B) or {}).get('score'))}
        if game.status in (ArenaGame.FINISHED, ArenaGame.TIE) and result else None,
        # Only the games that ended. Their problems are named once the tournament is over: before that one could
        # still be played in another battle.
        'problems': _problem_rows(game, game_problems, user, reveal) if done and game.status != ArenaGame.CANCELLED
        else [],
    }


def _player(match, slot):
    profile = match.player(slot)
    return {'profile': profile, 'name': match.shown_name(slot), 'seed': getattr(match, 'seed_' + slot),
            'avatar': gravatar(profile, AVATAR) if profile is not None else ''} if profile is not None else None


def _matches(tournament, rounds, user, reveal):
    total = len(rounds)
    names = {r.pk: round_name(r, total) for r in rounds}
    matches = list(ArenaMatch.objects.filter(tournament=tournament)
                   .select_related('player_a__user', 'player_b__user', 'round')
                   .order_by('round__number', 'is_third_place', 'position'))
    # Prepared games are never read: they may already hold the problems of a game nobody has played.
    games = list(ArenaGame.objects.filter(match__tournament=tournament).exclude(status=ArenaGame.PREPARED)
                 .order_by('match_id', 'number'))
    problems = {}
    ended = [game.pk for game in games if game.status in ArenaGame.DONE_STATUSES]
    for gp in ArenaGameProblem.objects.filter(game__in=ended).select_related('problem').order_by('order', 'id'):
        problems.setdefault(gp.game_id, []).append(gp)
    by_match = {}
    for game in games:
        by_match.setdefault(game.match_id, []).append(game)
    views = []
    for match in matches:
        winner = match.shown_name(match.winner_slot) if match.winner_slot else ''
        loser = match.shown_name(_other(match.winner_slot)) if match.winner_slot else ''
        views.append({
            'match': match,
            'id': match.pk,
            'anchor': 'batalla-%d' % match.pk,
            'round': _('Third place') if match.is_third_place else names.get(match.round_id, ''),
            'group': (match.round.number if match.round is not None else 0, match.is_third_place),
            'bye': match.decided_reason == 'bye',
            'status': match.status,
            'live': any(g.status in ArenaGame.ACTIVE_STATUSES for g in by_match.get(match.pk, ())),
            'best_of': match.best_of,
            'a': _player(match, SLOT_A),
            'b': _player(match, SLOT_B),
            'wins': {SLOT_A: match.wins_a, SLOT_B: match.wins_b},
            'winner': match.winner_slot or None,
            'reason': match_reason(match, winner, loser),
            'games': [_game_view(game, match, problems.get(game.pk, []), user, reveal)
                      for game in by_match.get(match.pk, ())],
        })
    return views


def _podium(matches):
    final = next((m for m in reversed(matches) if m['match'].next_match_id is None and not m['match'].is_third_place),
                 None)
    if final is None or not final['winner']:
        return None
    third = next((m for m in matches if m['match'].is_third_place), None)
    podium = {'first': final[final['winner']], 'second': final[_other(final['winner'])], 'third': [], 'fourth': None}
    if third is not None and third['winner']:
        podium['third'] = [third[third['winner']]]
        podium['fourth'] = third[_other(third['winner'])]
    else:
        # Without a match for third place both losers of the semifinals share it; a semifinal won by a bye has none.
        semis = [m for m in matches if m['match'].next_match_id == final['id'] and m['winner'] and not m['bye']]
        podium['third'] = [m[_other(m['winner'])] for m in semis if m[_other(m['winner'])]]
    return podium


def _standings(tournament, rounds, matches):
    """Every participant, by how far they got: the champion, then the runner-up, then the rest by the round where
    they lost (with a match for third place, its winner before its loser). Places are shared, as in any table: two
    who lost in the semifinals are both third. Only for a tournament that ended with a champion."""
    total = len(rounds)
    participants = list(ArenaParticipant.objects.filter(tournament=tournament).select_related('profile__user')
                        .order_by('seed'))
    stats = {p.profile_id: {'profile': p.profile, 'seed': p.seed, 'depth': 0, 'reached': '', 'battles': [0, 0],
                            'games': [0, 0]} for p in participants}
    for view in matches:
        match = view['match']
        number = match.round.number if match.round is not None else 0
        for slot in (SLOT_A, SLOT_B):
            row = stats.get(match.player_id(slot))
            if row is None:
                continue
            if match.status == ArenaMatch.DECIDED and not view['bye']:
                row['battles'][0 if match.winner_slot == slot else 1] += 1
            for game in view['games']:
                if game['status'] == ArenaGame.FINISHED and game['winner']:
                    row['games'][0 if game['winner'] == slot else 1] += 1
            if match.status != ArenaMatch.DECIDED or not match.winner_slot:
                continue
            won = match.winner_slot == slot
            if match.is_third_place:
                depth, reached = (total - 0.5, _('Third place')) if won else (total - 0.75, _('Fourth place'))
            elif match.next_match_id is None:
                depth, reached = (total + 1, _('Champion')) if won else (total, _('Runner-up'))
            elif won:
                continue
            else:
                depth, reached = number, view['round']
            if depth > row['depth']:
                row['depth'], row['reached'] = depth, reached
    ordered = sorted(stats.values(), key=lambda row: (-row['depth'], row['seed']))
    for row in ordered:
        row['place'] = 1 + sum(1 for other in ordered if other['depth'] > row['depth'])
        row['person'] = _person(row['profile'])
    return ordered


def _format(tournament, rounds):
    total = len(rounds)
    return [{'name': round_name(r, total), 'best_of': r.best_of} for r in rounds]


def page(tournament, user, now=None):
    """Everything the public page of a tournament shows. For a draft, only what an announcement says. Without a
    `user`, what anybody without an account sees."""
    now = now or timezone.now()
    user = user if user is not None else AnonymousUser()
    started = tournament.status in STARTED
    count = ArenaParticipant.objects.filter(tournament=tournament).count()
    data = {'tournament': tournament, 'started': started, 'over': tournament.status in OVER,
            'live': tournament.status == ArenaTournament.RUNNING, 'participants': count,
            'upcoming': not started and tournament.planned_at is not None and
            tournament.planned_at >= now - UPCOMING_GRACE}
    if not started:
        return data
    rounds = list(tournament.rounds.order_by('number'))
    matches = _matches(tournament, rounds, user, reveal=data['over'])
    data['format'] = _format(tournament, rounds)
    groups = [(r.number, False) for r in rounds] + [(len(rounds), True)]
    grouped = ([m for m in matches if m['group'] == group] for group in groups)
    data['rounds'] = [{'name': inside[0]['round'], 'matches': inside} for inside in grouped if inside]
    data['podium'] = _podium(matches)
    data['standings'] = _standings(tournament, rounds, matches) if data['over'] and data['podium'] else None
    return data

"""The ranking of the duels (2026-10-26): /duelos/ranking/ for anybody, and the page to decide what counts.

The ranking is open without an account, like the history of the tournaments. The page to decide asks for
`judge.decide_arena_ranking` (access.decides_ranking) and answers as if it did not exist to anybody else: it lists
every tournament and quick battle with its result (who played, the score, how it ended), never statements, code or
submissions, and its only actions are the decisions. Everything under /duelos/ is let through by the lock of a
battle (judge/arena/lock.py), so a player held in a battle is sent back to it, as on the pages of the history.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from judge.arena import access, history, lock, rating
from judge.arena.engine import ArenaError
from judge.arena.tournament import round_name
from judge.models.arena import ArenaMatch, ArenaParticipant, ArenaTournament, RANKING_PENDING, SLOT_A, SLOT_B
from judge.views.arena import json_error, json_response, read_json
from judge.views.arena_history import CALLED_POLL_MS

__all__ = ['ranking_page', 'ranking_decide', 'ranking_decide_action']

LIST_LIMIT = 100


def _held(request):
    profile = getattr(request, 'profile', None)
    return profile is not None and lock.is_held(profile.pk)


@require_GET
def ranking_page(request):
    if _held(request):
        return HttpResponseRedirect(lock.HOME)
    decides = access.decides_ranking(request.user)
    return render(request, 'arena/ranking/ranking.html', {
        'title': _('Ranking of the duels'),
        'history_tab': 'ranking', 'can_manage': access.can_manage(request.user),
        'watch_called': request.user.is_authenticated, 'called_poll': CALLED_POLL_MS,
        'table': rating.table(),
        'decides': decides,
        'pending': rating.pending_count() if decides else 0,
    })


def _person(match, slot):
    return {'name': match.shown_name(slot) or '—', 'username': match.player(slot).username
            if match.player_id(slot) else ''}


def _battle(match, games, names=None):
    winner = match.winner_slot
    return {
        'id': match.pk,
        'title': match.title,
        'round': names.get((match.round_id, match.is_third_place), '') if names is not None else '',
        'a': _person(match, SLOT_A), 'b': _person(match, SLOT_B),
        'wins': (match.wins_a, match.wins_b),
        'winner': winner or None,
        'status': match.status,
        'status_label': match.get_status_display(),
        'reason': match.decided_reason,
        'when': match.decided_at or match.created_at,
        'own': match.ranking,
        'decision': rating.decision(match),
        'never': rating.never_reason(match),
        'games': games.get(match.pk, 0),
        'manage_url': reverse('arena_manage_match', args=[match.pk]),
    }


def _tournament(tournament, user, participants, battles):
    return {
        'id': tournament.pk,
        'name': tournament.name,
        'status': tournament.status,
        'status_label': tournament.get_status_display(),
        'ranking': tournament.ranking,
        'participants': participants.get(tournament.pk, 0),
        'when': tournament.finished_at or tournament.started_at or tournament.created_at,
        'shown': tournament.show_in_history,
        'public_url': reverse('arena_history_tournament', args=[tournament.slug])
        if history.is_public(tournament) else '',
        'manage_url': reverse('arena_manage_tournament', args=[tournament.slug])
        if access.can_manage_tournament(user, tournament) else '',
        'battles': battles.get(tournament.pk, []),
    }


def decide_data(user, everything=False):
    """What the page to decide lists: tournaments and quick battles, only the ones not decided yet unless
    `everything`. Quick battles that can never count (cancelled, won without playing on, played with a typed name)
    are left out of the first list: there is nothing to decide about them."""
    tournaments = ArenaTournament.objects.all().order_by('-created_at', '-id')
    quick = ArenaMatch.objects.filter(tournament__isnull=True).select_related('player_a__user', 'player_b__user') \
        .order_by('-created_at', '-id')
    if not everything:
        tournaments = tournaments.filter(ranking=RANKING_PENDING)
        quick = quick.filter(ranking=RANKING_PENDING, display_name_a='', display_name_b='') \
            .exclude(status=ArenaMatch.CANCELLED).exclude(decided_reason='walkover')
    tournaments = list(tournaments[:LIST_LIMIT])
    quick = list(quick[:LIST_LIMIT])
    ids = [t.pk for t in tournaments]
    participants = dict(ArenaParticipant.objects.filter(tournament_id__in=ids).values('tournament_id')
                        .annotate(n=Count('id')).values_list('tournament_id', 'n'))
    inside = list(ArenaMatch.objects.filter(tournament_id__in=ids).exclude(decided_reason='bye')
                  .select_related('player_a__user', 'player_b__user', 'round', 'tournament')
                  .order_by('round__number', 'is_third_place', 'position'))
    games = rating.games_by_match([m.pk for m in inside] + [m.pk for m in quick])
    totals = {}
    for tournament in tournaments:
        totals[tournament.pk] = tournament.rounds.count()
    battles = {}
    for match in inside:
        total = totals.get(match.tournament_id, 0)
        name = _('Third place') if match.is_third_place else (round_name(match.round, total) if match.round_id else '')
        battles.setdefault(match.tournament_id, []).append(
            _battle(match, games, {(match.round_id, match.is_third_place): name}))
    return {
        'tournaments': [_tournament(t, user, participants, battles) for t in tournaments],
        'quick': [_battle(m, games) for m in quick],
        'everything': everything,
    }


def _decider(request):
    if not access.decides_ranking(request.user):
        raise Http404()


@login_required
@require_GET
def ranking_decide(request):
    _decider(request)
    if _held(request):
        return HttpResponseRedirect(lock.HOME)
    everything = request.GET.get('todo') == '1'
    return render(request, 'arena/ranking/decide.html', {
        'title': _('Decide what counts for the ranking'),
        'history_tab': 'ranking', 'can_manage': access.can_manage(request.user),
        'watch_called': True, 'called_poll': CALLED_POLL_MS,
        'data': decide_data(request.user, everything),
        # Only reads: whether what is stored is what the battles give (judge/arena/rating.py).
        'behind': rating.check(),
    })


@login_required
@require_POST
def ranking_decide_action(request):
    _decider(request)
    try:
        data = read_json(request)
        action = data.get('action')
        target = data.get('id')
        if action != 'recompute' and (isinstance(target, bool) or not isinstance(target, int)):
            raise ArenaError(_('Invalid request.'))
        if action == 'tournament':
            rating.set_tournament_ranking(request.profile, target, data.get('ranking'))
        elif action == 'match':
            rating.set_match_ranking(request.profile, target, data.get('ranking'))
        elif action == 'recompute':
            rating.recompute()
        else:
            raise ArenaError(_('Unknown action.'))
    except ArenaError as error:
        return json_error(error.message, error.status)
    except rating.RankingBusy:
        return json_error(_('The ranking is being worked out right now. Try again in a moment.'), 409)
    return json_response({'ok': True})

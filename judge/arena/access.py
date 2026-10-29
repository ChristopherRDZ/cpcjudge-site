"""Who organizes what in the duels (2026-10-23).

Two permissions, as with problems and contests:

* `judge.manage_arena`, «organize own battles»: the quick battles and tournaments one created or was added to as a
  co-organizer, with their games, plus one's own projector screens and problem banks.
* `judge.manage_all_arena`, «organize all battles»: every battle, tournament, screen and bank. It works by itself
  too: whoever has it does not also need the first one.

Superusers have both. The battles of a tournament belong to the tournament: its creator and its co-organizers run
them, whoever built the bracket. Somebody added as a co-organizer still needs one of the two permissions; the lists
only offer people who have one.

Until this change `judge.manage_arena` gave everything, so the people who had it see only their own from now on;
see README.md of security_preparation/permisos-correos-duelos-20261023.

The public history of the tournaments (2026-10-25, judge/arena/history.py) adds `judge.hide_arena_history`, «show or
hide any tournament in the history»: for somebody who looks after the history without organizing. Whoever organizes
a tournament already decides about it, and whoever organizes all battles about every one. It gives nothing else.

The ranking of the duels (2026-10-26, judge/arena/rating.py) adds `judge.decide_arena_ranking`, «decide which
tournaments and duels count»: neither organizing permission includes it, so whoever runs a battle does not decide
whether it counts. It gives the page to decide (the results of every tournament and quick battle, without
statements, code or submissions) and nothing else.
"""
from django.contrib.auth.models import Permission
from django.db.models import Q

from judge.models import Profile
from judge.models.arena import ArenaMatch, ArenaParticipant, ArenaProblemBank, ArenaScreen, ArenaTournament

OWN = 'judge.manage_arena'
ALL = 'judge.manage_all_arena'
HISTORY = 'judge.hide_arena_history'
RANKING = 'judge.decide_arena_ranking'

__all__ = ['OWN', 'ALL', 'HISTORY', 'RANKING', 'can_manage', 'manages_all', 'tournaments_for', 'matches_for',
           'screens_for', 'banks_for', 'screens_of_actor', 'contended_tournaments', 'contends', 'profile_contends',
           'contended_screens', 'contended_banks', 'contends_bank', 'closed_match_ids', 'can_manage_tournament',
           'plays', 'can_manage_match', 'can_manage_screen', 'can_manage_bank', 'eligible_organizers',
           'is_eligible_organizer', 'moderates_history', 'can_change_history', 'history_hidden_for', 'decides_ranking']


def can_manage(user):
    """May open the battle panel at all."""
    return bool(user is not None and user.is_authenticated and user.is_active and
                (user.has_perm(OWN) or user.has_perm(ALL)))


def manages_all(user):
    return bool(can_manage(user) and user.has_perm(ALL))


def _profile_id(user):
    profile = getattr(user, 'profile', None)
    return profile.pk if profile is not None else None


def _tournament_q(profile_id, prefix=''):
    return Q(**{prefix + 'created_by_id': profile_id}) | Q(**{prefix + 'organizers': profile_id})


def tournaments_for(user):
    queryset = ArenaTournament.objects.all()
    if manages_all(user):
        return queryset
    if not can_manage(user):
        return queryset.none()
    ids = ArenaTournament.objects.filter(_tournament_q(_profile_id(user))).values('id')
    return queryset.filter(id__in=ids)


def matches_for(user):
    """Quick battles by their own organizers; the battles of a tournament by those of the tournament."""
    queryset = ArenaMatch.objects.all()
    if manages_all(user):
        return queryset
    if not can_manage(user):
        return queryset.none()
    profile_id = _profile_id(user)
    quick = ArenaMatch.objects.filter(Q(tournament__isnull=True) & _tournament_q(profile_id)).values('id')
    return queryset.filter(Q(id__in=quick) | Q(tournament_id__in=tournaments_for(user).values('id')))


def screens_for(user):
    """Their own screens, or every one for whoever organizes all battles; never one aimed at a tournament they still
    play or at one of its battles (contended_screens, 2026-10-28): neither listed with its link nor changed."""
    queryset = ArenaScreen.objects.all()
    if not can_manage(user):
        return queryset.none()
    if not manages_all(user):
        queryset = queryset.filter(created_by_id=_profile_id(user))
    return queryset.exclude(id__in=contended_screens(user).values('id'))


def screens_of_actor(actor):
    """The screens a battle created by `actor` may take: theirs, or every one for whoever organizes all battles.
    Code that runs without the permission check of the panel (the shell, tests) keeps every screen, as before."""
    if actor is None or not can_manage(actor.user):
        return ArenaScreen.objects.all()
    return screens_for(actor.user)


def banks_for(user):
    """Their own banks, or every one for whoever organizes all battles; never one of a tournament they still play
    (contended_banks, 2026-10-28): it is not listed, so no link leads to a 404."""
    queryset = ArenaProblemBank.objects.all()
    if not can_manage(user):
        return queryset.none()
    if not manages_all(user):
        queryset = queryset.filter(created_by_id=_profile_id(user))
    return queryset.exclude(id__in=contended_banks(user).values('id'))


def _within(queryset, obj):
    return obj is not None and obj.pk is not None and queryset.filter(pk=obj.pk).exists()


def contended_tournaments(user):
    """The tournaments `user` still plays:

    * a draft they are a participant of, even while its bracket leaves them out of every battle: the bracket is
      built again from the participants each time they change;
    * a draft or a running tournament with a battle of theirs not decided or cancelled yet, whether they are one of
      its participants or not (2026-10-28, round 2 of the adversarial review): replacing a player, or editing a
      battle in the admin, puts somebody in a battle without making them a participant, and the bracket moves them
      forward like anybody else.

    Once they are out, or it finishes, they may run it again."""
    profile_id = _profile_id(user) if user is not None and user.is_authenticated else None
    if not profile_id:
        return ArenaTournament.objects.none()
    return _contended_by(profile_id)


def _contended_by(profile_id):
    entered = ArenaParticipant.objects.filter(profile_id=profile_id).values('tournament_id')
    playing = ArenaMatch.objects.filter(Q(player_a_id=profile_id) | Q(player_b_id=profile_id),
                                        tournament__isnull=False) \
        .exclude(status__in=(ArenaMatch.DECIDED, ArenaMatch.CANCELLED)).values('tournament_id')
    return ArenaTournament.objects.filter(
        Q(status=ArenaTournament.DRAFT, id__in=entered) |
        Q(status__in=(ArenaTournament.DRAFT, ArenaTournament.RUNNING), id__in=playing))


def contends(user, tournament):
    return bool(tournament is not None and tournament.pk is not None and
                contended_tournaments(user).filter(pk=tournament.pk).exists())


def profile_contends(profile_id, tournament_id):
    """contends, for whoever has the profile `profile_id` (the creator of a screen) and a tournament id."""
    return bool(profile_id and tournament_id and _contended_by(profile_id).filter(pk=tournament_id).exists())


def contended_screens(user):
    """The screens aimed at a tournament `user` still plays or at one of its battles, in whatever mode they are now
    (a screen keeps its tournament when it moves to a battle or to a message): closed to them, as the tournament is
    (2026-10-28, round 3 of the adversarial review). Whoever organizes all battles had the link of every screen in
    the panel, so they watched the other battles of their tournament, with their code and statements, and could
    change what the projector of the room showed."""
    tournaments = contended_tournaments(user).values('id')
    battles = ArenaMatch.objects.filter(tournament_id__in=tournaments).values('id')
    return ArenaScreen.objects.filter(Q(tournament_id__in=tournaments) | Q(match_id__in=battles))


def contended_banks(user):
    """The problem banks of the tournaments `user` still plays, closed to them as those tournaments are (2026-10-28,
    round 2 of the adversarial review): the page of a bank lists every problem their next games may draw, with its
    level and its note, and changing it changed what those games would get."""
    return ArenaProblemBank.objects.filter(
        id__in=contended_tournaments(user).filter(bank__isnull=False).values('bank_id'))


def contends_bank(user, bank_id):
    """Whether the bank `bank_id` is one of a tournament `user` still plays (contended_banks)."""
    return bool(bank_id and contended_banks(user).filter(pk=bank_id).exists())


def closed_match_ids(user):
    """The battles whose panel is closed to `user` (can_manage_match) because they play them or their tournament,
    for the lists that link to it."""
    profile_id = _profile_id(user) if user is not None and user.is_authenticated else None
    if not profile_id:
        return set()
    played = (Q(player_a_id=profile_id) | Q(player_b_id=profile_id)) & \
        ~Q(status__in=(ArenaMatch.DECIDED, ArenaMatch.CANCELLED))
    return set(ArenaMatch.objects.filter(played | Q(tournament_id__in=contended_tournaments(user).values('id')))
               .values_list('pk', flat=True))


def can_manage_tournament(user, tournament):
    """Never for one of its participants while they still play it (2026-10-28, adversarial review of duelos-web-1):
    its panel, its rounds and rules and the problems of its battles told them the problems of their next games and
    let them change the rules of their own next battle."""
    if contends(user, tournament):
        return False
    return manages_all(user) or (can_manage(user) and _within(tournaments_for(user), tournament))


def plays(user, match):
    """Whether `user` plays `match` and it is not decided yet."""
    profile_id = _profile_id(user) if user is not None and user.is_authenticated else None
    return bool(profile_id and match is not None and match.slot_of(profile_id) is not None and
                match.status not in (ArenaMatch.DECIDED, ArenaMatch.CANCELLED))


def can_manage_match(user, match):
    """Never for one of its players until it is decided (duelos-web-1, 2026-10-28), whatever they organize: the
    panel showed them the live code and the submissions of the rival and the problems of the next games, and let
    them decide the battle. Nor, in a tournament, for whoever still plays it: the battles they may still reach
    (the final) have their problems chosen in advance. The lock of the battle lets every page under /duelos/
    through, the panel included."""
    if plays(user, match) or (match is not None and match.tournament_id and contends(user, match.tournament)):
        return False
    return manages_all(user) or (can_manage(user) and _within(matches_for(user), match))


def can_manage_screen(user, screen):
    """Never a screen aimed at a tournament they still play (contended_screens, 2026-10-28)."""
    if screen is not None and screen.pk is not None and contended_screens(user).filter(pk=screen.pk).exists():
        return False
    return manages_all(user) or (can_manage(user) and _within(screens_for(user), screen))


def can_manage_bank(user, bank):
    """Never a bank of a tournament they still play (contended_banks, 2026-10-28)."""
    if bank is not None and contends_bank(user, bank.pk):
        return False
    return manages_all(user) or (can_manage(user) and _within(banks_for(user), bank))


def eligible_organizers():
    """Active accounts that could organize battles: superusers and whoever has either permission, given to them or
    to one of their groups."""
    permissions = Permission.objects.filter(content_type__app_label='judge',
                                            codename__in=('manage_arena', 'manage_all_arena'))
    return Profile.objects.filter(user__is_active=True).filter(
        Q(user__is_superuser=True) | Q(user__user_permissions__in=permissions) |
        Q(user__groups__permissions__in=permissions)).distinct()


def is_eligible_organizer(profile):
    return profile is not None and can_manage(profile.user)


def moderates_history(user):
    """May show or hide any tournament in the public history: the permission of its own, or organizing all."""
    return bool(user is not None and user.is_authenticated and user.is_active and
                (user.has_perm(HISTORY) or manages_all(user)))


def can_change_history(user, tournament):
    """May show or hide this tournament in the public history: its organizers, and whoever moderates the history."""
    return moderates_history(user) or can_manage_tournament(user, tournament)


def history_hidden_for(user):
    """The tournaments left out of the history that `user` still sees there, marked as hidden: theirs, or every one
    for whoever moderates the history. Nobody else ever sees them."""
    queryset = ArenaTournament.objects.filter(show_in_history=False)
    if moderates_history(user):
        return queryset
    return queryset.filter(id__in=tournaments_for(user).values('id'))


def decides_ranking(user):
    """May decide which tournaments and quick battles count for the ranking of the duels."""
    return bool(user is not None and user.is_authenticated and user.is_active and user.has_perm(RANKING))

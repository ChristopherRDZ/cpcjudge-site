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
"""
from django.contrib.auth.models import Permission
from django.db.models import Q

from judge.models import Profile
from judge.models.arena import ArenaMatch, ArenaProblemBank, ArenaScreen, ArenaTournament

OWN = 'judge.manage_arena'
ALL = 'judge.manage_all_arena'
HISTORY = 'judge.hide_arena_history'

__all__ = ['OWN', 'ALL', 'HISTORY', 'can_manage', 'manages_all', 'tournaments_for', 'matches_for', 'screens_for',
           'banks_for', 'screens_of_actor', 'can_manage_tournament', 'can_manage_match', 'can_manage_screen',
           'can_manage_bank', 'eligible_organizers', 'is_eligible_organizer', 'moderates_history',
           'can_change_history', 'history_hidden_for']


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
    queryset = ArenaScreen.objects.all()
    if manages_all(user):
        return queryset
    if not can_manage(user):
        return queryset.none()
    return queryset.filter(created_by_id=_profile_id(user))


def screens_of_actor(actor):
    """The screens a battle created by `actor` may take: theirs, or every one for whoever organizes all battles.
    Code that runs without the permission check of the panel (the shell, tests) keeps every screen, as before."""
    if actor is None or not can_manage(actor.user):
        return ArenaScreen.objects.all()
    return screens_for(actor.user)


def banks_for(user):
    queryset = ArenaProblemBank.objects.all()
    if manages_all(user):
        return queryset
    if not can_manage(user):
        return queryset.none()
    return queryset.filter(created_by_id=_profile_id(user))


def _within(queryset, obj):
    return obj is not None and obj.pk is not None and queryset.filter(pk=obj.pk).exists()


def can_manage_tournament(user, tournament):
    return manages_all(user) or (can_manage(user) and _within(tournaments_for(user), tournament))


def can_manage_match(user, match):
    return manages_all(user) or (can_manage(user) and _within(matches_for(user), match))


def can_manage_screen(user, screen):
    return manages_all(user) or (can_manage(user) and _within(screens_for(user), screen))


def can_manage_bank(user, bank):
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

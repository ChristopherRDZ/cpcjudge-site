"""Contest registration and division selection, shared by web views and tests."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext as _

from judge.models import Contest, ContestParticipation, ContestTeamMember, Profile, Team


def ranking_division(request, contest):
    if contest.participation_mode == Contest.TEAM:
        return 'team'
    if contest.participation_mode == Contest.INDIVIDUAL:
        return 'individual'
    value = request.GET.get('division')
    if value in ('individual', 'team'):
        return value
    participation = getattr(request, 'participation', None)
    return 'team' if participation and participation.contest_id == contest.pk and participation.team_id else 'individual'


def in_division(queryset, division):
    return queryset.filter(team__isnull=division == 'individual')


def _active_attempt(contest, profile):
    entries = contest.users.for_profile(profile).filter(virtual__gte=1).select_related('contest').order_by('-virtual')
    return next((entry for entry in entries if not entry.ended), None)


def _roster_blocker(contest, profile, virtual=False):
    """Why somebody the registrant is bringing along cannot be on the roster, or None.

    Contest access is deliberately not re-checked here. Whoever registers the team
    carries the access code, the visibility and the organisation limit for the whole
    roster, and `Contest.access_check` then lets each registered member in through
    their `team_roster` row. What is left is personal and nobody can delegate it: a
    disabled account, a ban from this contest, or running the contest itself.
    """
    if not profile.user.is_active:
        return _('%(user)s no tiene una cuenta activa.') % {'user': profile.username}
    if contest.banned_users.filter(pk=profile.pk).exists():
        return _('%(user)s no puede participar en este concurso.') % {'user': profile.username}
    if not virtual and (profile.pk in contest.editor_ids or profile.pk in contest.tester_ids):
        return _('%(user)s organiza o revisa este concurso, así que no puede inscribirse como '
                 'participante. Quítalo de la lista para continuar.') % {'user': profile.username}
    return None


def _access_blocker(contest, profile, virtual=False):
    """Full check for whoever does the registering: access to the contest, then the rest."""
    if not profile.user.is_active or not contest.is_accessible_by(profile.user):
        return _('%(user)s no tiene acceso a este concurso.') % {'user': profile.username}
    if contest.limit_join_organizations and not contest.join_organizations.filter(
            pk__in=profile.organizations.all()).exists():
        return _('%(user)s no pertenece a ninguna de las organizaciones que pueden entrar a este '
                 'concurso.') % {'user': profile.username}
    return _roster_blocker(contest, profile, virtual)


def _chosen_members(members, member_ids, actor):
    """Narrow a team down to who will actually compete. `None` takes the whole team."""
    if member_ids is None:
        return members
    wanted = set()
    for value in member_ids:
        try:
            wanted.add(int(value))
        except (TypeError, ValueError):
            raise ValidationError(_('Selecciona integrantes válidos del equipo.'))
    chosen = [member for member in members if member.pk in wanted]
    if len(chosen) != len(wanted):
        raise ValidationError(_('Selecciona integrantes válidos del equipo.'))
    if actor.pk not in wanted:
        raise ValidationError(_('Debes incluirte entre quienes participan.'))
    return chosen


@transaction.atomic
def join_attempt(contest_id, actor, selection, access_code='', member_ids=None):
    """Register a whole roster once, then attach only this caller's current session.

    Locking the contest serializes individual/team choices and competing teams,
    including teams with overlapping members. Team mutations use the same team
    lock; historical membership is never inferred from today's membership list.

    `member_ids` picks which members of the team compete; `None` takes the whole team.
    The caller must be among them, and it is that registration which admits the rest:
    they neither type the access code again nor need their own access to the contest.
    """
    contest = Contest.objects.select_for_update().get(pk=contest_id)
    if not contest.started and actor.pk not in contest.editor_ids | contest.tester_ids:
        raise ValidationError(_('El concurso todavía no ha comenzado.'))
    if contest.banned_users.filter(pk=actor.pk).exists() and not actor.user.is_superuser:
        raise ValidationError(_('No puedes participar en este concurso.'))
    if not contest.is_accessible_by(actor.user):
        raise ValidationError(_('No tienes acceso a este concurso.'))
    editor = contest.is_editable_by(actor.user)
    spectator = not contest.ended and (actor.pk in contest.editor_ids or actor.pk in contest.tester_ids or
                                       contest.has_completed_contest(actor.user))
    existing = None
    if selection.startswith('entry:'):
        try:
            existing = contest.users.for_profile(actor).get(pk=int(selection[6:]))
        except (ValueError, ContestParticipation.DoesNotExist):
            raise ValidationError(_('No puedes entrar a esa participación.'))
        existing.contest = contest
        if existing.ended or existing.is_disqualified:
            raise ValidationError(_('Esta participación ya terminó o está descalificada.'))
    if existing is None and spectator:
        if not contest.is_spectatable_by(actor.user):
            raise ValidationError(_('No puedes observar este concurso.'))
        existing = contest.users.filter(user=actor, team__isnull=True, virtual=ContestParticipation.SPECTATE).first()

    def require_access_code():
        """The code opens a participation; it is not asked again to return to one.

        Resuming an `entry:` never asked for it, and a member of an already registered
        team is in the same position: the roster was admitted when whoever registered
        typed the code. Asking each member again is what made a team of four need four
        copies of the code.
        """
        if contest.access_code and not editor and access_code != contest.access_code:
            raise ValidationError(_('Introduce el código de acceso correcto.'))

    if existing is None and spectator:
        require_access_code()
        existing = ContestParticipation.objects.create(contest=contest, user=actor, virtual=ContestParticipation.SPECTATE)

    if existing is None:
        team = None
        if selection.startswith('team:'):
            try:
                team = Team.objects.select_for_update().get(pk=int(selection[5:]), is_active=True, members=actor)
            except (ValueError, Team.DoesNotExist):
                raise ValidationError(_('Selecciona un equipo activo del que formes parte.'))
            if contest.participation_mode == Contest.INDIVIDUAL:
                raise ValidationError(_('Este concurso sólo admite participaciones individuales.'))
            if (contest.is_private and contest.private_teams.exists() and
                    not contest.private_teams.filter(pk=team.pk).exists()):
                raise ValidationError(_('Este equipo no está autorizado para participar en el concurso privado.'))
            existing = contest.users.filter(team=team, virtual=0).first() if not contest.ended else None
            if existing is None and contest.ended:
                existing = next((p for p in contest.users.filter(team=team, virtual__gt=0).order_by('-virtual')
                                 if not p.ended), None)
            if existing and not existing.contains_profile(actor.pk):
                raise ValidationError(_('No formas parte de la lista inscrita en esta participación.'))
            if existing is None:
                from judge.team_services import require_teams_enabled
                require_teams_enabled()
            members = list(team.members.select_related('user').order_by('pk'))
            if existing is None:
                # The roster is picked now and frozen; joining an existing attempt never re-picks it.
                members = _chosen_members(members, member_ids, actor)
                if not contest.team_min_size <= len(members) <= contest.team_max_size:
                    raise ValidationError(_('El equipo debe tener entre %(minimum)s y %(maximum)s integrantes.') % {
                        'minimum': contest.team_min_size, 'maximum': contest.team_max_size})
        elif selection == 'individual':
            if contest.participation_mode == Contest.TEAM:
                raise ValidationError(_('Este concurso sólo admite equipos.'))
            members = [actor]
            existing = contest.users.for_profile(actor).filter(virtual=0).first() if not contest.ended else None
            if existing and existing.team_id:
                raise ValidationError(_('Ya estás inscrito en equipo en este concurso.'))
        else:
            raise ValidationError(_('Selecciona cómo quieres participar.'))

        if existing is None:
            require_access_code()
            list(Profile.objects.select_for_update().filter(pk__in=[p.pk for p in members]).order_by('pk'))
            for member in members:
                # Only the caller needs access of their own; their registration carries the rest.
                blocker = (_access_blocker if member.pk == actor.pk else _roster_blocker)(
                    contest, member, contest.ended)
                if blocker:
                    raise ValidationError(blocker)
                if not contest.ended and contest.users.for_profile(member).filter(virtual=0).exists():
                    raise ValidationError(_('%(user)s ya está inscrito en otra participación de este concurso.')
                                          % {'user': member.username})
                active = _active_attempt(contest, member) if contest.ended else None
                if active:
                    if not team and not active.team_id:
                        existing = active
                    else:
                        raise ValidationError(_('%(user)s ya tiene una simulación en curso en este concurso.')
                                              % {'user': member.username})
            if existing is None:
                attempt = max(1, (contest.users.aggregate(v=Max('virtual'))['v'] or 0) + 1) if contest.ended else 0
                existing = ContestParticipation.objects.create(contest=contest, user=actor, team=team,
                    team_name=team.name if team else '', virtual=attempt, real_start=timezone.now())
                if team:
                    ContestTeamMember.objects.bulk_create([
                        ContestTeamMember(participation=existing, profile=member, username=member.username)
                        for member in members])
                contest._updating_stats_only = True
                contest.update_user_count()

    if existing.is_disqualified or existing.ended:
        raise ValidationError(_('Esta participación ya terminó o está descalificada.'))
    Profile.objects.filter(pk=actor.pk).update(current_contest=existing)
    actor.current_contest = existing
    return existing

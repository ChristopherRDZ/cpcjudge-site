"""Team mutations. Lock order: team, then profiles in primary-key order.

Invitation responses and changes to the roster serialize on the team row. No
notification delivery or external side effect occurs inside these transactions.
"""
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from judge.models import Profile
from judge.models.team import Team, TeamInvitation, TeamMembership


def clean_team_name(value):
    name = ' '.join(value.split())
    if not name or len(name) > 60 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValidationError(_('Escribe un nombre de equipo de 1 a 60 caracteres.'))
    return name


def require_teams_enabled():
    if not getattr(settings, 'CPC_TEAMS_ENABLED', True):
        raise ValidationError(_('Las nuevas inscripciones e invitaciones de equipos están pausadas temporalmente.'))


def _owner(team, actor):
    if not team.is_active or team.owner_id != actor.pk:
        raise PermissionDenied


def _require_idle(team):
    now = timezone.now()
    for entry in team.participations.filter(virtual__gte=0).select_related('contest'):
        if (entry.virtual == 0 and entry.contest.end_time > now) or (entry.virtual > 0 and entry.end_time > now):
            raise ValidationError(_('No se puede cambiar la lista de integrantes ni eliminar el equipo mientras participa en un concurso o simulación activa.'))


@transaction.atomic
def create_team(actor, name):
    require_teams_enabled()
    Profile.objects.select_for_update().get(pk=actor.pk)
    if Team.objects.filter(owner=actor, is_active=True).count() >= getattr(settings, 'CPC_MAX_OWNED_TEAMS', 20):
        raise ValidationError(_('Ya alcanzaste el límite de equipos activos que puedes crear.'))
    team = Team.objects.create(owner=actor, name=clean_team_name(name))
    TeamMembership.objects.create(team=team, profile=actor)
    return team


@transaction.atomic
def invite_member(actor, team_id, username):
    require_teams_enabled()
    team = Team.objects.select_for_update().get(pk=team_id)
    _owner(team, actor)
    recipient = Profile.objects.select_related('user').filter(user__username=username.strip(), user__is_active=True).first()
    if recipient is None:
        raise ValidationError(_('No se encontró un usuario activo con ese nombre.'))
    # Serialize across teams, too, so sending from many teams cannot bypass the limit.
    list(Profile.objects.select_for_update().filter(pk__in=[actor.pk, recipient.pk]).order_by('pk'))
    if team.members.filter(pk=recipient.pk).exists():
        raise ValidationError(_('Ese usuario ya forma parte del equipo.'))
    if team.invitations.filter(recipient=recipient, status=TeamInvitation.PENDING).exists():
        raise ValidationError(_('Ya hay una invitación pendiente para ese usuario.'))
    now = timezone.now()
    recent = TeamInvitation.objects.filter(sender=actor, created__gte=now - timedelta(hours=1)).count()
    if recent >= getattr(settings, 'CPC_TEAM_INVITES_PER_HOUR', 20):
        raise ValidationError(_('Has enviado muchas invitaciones. Inténtalo más tarde.'))
    if TeamInvitation.objects.filter(recipient=recipient, status=TeamInvitation.PENDING, team__is_active=True).count() >= 50:
        raise ValidationError(_('Ese usuario tiene demasiadas invitaciones pendientes.'))
    return TeamInvitation.objects.create(team=team, sender=actor, recipient=recipient)


@transaction.atomic
def respond_invitation(actor, invitation_id, action):
    reference = TeamInvitation.objects.filter(pk=invitation_id).values('team_id').first()
    if reference is None:
        raise ValidationError(_('Esta invitación ya no está disponible.'))
    try:
        team = Team.objects.select_for_update().get(pk=reference['team_id'])
        invitation = TeamInvitation.objects.select_for_update().get(pk=invitation_id)
    except (Team.DoesNotExist, TeamInvitation.DoesNotExist):
        raise ValidationError(_('Esta invitación ya no está disponible.'))
    if action == 'cancel':
        _owner(team, actor)
    elif invitation.recipient_id != actor.pk or action not in ('accept', 'decline'):
        raise PermissionDenied
    if not team.is_active or invitation.status != TeamInvitation.PENDING:
        raise ValidationError(_('Esta invitación ya no está pendiente.'))
    if action == 'accept':
        require_teams_enabled()
        Profile.objects.select_for_update().get(pk=actor.pk)
        TeamMembership.objects.get_or_create(team=team, profile=actor)
    invitation.status = {'accept': TeamInvitation.ACCEPTED, 'decline': TeamInvitation.DECLINED,
                         'cancel': TeamInvitation.CANCELLED}[action]
    invitation.responded = timezone.now()
    invitation.save(update_fields=['status', 'responded'])
    return team


@transaction.atomic
def remove_member(actor, team_id, profile_id):
    team = Team.objects.select_for_update().get(pk=team_id)
    if not team.is_active:
        raise PermissionDenied
    if actor.pk != profile_id:
        _owner(team, actor)
    _require_idle(team)
    if profile_id == team.owner_id:
        raise ValidationError(_('Para salir, transfiere primero la propiedad a otro integrante o elimina el equipo.'))
    if not team.memberships.filter(profile_id=profile_id).exists():
        raise ValidationError(_('Ese usuario ya no forma parte del equipo.'))
    team.memberships.filter(profile_id=profile_id).delete()


@transaction.atomic
def transfer_team(actor, team_id, profile_id):
    team = Team.objects.select_for_update().get(pk=team_id)
    _owner(team, actor)
    _require_idle(team)
    if not team.members.filter(pk=profile_id).exists():
        raise ValidationError(_('El nuevo propietario debe formar parte del equipo.'))
    team.owner_id = profile_id
    team.save(update_fields=['owner'])


@transaction.atomic
def delete_team(actor, team_id):
    team = Team.objects.select_for_update().get(pk=team_id)
    _owner(team, actor)
    _require_idle(team)
    # PROTECT on participation.team is a second line of defence against history loss.
    if team.participations.exists() or team.balloon_locations.exists():
        team.is_active = False
        team.save(update_fields=['is_active'])
        team.memberships.all().delete()
        team.invitations.filter(status=TeamInvitation.PENDING).update(
            status=TeamInvitation.CANCELLED, responded=timezone.now())
    else:
        team.delete()

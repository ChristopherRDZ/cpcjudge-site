from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

__all__ = ['Team', 'TeamMembership', 'TeamInvitation', 'ContestTeamMember', 'ContestTeamLocation']


class Team(models.Model):
    name = models.CharField(_('Nombre del equipo'), max_length=60)
    owner = models.ForeignKey('Profile', on_delete=models.PROTECT, related_name='owned_teams')
    is_active = models.BooleanField(default=True, db_index=True)
    created = models.DateTimeField(default=timezone.now)
    members = models.ManyToManyField('Profile', through='TeamMembership', related_name='teams')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('team_detail', args=[self.pk])

    class Meta:
        ordering = ('name', 'id')
        verbose_name = _('equipo')
        verbose_name_plural = _('equipos')


class TeamMembership(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships')
    profile = models.ForeignKey('Profile', on_delete=models.CASCADE, related_name='team_memberships')
    joined = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (('team', 'profile'),)


class TeamInvitation(models.Model):
    PENDING, ACCEPTED, DECLINED, CANCELLED = 'P', 'A', 'D', 'C'
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='invitations')
    sender = models.ForeignKey('Profile', on_delete=models.CASCADE, related_name='sent_team_invitations')
    recipient = models.ForeignKey('Profile', on_delete=models.CASCADE, related_name='team_invitations')
    status = models.CharField(max_length=1, default=PENDING, choices=(
        (PENDING, _('Pendiente')), (ACCEPTED, _('Aceptada')),
        (DECLINED, _('Rechazada')), (CANCELLED, _('Cancelada')),
    ))
    created = models.DateTimeField(default=timezone.now)
    responded = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created', '-id')
        indexes = [models.Index(fields=['recipient', 'status'], name='team_invite_inbox')]


class ContestTeamMember(models.Model):
    """Immutable roster of one attempt; changing a team never rewrites contest history."""
    participation = models.ForeignKey('ContestParticipation', on_delete=models.CASCADE, related_name='team_roster')
    profile = models.ForeignKey('Profile', on_delete=models.PROTECT, related_name='team_contest_entries')
    username = models.CharField(max_length=150)

    class Meta:
        unique_together = (('participation', 'profile'),)


class ContestTeamLocation(models.Model):
    contest = models.ForeignKey('Contest', on_delete=models.CASCADE, related_name='team_locations')
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name='balloon_locations')
    location = models.CharField(max_length=60)

    class Meta:
        unique_together = (('contest', 'team'),)

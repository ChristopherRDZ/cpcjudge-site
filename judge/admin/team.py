from django.contrib import admin
from django.db.models import Count
from django.forms import ModelForm
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html_join
from django.utils.translation import gettext, gettext_lazy as _

from judge.admin.purge import PurgeMixin, purge_team, team_plan
from judge.models.team import Team, TeamInvitation, TeamMembership
from judge.widgets import AdminHeavySelect2Widget


class TeamMembershipForm(ModelForm):
    class Meta:
        widgets = {
            'profile': AdminHeavySelect2Widget(data_view='profile_select2'),
        }
        labels = {
            'profile': _('Integrante'),
            'joined': _('Se unió'),
        }


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    form = TeamMembershipForm
    fields = ('profile', 'joined')
    extra = 0
    verbose_name = _('Integrante')
    verbose_name_plural = _('Integrantes')


class TeamForm(ModelForm):
    class Meta:
        widgets = {
            'owner': AdminHeavySelect2Widget(data_view='profile_select2'),
        }
        labels = {
            'owner': _('Propietario'),
            'is_active': _('Activo'),
            'created': _('Creado'),
        }


class TeamAdmin(PurgeMixin, admin.ModelAdmin):
    form = TeamForm
    fields = ('name', 'owner', 'is_active', 'created', 'show_participations')
    readonly_fields = ('show_participations',)
    list_display = ('name', 'show_owner', 'show_members', 'show_participation_count', 'show_active', 'show_created')
    list_filter = ('is_active', ('created', admin.DateFieldListFilter))
    search_fields = ('name', 'owner__user__username', 'members__user__username')
    ordering = ('-created', '-id')
    inlines = (TeamMembershipInline,)
    actions = ('make_inactive', 'make_active', 'purge_teams')
    owner_only_actions = ('purge_teams',)
    purge_action_name = 'purge_teams'
    purge_title = _('Eliminar equipos definitivamente')
    purge_warning = _('Se borra también el historial de concursos del equipo. No se puede deshacer y no queda '
                      'copia. Los envíos en sí se conservan en el historial personal de cada quien; lo que '
                      'desaparece es su vínculo con el concurso.')
    actions_on_top = True
    actions_on_bottom = True
    # get_absolute_url points at team_detail, which 404s for anyone who is not a
    # member: a broken "view on site" button is worse than no button.
    view_on_site = False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('owner__user').annotate(
            member_count=Count('memberships', distinct=True),
            participation_count=Count('participations', distinct=True),
        )

    @admin.display(description=_('Propietario'), ordering='owner__user__username')
    def show_owner(self, obj):
        return obj.owner.user.username

    @admin.display(description=_('Integrantes'), ordering='member_count')
    def show_members(self, obj):
        return obj.member_count

    @admin.display(description=_('Participaciones'), ordering='participation_count')
    def show_participation_count(self, obj):
        return obj.participation_count

    @admin.display(description=_('Activo'), boolean=True, ordering='is_active')
    def show_active(self, obj):
        return obj.is_active

    @admin.display(description=_('Creado'), ordering='created')
    def show_created(self, obj):
        return obj.created

    @admin.display(description=_('Participaciones'))
    def show_participations(self, obj):
        if obj is None or obj.pk is None:
            return gettext('Sin participaciones')
        rows = [(reverse('admin:judge_contestparticipation_change', args=[participation.pk]),
                 '%s (%s)' % (participation.contest.name, participation.team_name or obj.name))
                for participation in obj.participations.select_related('contest').order_by('-id')[:100]]
        if not rows:
            return gettext('Sin participaciones')
        return format_html_join(', ', '<a href="{0}">{1}</a>', rows)

    def _set_active(self, request, queryset, active, message):
        # Materialise the ids: the changelist queryset is annotated, and MariaDB
        # refuses an UPDATE whose subquery reads the table being updated.
        ids = list(queryset.values_list('pk', flat=True))
        count = Team.objects.filter(pk__in=ids).update(is_active=active)
        self.message_user(request, message % count)

    @admin.action(description=_('Desactivar los equipos seleccionados'))
    def make_inactive(self, request, queryset):
        self._set_active(request, queryset, False, gettext('Equipos desactivados: %d.'))

    @admin.action(description=_('Activar los equipos seleccionados'))
    def make_active(self, request, queryset):
        self._set_active(request, queryset, True, gettext('Equipos activados: %d.'))

    # --- borrado total, sólo para la cuenta dueña del servidor ------------------

    def purge_plan(self, obj):
        return team_plan(obj)

    def purge_object(self, obj):
        purge_team(obj)

    @admin.action(description=_('Eliminar definitivamente, con todo su historial'))
    def purge_teams(self, request, queryset):
        return self.run_purge(request, queryset)


class TeamInvitationAdmin(admin.ModelAdmin):
    fields = ('team', 'sender', 'recipient', 'status', 'created', 'responded')
    readonly_fields = fields
    list_display = ('team', 'show_sender', 'show_recipient', 'show_status', 'show_created', 'show_responded')
    list_filter = ('status', ('created', admin.DateFieldListFilter))
    search_fields = ('team__name', 'sender__user__username', 'recipient__user__username')
    ordering = ('-created', '-id')
    actions = ('cancel_invitations',)
    actions_on_top = True
    actions_on_bottom = True

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('team', 'sender__user', 'recipient__user')

    def has_add_permission(self, request):
        # Invitations are created by the team flow, never by hand.
        return False

    @admin.display(description=_('Remitente'), ordering='sender__user__username')
    def show_sender(self, obj):
        return obj.sender.user.username

    @admin.display(description=_('Destinatario'), ordering='recipient__user__username')
    def show_recipient(self, obj):
        return obj.recipient.user.username

    @admin.display(description=_('Estado'), ordering='status')
    def show_status(self, obj):
        return obj.get_status_display()

    @admin.display(description=_('Enviada'), ordering='created')
    def show_created(self, obj):
        return obj.created

    @admin.display(description=_('Respondida'), ordering='responded')
    def show_responded(self, obj):
        return obj.responded

    @admin.action(description=_('Cancelar las invitaciones pendientes seleccionadas'))
    def cancel_invitations(self, request, queryset):
        ids = list(queryset.filter(status=TeamInvitation.PENDING).values_list('pk', flat=True))
        count = TeamInvitation.objects.filter(pk__in=ids).update(status=TeamInvitation.CANCELLED,
                                                                 responded=timezone.now())
        self.message_user(request, gettext('Invitaciones canceladas: %d.') % count)

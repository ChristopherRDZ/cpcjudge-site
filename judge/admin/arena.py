"""Arena in the admin: the «Battles» entry of the side menu and read-mostly lists for emergencies.

Battles are run from the panel of the duels (/duelos/manage/), which keeps every rule in one place: who may play,
what can change while a game runs, how results move a bracket forward. The lists here are for looking things up. Only
superusers can edit a row, and deleting stays with the server owner, as everywhere in this admin.

Who organizes only their own battles (`judge.manage_arena`) sees here only those, with their games, screens and banks;
`judge.manage_all_arena` sees everything (judge/arena/access.py).
"""
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from judge.admin.purge import contest_plan, purge_contest
from judge.arena import access
from judge.models import ArenaGame, Contest


def _can_view(request):
    return access.can_manage(request.user)


class ArenaViewMixin:
    """Organizers see the lists, of what they organize; only superusers change rows; nobody adds rows here."""

    def scoped(self, request, queryset):
        """The rows of the list that `request.user` organizes."""
        return queryset

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if access.manages_all(request.user):
            return queryset
        return self.scoped(request, queryset)

    def has_module_permission(self, request):
        return _can_view(request)

    def has_view_permission(self, request, obj=None):
        return _can_view(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser


class ArenaPanelAdmin(ArenaViewMixin, admin.ModelAdmin):
    """No list of its own: it opens the battle panel."""

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        return HttpResponseRedirect(reverse('arena_manage'))

    def has_change_permission(self, request, obj=None):
        return False


class ArenaMatchAdmin(ArenaViewMixin, admin.ModelAdmin):
    list_display = ('id', 'show_players', 'tournament', 'round', 'best_of', 'show_score', 'status', 'created_at',
                    'panel_link')
    # Only the tournaments of the rows this person sees: a plain 'tournament' offered every tournament by name.
    list_filter = ('status', ('tournament', admin.RelatedOnlyFieldListFilter))
    search_fields = ('player_a__user__username', 'player_b__user__username', 'display_name_a', 'display_name_b',
                     'title')
    raw_id_fields = ('player_a', 'player_b', 'next_match', 'loser_next_match', 'created_by', 'organizers')
    readonly_fields = ('version', 'created_at', 'decided_at', 'called_at')
    list_select_related = ('player_a__user', 'player_b__user', 'tournament', 'round')

    def scoped(self, request, queryset):
        return queryset.filter(pk__in=access.matches_for(request.user).values('pk'))

    @admin.display(description=_('players'))
    def show_players(self, obj):
        return str(obj)

    @admin.display(description=_('games won'))
    def show_score(self, obj):
        return '%d – %d' % (obj.wins_a, obj.wins_b)

    @admin.display(description=_('panel'))
    def panel_link(self, obj):
        return format_html('<a href="{0}"><i class="fa fa-bolt"></i> {1}</a>',
                           reverse('arena_manage_match', args=[obj.pk]), _('Open'))


class ArenaGameAdmin(ArenaViewMixin, admin.ModelAdmin):
    """A battle cannot be deleted while it has games (PROTECT). Filtering by tournament or searching by player
    selects the games of a test at once; after deleting them, the battle or the tournament can go.

    Each game takes its hidden contest with it (2026-10-19), with the same purge as the list of contests: until then
    the contests stayed behind and filled the list of contests."""
    list_display = ('id', 'match', 'show_tournament', 'number', 'status', 'winner_slot', 'reason', 'started_at',
                    'contest')
    list_filter = ('status', ('match__tournament', admin.RelatedOnlyFieldListFilter))
    search_fields = ('match__player_a__user__username', 'match__player_b__user__username', 'match__display_name_a',
                     'match__display_name_b', 'match__title', 'match__tournament__name')
    raw_id_fields = ('match', 'contest', 'participation_a', 'participation_b', 'decided_by')
    readonly_fields = ('created_at',)
    list_select_related = ('match__player_a__user', 'match__player_b__user', 'match__tournament', 'contest')

    def scoped(self, request, queryset):
        return queryset.filter(match__in=access.matches_for(request.user).values('pk'))

    @admin.display(description=_('tournament'), ordering='match__tournament__name')
    def show_tournament(self, obj):
        return obj.match.tournament.name if obj.match.tournament_id else ''

    def has_delete_permission(self, request, obj=None):
        # A game in its countdown, being played or waiting for its last verdicts is cancelled from the panel first.
        if obj is not None and obj.status in ArenaGame.ACTIVE_STATUSES:
            return False
        return super().has_delete_permission(request, obj)

    def get_deleted_objects(self, objs, request):
        # The confirmation also says which contest goes with each game, and what hangs from it, counted.
        objs = list(objs)
        to_delete, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        contests = [game.contest for game in objs if game.contest_id]
        for contest in contests:
            to_delete.append(format_html('{}: {}', capfirst(Contest._meta.verbose_name), contest))
            to_delete.append(['%s: %d' % (label, items.count()) for label, items in contest_plan(contest).items()])
        if contests:
            name = Contest._meta.verbose_name_plural
            model_count[name] = model_count.get(name, 0) + len(contests)
        return to_delete, model_count, perms_needed, protected

    def delete_model(self, request, obj):
        contest = obj.contest
        with transaction.atomic():
            super().delete_model(request, obj)
            if contest is not None:
                purge_contest(contest)

    def delete_queryset(self, request, queryset):
        contests = list(Contest.objects.filter(arena_game__in=queryset))
        with transaction.atomic():
            super().delete_queryset(request, queryset)
            for contest in contests:
                purge_contest(contest)


class ArenaTournamentAdmin(ArenaViewMixin, admin.ModelAdmin):
    list_display = ('name', 'slug', 'status', 'show_in_history', 'seeding', 'created_at', 'panel_link')
    list_filter = ('status', 'show_in_history')
    search_fields = ('name', 'slug')
    raw_id_fields = ('created_by', 'bank', 'organizers')
    readonly_fields = ('version', 'created_at', 'started_at', 'finished_at')

    def scoped(self, request, queryset):
        return queryset.filter(pk__in=access.tournaments_for(request.user).values('pk'))

    @admin.display(description=_('panel'))
    def panel_link(self, obj):
        return format_html('<a href="{0}"><i class="fa fa-bolt"></i> {1}</a>',
                           reverse('arena_manage_tournament', args=[obj.slug]), _('Open'))


class ArenaProblemBankAdmin(ArenaViewMixin, admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at')
    search_fields = ('name',)
    raw_id_fields = ('created_by',)

    def scoped(self, request, queryset):
        return queryset.filter(pk__in=access.banks_for(request.user).values('pk'))


class ArenaScreenAdmin(ArenaViewMixin, admin.ModelAdmin):
    list_display = ('name', 'mode', 'match', 'tournament', 'follow_live', 'updated_at')
    # The token is the key of the screen: it is never shown here, only in the panel to organizers.
    exclude = ('token',)
    raw_id_fields = ('match', 'tournament', 'created_by')
    readonly_fields = ('version',)

    def scoped(self, request, queryset):
        return queryset.filter(pk__in=access.screens_for(request.user).values('pk'))


__all__ = ['ArenaPanelAdmin', 'ArenaMatchAdmin', 'ArenaGameAdmin', 'ArenaTournamentAdmin', 'ArenaProblemBankAdmin',
           'ArenaScreenAdmin']

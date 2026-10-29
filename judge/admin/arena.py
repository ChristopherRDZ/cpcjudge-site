"""Arena in the admin: the «Battles» entry of the side menu and read-mostly lists for emergencies.

Battles are run from the panel of the duels (/duelos/manage/), which keeps every rule in one place: who may play,
what can change while a game runs, how results move a bracket forward. The lists here are for looking things up. Only
superusers can edit a row, and deleting stays with the server owner, as everywhere in this admin.

Who organizes only their own battles (`judge.manage_arena`) sees here only those, with their games, screens and banks;
`judge.manage_all_arena` sees everything (judge/arena/access.py).

Whether a tournament or a quick battle counts for the ranking of the duels (2026-10-26) shows in its list, with a
filter, and changes only with the actions of the list, for whoever has `judge.decide_arena_ranking`: they go through
judge/arena/rating.py, which works the ranking out again. The field itself is read only, even for superusers, so
the ranking never misses a change.
"""
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from judge.admin.purge import contest_plan, purge_contest
from judge.arena import access, engine, rating
from judge.arena.engine import ArenaError
from judge.models import ArenaGame, Contest
from judge.models.arena import RANKING_COUNTS, RANKING_EXCLUDED, RANKING_PENDING


def _can_view(request):
    return access.can_manage(request.user)


def _panel_column(columns, link, closed):
    """The columns of a list with the link to the panel left empty on the rows in `closed`: those whose panel is
    closed to whoever looks because they play them (judge/arena/access.py, 2026-10-28, round 2 of the adversarial
    review), as the battle panel leaves them out, so no link leads to a page that does not exist."""
    @admin.display(description=_('panel'))
    def panel_link(obj):
        return '' if obj.pk in closed else link(obj)
    return [panel_link if column == 'panel_link' else column for column in columns]


class ArenaViewMixin:
    """Organizers see the lists, of what they organize; only superusers change rows; nobody adds rows here."""

    def sees_everything(self, request):
        return access.manages_all(request.user)

    def scoped(self, request, queryset):
        """The rows of the list that `request.user` organizes."""
        return queryset

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if self.sees_everything(request):
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


class RecomputeMixin:
    """A superuser who edits or deletes a battle or a game here goes around judge/arena/engine.py (2026-10-28): the
    ranking of the duels is worked out again (duelos-web-5; deleting a battle took its rating changes away while
    the ratings kept counting it) and the players held inside the arena are read again from the database
    (duelos-gestion-js-1), once the change is saved."""

    def _after_change(self):
        rating.recompute_on_commit()
        engine.refresh_lock_on_commit()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        self._after_change()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        self._after_change()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        self._after_change()


class RankingActionsMixin:
    """The three decisions about the ranking of the duels as actions of the list, only for whoever may decide.

    Whoever may decide sees every battle and tournament in these two lists, even without organizing any (duelos-web-3,
    2026-10-28): until then the lists only opened to organizers, so the permission alone never reached its actions.
    The rows show who played and how it ended, never statements, code or submissions; only superusers change them."""
    ranking_setter = None

    def has_decide_ranking_permission(self, request):
        return access.decides_ranking(request.user)

    def sees_everything(self, request):
        return super().sees_everything(request) or access.decides_ranking(request.user)

    def has_module_permission(self, request):
        return super().has_module_permission(request) or access.decides_ranking(request.user)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) or access.decides_ranking(request.user)

    def _decide(self, request, queryset, value):
        done, refused = 0, []
        for obj in queryset:
            try:
                type(self).ranking_setter(request.profile, obj.pk, value)
                done += 1
            except ArenaError as error:
                refused.append('%s: %s' % (obj, error.message))
        if done:
            self.message_user(request, _('Decision saved for %(count)d rows. The ranking is worked out again by '
                                         'itself.') % {'count': done}, messages.SUCCESS)
        for text in refused[:10]:
            self.message_user(request, text, messages.WARNING)

    @admin.action(description=_('Counts for the ranking of the duels'), permissions=['decide_ranking'])
    def ranking_counts(self, request, queryset):
        self._decide(request, queryset, RANKING_COUNTS)

    @admin.action(description=_('Does not count for the ranking of the duels'), permissions=['decide_ranking'])
    def ranking_excluded(self, request, queryset):
        self._decide(request, queryset, RANKING_EXCLUDED)

    @admin.action(description=_('Back to not decided yet'), permissions=['decide_ranking'])
    def ranking_pending(self, request, queryset):
        self._decide(request, queryset, RANKING_PENDING)


class ArenaPanelAdmin(ArenaViewMixin, admin.ModelAdmin):
    """No list of its own: it opens the battle panel."""

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        return HttpResponseRedirect(reverse('arena_manage'))

    def has_change_permission(self, request, obj=None):
        return False


class ArenaMatchAdmin(RankingActionsMixin, RecomputeMixin, ArenaViewMixin, admin.ModelAdmin):
    list_display = ('id', 'show_players', 'tournament', 'round', 'best_of', 'show_score', 'status', 'show_ranking',
                    'created_at', 'panel_link')
    # Only the tournaments of the rows this person sees: a plain 'tournament' offered every tournament by name.
    list_filter = ('status', 'ranking', ('tournament', admin.RelatedOnlyFieldListFilter))
    search_fields = ('player_a__user__username', 'player_b__user__username', 'display_name_a', 'display_name_b',
                     'title')
    raw_id_fields = ('player_a', 'player_b', 'next_match', 'loser_next_match', 'created_by', 'organizers')
    readonly_fields = ('version', 'created_at', 'decided_at', 'called_at', 'ranking')
    list_select_related = ('player_a__user', 'player_b__user', 'tournament', 'round')
    actions = ('ranking_counts', 'ranking_excluded', 'ranking_pending')
    ranking_setter = rating.set_match_ranking

    def scoped(self, request, queryset):
        return queryset.filter(pk__in=access.matches_for(request.user).values('pk'))

    def get_list_display(self, request):
        return _panel_column(super().get_list_display(request), self.panel_link,
                             access.closed_match_ids(request.user))

    @admin.display(description=_('players'))
    def show_players(self, obj):
        return str(obj)

    @admin.display(description=_('games won'))
    def show_score(self, obj):
        return '%d – %d' % (obj.wins_a, obj.wins_b)

    @admin.display(description=_('ranking of the duels'))
    def show_ranking(self, obj):
        """What counts in the end: a battle of a tournament goes with its tournament unless it was left out."""
        if rating.never_reason(obj) is not None:
            return _('Never counts')
        if obj.tournament_id and obj.ranking != RANKING_EXCLUDED:
            return _('As its tournament: %(decision)s') % {'decision': obj.tournament.get_ranking_display()}
        return obj.get_ranking_display()

    @admin.display(description=_('panel'))
    def panel_link(self, obj):
        return format_html('<a href="{0}"><i class="fa fa-bolt"></i> {1}</a>',
                           reverse('arena_manage_match', args=[obj.pk]), _('Open'))


class ArenaGameAdmin(RecomputeMixin, ArenaViewMixin, admin.ModelAdmin):
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


class ArenaTournamentAdmin(RankingActionsMixin, ArenaViewMixin, admin.ModelAdmin):
    list_display = ('name', 'slug', 'status', 'show_in_history', 'ranking', 'seeding', 'created_at', 'panel_link')
    list_filter = ('status', 'show_in_history', 'ranking')
    search_fields = ('name', 'slug')
    raw_id_fields = ('created_by', 'bank', 'organizers')
    readonly_fields = ('version', 'created_at', 'started_at', 'finished_at', 'ranking')
    actions = ('ranking_counts', 'ranking_excluded', 'ranking_pending')
    ranking_setter = rating.set_tournament_ranking

    def scoped(self, request, queryset):
        return queryset.filter(pk__in=access.tournaments_for(request.user).values('pk'))

    def get_list_display(self, request):
        return _panel_column(super().get_list_display(request), self.panel_link,
                             set(access.contended_tournaments(request.user).values_list('pk', flat=True)))

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

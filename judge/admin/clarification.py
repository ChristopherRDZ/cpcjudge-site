from django.contrib import admin
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class ContestClarificationAdmin(admin.ModelAdmin):
    fields = ('contest', 'problem', 'user', 'question', 'answer', 'is_public')
    list_display = ('asked', 'contest', 'user', 'summary', 'answered', 'is_public')
    list_filter = ('is_public', 'contest')
    search_fields = ('question', 'answer')
    date_hierarchy = 'asked'
    raw_id_fields = ('user', 'problem')

    def summary(self, obj):
        return obj.question if len(obj.question) <= 80 else obj.question[:77] + '...'
    summary.short_description = _('question')

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('contest', 'user__user', 'answered_by__user')
        if request.user.has_perm('judge.edit_all_contest'):
            return queryset
        # Same rule the rest of the admin uses: your own contests only.
        return queryset.filter(
            Q(contest__authors=request.profile) | Q(contest__curators=request.profile),
        ).distinct()

    def save_model(self, request, obj, form, change):
        # The tab is the normal way to answer; this keeps the record straight for
        # anyone who answers from the admin instead.
        if obj.answer and obj.answered is None:
            obj.answered = timezone.now()
            obj.answered_by = request.profile
        super().save_model(request, obj, form, change)

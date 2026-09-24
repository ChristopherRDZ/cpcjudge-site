from django import forms
from django.contrib import admin
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from judge.models import ContestClarification


class ContestClarificationForm(forms.ModelForm):
    class Meta:
        model = ContestClarification
        fields = ('contest', 'problem', 'user', 'question', 'answer', 'is_public')

    def clean(self):
        cleaned_data = super().clean()
        contest = cleaned_data.get('contest')
        problem = cleaned_data.get('problem')
        # `problem` is a raw id field, so the browser can name any contest problem
        # at all. A question filed against another contest's problem would show
        # that problem's name on this contest's page.
        if contest is not None and problem is not None and problem.contest_id != contest.pk:
            self.add_error('problem', _('That problem belongs to a different contest.'))
        return cleaned_data


class ContestClarificationAdmin(admin.ModelAdmin):
    form = ContestClarificationForm
    fields = ('contest', 'problem', 'user', 'question', 'answer', 'is_public')
    list_display = ('asked', 'contest', 'user', 'summary', 'answered', 'is_public')
    list_filter = ('is_public', 'contest')
    search_fields = ('question', 'answer')
    date_hierarchy = 'asked'
    raw_id_fields = ('user', 'problem')

    def summary(self, obj):
        return obj.question if len(obj.question) <= 80 else obj.question[:77] + '...'
    summary.short_description = _('question')

    def editable_contests(self, request):
        """The contests this account may write a clarification into.

        Same rule the rest of the admin uses: your own contests only.
        """
        from judge.models import Contest

        contests = Contest.objects.all()
        if request.user.has_perm('judge.edit_all_contest'):
            return contests
        return contests.filter(
            Q(authors=request.profile) | Q(curators=request.profile),
        ).distinct()

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('contest', 'user__user', 'answered_by__user')
        if request.user.has_perm('judge.edit_all_contest'):
            return queryset
        return queryset.filter(
            Q(contest__authors=request.profile) | Q(contest__curators=request.profile),
        ).distinct()

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Narrowing which rows can be opened was never enough: the destination is
        # chosen by the `contest` field, and the browser can name any contest at
        # all. Restricting the field's queryset is what rejects a foreign one on
        # the server, both when adding and when changing, because that is where
        # a ModelChoiceField validates its value.
        if 'contest' in form.base_fields:
            form.base_fields['contest'].queryset = self.editable_contests(request)
            form.base_fields['contest'].required = True
        return form

    def save_model(self, request, obj, form, change):
        # The tab is the normal way to answer; this keeps the record straight for
        # anyone who answers from the admin instead.
        if obj.answer and obj.answered is None:
            obj.answered = timezone.now()
            obj.answered_by = request.profile
        super().save_model(request, obj, form, change)
        # And it publishes or withdraws through the same helper as the tab. Saving
        # a public answer here used to reach nobody —the tab hides it, expecting
        # an announcement that was never created— and turning one private used to
        # leave the announcement behind.
        obj.sync_announcement(author=request.profile)

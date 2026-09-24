from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _

from judge.models.contest import Contest, ContestProblem
from judge.models.profile import Profile

__all__ = ['ContestClarification']

# The longest question the form will take. Generous for a real question, small
# enough that the box cannot be used to dump data into the contest.
MAX_QUESTION = 1000

# What keeps one participant from flooding the jury: how many of their questions
# may be waiting for an answer at once, and how long between two questions.
MAX_PENDING_PER_USER = 3
MIN_SECONDS_BETWEEN = 30


class ContestClarification(models.Model):
    contest = models.ForeignKey(Contest, verbose_name=_('contest'), related_name='clarifications',
                                on_delete=models.CASCADE)
    problem = models.ForeignKey(ContestProblem, verbose_name=_('problem'), null=True, blank=True,
                                on_delete=models.CASCADE, related_name='clarifications',
                                help_text=_('Leave empty for a question about the contest as a whole.'))
    user = models.ForeignKey(Profile, verbose_name=_('asked by'), related_name='clarifications',
                             on_delete=models.CASCADE)
    team_participation = models.ForeignKey('ContestParticipation', null=True, blank=True,
                                          related_name='team_clarifications', on_delete=models.PROTECT)
    question = models.TextField(verbose_name=_('question'))
    asked = models.DateTimeField(verbose_name=_('asked at'), auto_now_add=True, db_index=True)
    answer = models.TextField(verbose_name=_('answer'), blank=True)
    answered_by = models.ForeignKey(Profile, verbose_name=_('answered by'), null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='answered_clarifications')
    answered = models.DateTimeField(verbose_name=_('answered at'), null=True, blank=True, db_index=True)
    is_public = models.BooleanField(verbose_name=_('answer is public'), default=False,
                                    help_text=_('A public answer is shown to every contestant. A private one only '
                                                'goes back to whoever asked.'))

    @property
    def participant_name(self):
        return self.team_participation.team_name if self.team_participation_id else self.user.username

    @staticmethod
    def ownership_filter(profile):
        if profile is None:
            return models.Q(pk__in=[])
        return (models.Q(user=profile, team_participation__isnull=True) |
                models.Q(team_participation_id__in=profile.team_contest_entries.values('participation_id')))

    @property
    def is_answered(self):
        return bool(self.answer)

    def sync_announcement(self, author=None):
        """Make the announcements match this answer's visibility.

        Visibility and delivery are one decision, so both places that edit an
        answer —the contest tab and the admin— end up here and cannot drift.
        A public answer travels as an announcement; a private one leaves none
        behind. Answering publicly twice updates the announcement already sent
        instead of adding a second one.

        Nothing is deleted: an announcement that no longer applies is withdrawn
        with `is_visible`, which is what that field is for, so the record that
        it was sent survives.
        """
        from judge.models.announcement import Announcement

        announcements = Announcement.objects.filter(clarification=self)
        if not (self.is_public and self.answer):
            announcements.filter(is_visible=True).update(is_visible=False)
            return None

        current = announcements.order_by('created', 'pk').first()
        if current is None:
            return Announcement.objects.create(
                contest=self.contest,
                clarification=self,
                body='',
                author=author,
                expires=self.contest.end_time,
            )

        # Re-publishing an answer that was withdrawn, or correcting one: the same
        # announcement is reused so nobody gets the question twice.
        updated = []
        if not current.is_visible:
            current.is_visible = True
            updated.append('is_visible')
        # The question can be moved to another contest from the admin. Reusing the
        # announcement without bringing it along left the new answer on show to the
        # old contest, labelled as theirs: "public" means public to the participants
        # of its own contest, and says nothing about anybody else's.
        if current.contest_id != self.contest_id:
            current.contest = self.contest
            updated.append('contest')
        if current.expires != self.contest.end_time:
            current.expires = self.contest.end_time
            updated.append('expires')
        if updated:
            current.save(update_fields=updated)
        announcements.exclude(pk=current.pk).filter(is_visible=True).update(is_visible=False)
        return current

    def __str__(self):
        question = self.question if len(self.question) <= 60 else self.question[:57] + '...'
        return '%s: %s' % (self.contest.key, question)

    class Meta:
        ordering = ['-asked']
        indexes = [
            # The contest tab asks for "this contest, newest first" on every load.
            # Named explicitly: an unnamed index gets a hashed name that has to
            # match between model and migration, and there is no way to work that
            # hash out without running Django.
            models.Index(fields=['contest', '-asked'], name='clarification_contest_asked'),
        ]
        verbose_name = _('contest clarification')
        verbose_name_plural = _('contest clarifications')


@receiver(pre_delete, sender=ContestClarification, dispatch_uid='withdraw_deleted_clarification_announcement')
def withdraw_deleted_clarification_announcement(sender, instance, **kwargs):
    """Withdraw the announcement of a clarification that is being deleted.

    `Announcement.clarification` is SET_NULL, so without this the announcement outlived its question as an
    empty hand-written one. A signal rather than `delete()`, because most of these deletions are cascades —
    removing the contest problem the question was about, purging a team— and those never call `delete()`.
    Withdrawn, not deleted, as `sync_announcement` does: the record that it was sent survives.
    """
    from judge.models.announcement import Announcement

    Announcement.objects.filter(clarification=instance, is_visible=True).update(is_visible=False)

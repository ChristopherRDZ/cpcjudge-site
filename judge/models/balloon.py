from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from judge.models.contest import Contest, ContestParticipation, ContestProblem
from judge.models.profile import Profile

__all__ = ['ContestBalloonAction', 'ContestLocation']

# Long enough for "Lab 3, row B, seat 12", short enough to fit the sheet on a phone.
MAX_LOCATION = 60


class ContestBalloonAction(models.Model):
    """One entry of the balloon log: somebody delivered a balloon, or took a delivery back.

    Nothing here is ever updated or deleted. Whether a balloon counts as delivered is whatever its latest entry
    says, so a mistaken delivery that gets undone stays on record together with who undid it.

    There is no row for a balloon that is due: that comes straight from the accepted submissions every time the
    sheet is read, so a rejudge can never leave a stale balloon behind.
    """
    DELIVERED = 'D'
    UNDONE = 'U'
    ACTIONS = (
        (DELIVERED, _('Delivered')),
        (UNDONE, _('Delivery undone')),
    )

    participation = models.ForeignKey(ContestParticipation, verbose_name=_('participation'),
                                      related_name='balloon_actions', on_delete=models.CASCADE)
    problem = models.ForeignKey(ContestProblem, verbose_name=_('problem'), related_name='balloon_actions',
                                on_delete=models.CASCADE)
    action = models.CharField(verbose_name=_('action'), max_length=1, choices=ACTIONS)
    user = models.ForeignKey(Profile, verbose_name=_('marked by'), related_name='+', null=True,
                             on_delete=models.SET_NULL)
    time = models.DateTimeField(verbose_name=_('marked at'), default=timezone.now)

    def __str__(self):
        return '%s %s %s' % (self.participation_id, self.problem_id, self.action)

    class Meta:
        ordering = ('time', 'id')
        verbose_name = _('balloon action')
        verbose_name_plural = _('balloon actions')


class ContestLocation(models.Model):
    """Where a contestant sits during an on-site contest, so the balloon staff can find them.

    Kept apart from the participation because it is set up before the contest starts, when most contestants have
    not joined yet and so have no participation to hang it on.
    """
    contest = models.ForeignKey(Contest, verbose_name=_('contest'), related_name='locations',
                                on_delete=models.CASCADE)
    user = models.ForeignKey(Profile, verbose_name=_('user'), related_name='+', on_delete=models.CASCADE)
    location = models.CharField(verbose_name=_('location'), max_length=MAX_LOCATION)

    def __str__(self):
        return '%s: %s' % (self.user_id, self.location)

    class Meta:
        unique_together = ('contest', 'user')
        verbose_name = _('contest location')
        verbose_name_plural = _('contest locations')

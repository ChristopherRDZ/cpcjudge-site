from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.models.contest import Contest
from judge.models.profile import Profile

__all__ = ['Announcement']


class Announcement(models.Model):
    contest = models.ForeignKey(Contest, verbose_name=_('contest'), null=True, blank=True,
                                on_delete=models.CASCADE, related_name='announcements',
                                help_text=_('Leave empty to announce to the whole site. Otherwise the announcement '
                                            'only reaches that contest, and is kept on its announcements tab.'))
    body = models.TextField(verbose_name=_('announcement'), blank=True,
                            help_text=_('Shown as plain text: HTML and Markdown are not interpreted. Leave empty '
                                        'only on an announcement that carries a clarification.'))
    # When the jury answers a question in public, the announcement points at the
    # clarification instead of copying its text. That way the page can label
    # which half is the question and which is the answer, in the reader's own
    # language, and the same words are not stored twice.
    clarification = models.ForeignKey('ContestClarification', verbose_name=_('clarification'), null=True, blank=True,
                                      on_delete=models.SET_NULL, related_name='announcements')
    author = models.ForeignKey(Profile, verbose_name=_('author'), null=True, blank=True,
                               on_delete=models.SET_NULL, related_name='announcements')
    created = models.DateTimeField(verbose_name=_('creation time'), auto_now_add=True, db_index=True)
    expires = models.DateTimeField(verbose_name=_('stop showing at'), null=True, blank=True, db_index=True,
                                   help_text=_('After this moment the announcement stops popping up. It is still '
                                               'kept on the contest tab. Empty means it never stops.'))
    is_visible = models.BooleanField(verbose_name=_('is visible'), default=True,
                                     help_text=_('Uncheck to withdraw an announcement without deleting it, so there '
                                                 'is still a record that it was sent.'))

    def __str__(self):
        where = self.contest.name if self.contest_id else _('Site')
        text = self.body or (self.clarification.question if self.clarification_id else '')
        if len(text) > 60:
            text = text[:57] + '...'
        return '%s: %s' % (where, text)

    class Meta:
        ordering = ['-created']
        permissions = (
            ('announce_site', _('Announce to the whole site')),
        )
        verbose_name = _('announcement')
        verbose_name_plural = _('announcements')

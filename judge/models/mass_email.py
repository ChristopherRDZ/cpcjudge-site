from django.contrib.auth.models import Group
from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.models.contest import Contest
from judge.models.profile import Organization, Profile

__all__ = ['MassEmail', 'MassEmailRecipient']


class MassEmail(models.Model):
    """An email written in the admin and sent to many users at once, through Resend Broadcasts.

    Only accounts that confirmed their email can receive it. Without filters it goes to all of them; each filter
    narrows the list, and within a filter with several choices (contests, organizations, groups) one is enough.
    """
    DRAFT, SENDING, SCHEDULED, SENT, FAILED, CANCELLED = 'draft', 'sending', 'scheduled', 'sent', 'failed', 'cancelled'
    STATUS = (
        (DRAFT, _('Draft')),
        (SENDING, _('Sending')),
        (SCHEDULED, _('Scheduled')),
        (SENT, _('Sent')),
        (FAILED, _('Failed')),
        (CANCELLED, _('Cancelled')),
    )
    LANGUAGES = (
        ('es', _('Spanish')),
        ('en', _('English')),
    )

    subject = models.CharField(verbose_name=_('subject'), max_length=150)
    preheader = models.CharField(verbose_name=_('preview text'), max_length=200, blank=True,
                                 help_text=_('Shown by most inboxes next to the subject. Optional.'))
    heading = models.CharField(verbose_name=_('title'), max_length=150, blank=True,
                               help_text=_('Big title at the top of the email. Leave empty for no title.'))
    content = models.TextField(verbose_name=_('content'))
    language = models.CharField(verbose_name=_('language of the footer'), max_length=7, choices=LANGUAGES,
                                default='es',
                                help_text=_('The language of the lines the site adds at the end, such as the '
                                            'unsubscribe link. Write the content in the language you prefer.'))

    contests = models.ManyToManyField(Contest, verbose_name=_('took part in any of these contests'), blank=True,
                                      related_name='+',
                                      help_text=_('Registered in or took part in at least one of them, team members '
                                                  'included. Spectators do not count.'))
    include_virtual = models.BooleanField(verbose_name=_('count virtual participations'), default=False,
                                          help_text=_('Also people who did those contests in virtual mode, after '
                                                      'they ended.'))
    organizations = models.ManyToManyField(Organization, verbose_name=_('member of any of these organizations'),
                                           blank=True, related_name='+')
    groups = models.ManyToManyField(Group, verbose_name=_('in any of these permission groups'), blank=True,
                                    related_name='+',
                                    help_text=_('For example, to write only to the people who create problems.'))
    active_within_days = models.PositiveIntegerField(verbose_name=_('visited the site in the last days'), null=True,
                                                     blank=True,
                                                     help_text=_('For example 90: only people who opened the site '
                                                                 'in the last 90 days.'))
    joined_after = models.DateField(verbose_name=_('signed up on or after'), null=True, blank=True)
    joined_before = models.DateField(verbose_name=_('signed up on or before'), null=True, blank=True)
    min_problems = models.PositiveIntegerField(verbose_name=_('solved at least this many problems'), null=True,
                                               blank=True)
    include_users = models.ManyToManyField(Profile, verbose_name=_('always include'), blank=True,
                                           related_name='+',
                                           help_text=_('They receive it even if the filters leave them out, as long '
                                                       'as their email is confirmed.'))
    exclude_users = models.ManyToManyField(Profile, verbose_name=_('never include'), blank=True, related_name='+')

    status = models.CharField(verbose_name=_('status'), max_length=10, choices=STATUS, default=DRAFT, db_index=True)
    created_by = models.ForeignKey(Profile, verbose_name=_('written by'), null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')
    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True)
    updated = models.DateTimeField(verbose_name=_('last change'), auto_now=True)
    sent_by = models.ForeignKey(Profile, verbose_name=_('sent by'), null=True, blank=True,
                                on_delete=models.SET_NULL, related_name='+')
    sent_at = models.DateTimeField(verbose_name=_('sent at'), null=True, blank=True)
    scheduled_at = models.DateTimeField(verbose_name=_('scheduled for'), null=True, blank=True)
    from_email = models.CharField(verbose_name=_('sender'), max_length=254, blank=True)
    recipient_count = models.PositiveIntegerField(verbose_name=_('recipients'), null=True, blank=True)
    task_id = models.CharField(max_length=64, blank=True)
    resend_segment_id = models.CharField(max_length=64, blank=True)
    resend_broadcast_id = models.CharField(max_length=64, blank=True)
    # Set right before asking Resend for the broadcast. If the answer is lost, a retry looks the broadcast up by
    # name instead of creating a second one, which would send everything twice.
    broadcast_requested = models.BooleanField(default=False)
    resend_status = models.CharField(max_length=20, blank=True)
    stats = models.TextField(blank=True)
    stats_updated = models.DateTimeField(null=True, blank=True)
    error = models.TextField(verbose_name=_('error'), blank=True)

    class Meta:
        ordering = ('-id',)
        permissions = (
            ('send_mass_email', _('Write and send emails to users')),
        )
        verbose_name = _('email to users')
        verbose_name_plural = _('emails to users')

    def __str__(self):
        return self.subject

    @property
    def is_editable(self):
        return self.status == self.DRAFT

    @property
    def broadcast_name(self):
        """How the broadcast is called in Resend; also how a retry finds it."""
        return 'cpcjudge-%d: %s' % (self.pk, self.subject[:80])


class MassEmailRecipient(models.Model):
    """Who an email was handed to, kept as it was at that moment."""
    PENDING, ADDED, UNSUBSCRIBED, FAILED = 'pending', 'added', 'unsubscribed', 'failed'
    STATES = (
        (PENDING, _('Pending')),
        (ADDED, _('Handed to Resend')),
        (UNSUBSCRIBED, _('Unsubscribed')),
        (FAILED, _('Failed')),
    )

    mass_email = models.ForeignKey(MassEmail, related_name='recipients', on_delete=models.CASCADE)
    profile = models.ForeignKey(Profile, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    username = models.CharField(max_length=150)
    email = models.CharField(max_length=254)
    state = models.CharField(max_length=12, choices=STATES, default=PENDING)
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ('id',)
        unique_together = ('mass_email', 'email')
        verbose_name = _('email recipient')
        verbose_name_plural = _('email recipients')

    def __str__(self):
        return self.username

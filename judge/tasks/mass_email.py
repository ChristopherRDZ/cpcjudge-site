import re
from datetime import timezone as dt_timezone

from celery import shared_task
from django.db import transaction
from django.utils import translation
from django.utils.translation import gettext as _

from judge.mass_email import render_email, reply_to, sender
from judge.models import MassEmail, MassEmailRecipient
from judge.utils.celery import Progress
from judge.utils.resend import ResendClient, ResendError

__all__ = ('send_mass_email',)

# MassEmail.broadcast_name, which is also the name of its segment.
SEGMENT_NAME = re.compile(r'^cpcjudge-(\d+): ')


def _save(mail, **fields):
    for name, value in fields.items():
        setattr(mail, name, value)
    mail.save(update_fields=list(fields) + ['updated'])


@shared_task(bind=True)
def send_mass_email(self, mail_id):
    """Hands an email to Resend: its people to a segment of their own, and then one broadcast to that segment.

    The list of people was fixed when the email was confirmed (MassEmailRecipient). Running this again after a
    failure continues where it stopped: it reuses the segment, skips who is already in it, and never asks for a
    second broadcast.
    """
    with transaction.atomic():
        mail = MassEmail.objects.select_for_update().get(pk=mail_id)
        if mail.status != MassEmail.SENDING:
            return 'not sending: %s' % mail.status
    # Celery has no request, so without this its messages (the progress page, the error kept with the email) came
    # out in the language of the settings, English, on a site in Spanish. The language of the email is the best
    # guess of the language of whoever wrote it.
    with translation.override(mail.language or 'es'):
        try:
            return _send(self, mail, ResendClient())
        except Exception as error:
            message = str(error) if isinstance(error, ResendError) else '%s: %s' % (type(error).__name__, error)
            _save(mail, status=MassEmail.FAILED, error=message[:2000])
            raise


def _free_segments(client, mail):
    """Resend's free plan holds three segments, so the segments of emails that are done are deleted before a new
    one is made: sent and delivered, cancelled, or failed before asking for a broadcast. Scheduled emails and those
    still being sent keep theirs, because Resend reads the segment when it sends."""
    for segment in client.segments():
        match = SEGMENT_NAME.match(segment.get('name') or '')
        if not match or int(match.group(1)) == mail.pk:
            continue
        other = MassEmail.objects.filter(pk=int(match.group(1))).first()
        if other is None or other.status == MassEmail.CANCELLED:
            done = True
        elif other.status == MassEmail.FAILED:
            done = not other.resend_broadcast_id and not other.broadcast_requested
        elif other.status == MassEmail.SENT and other.resend_broadcast_id:
            done = client.broadcast(other.resend_broadcast_id).get('status') == 'sent'
        else:
            done = False
        if not done:
            continue
        client.delete_segment(segment['id'])
        if other is not None and other.resend_segment_id == segment['id']:
            _save(other, resend_segment_id='')
            # A failed email rebuilds its list in a new segment if it is retried.
            other.recipients.filter(state=MassEmailRecipient.ADDED).update(state=MassEmailRecipient.PENDING)


def _send(task, mail, client):
    pending = mail.recipients.filter(state__in=(MassEmailRecipient.PENDING, MassEmailRecipient.FAILED))
    scheduled_at = mail.scheduled_at.astimezone(dt_timezone.utc).isoformat() if mail.scheduled_at else None
    with Progress(task, pending.count() + 2, stage=_('Handing the email to Resend')) as progress:
        contacts = client.contacts()
        if not mail.resend_segment_id:
            _free_segments(client, mail)
            try:
                _save(mail, resend_segment_id=client.create_segment(mail.broadcast_name))
            except ResendError as error:
                raise ResendError(_('Resend did not create the list of this email (%(error)s). Its free plan holds '
                                    'three lists, called segments: delete the ones left over in Resend and retry.') %
                                  {'error': error}) from error
            pending = mail.recipients.filter(state__in=(MassEmailRecipient.PENDING, MassEmailRecipient.FAILED))
        progress.did(1)

        for recipient in pending.iterator():
            contact = contacts.get(recipient.email.lower())
            try:
                if contact is not None and contact.get('unsubscribed'):
                    recipient.state = MassEmailRecipient.UNSUBSCRIBED
                elif contact is not None:
                    client.add_to_segment(contact['id'], mail.resend_segment_id)
                    recipient.state = MassEmailRecipient.ADDED
                else:
                    client.create_contact(recipient.email, recipient.username, mail.resend_segment_id)
                    recipient.state = MassEmailRecipient.ADDED
                recipient.error = ''
            except ResendError as error:
                recipient.state, recipient.error = MassEmailRecipient.FAILED, str(error)[:255]
            recipient.save(update_fields=['state', 'error'])
            progress.did(1)

        added = mail.recipients.filter(state=MassEmailRecipient.ADDED).count()
        if not added:
            # What Resend answered for the first of them says why, without having to open "Who it was sent to".
            first = mail.recipients.filter(state=MassEmailRecipient.FAILED).exclude(error='') \
                .values_list('error', flat=True).first()
            message = _('Nobody could be added to the list in Resend, so nothing was sent.')
            if first:
                message += ' ' + _('First error: %(error)s') % {'error': first}
            raise ResendError(message)

        if not mail.resend_broadcast_id and mail.broadcast_requested:
            # A previous attempt asked for the broadcast and did not hear back: it may exist already. If it stayed
            # as a draft, it was never sent, and it is sent now.
            found = client.find_broadcast(mail.broadcast_name)
            if found:
                _save(mail, resend_broadcast_id=found)
                if client.broadcast(found).get('status') == 'draft':
                    client.send_broadcast(found, scheduled_at)
        if not mail.resend_broadcast_id:
            subject, html, text = render_email(mail, for_resend=True)
            payload = {'segment_id': mail.resend_segment_id, 'from': mail.from_email or sender(), 'subject': subject,
                       'html': html, 'text': text, 'name': mail.broadcast_name, 'send': True}
            if mail.preheader:
                payload['preview_text'] = mail.preheader
            if reply_to():
                payload['reply_to'] = reply_to()
            if scheduled_at:
                payload['scheduled_at'] = scheduled_at
            _save(mail, broadcast_requested=True)
            _save(mail, resend_broadcast_id=client.create_broadcast(payload, 'cpcjudge-mass-email-%d' % mail.pk))
        progress.did(1)

    _save(mail, status=MassEmail.SCHEDULED if scheduled_at else MassEmail.SENT, recipient_count=added, error='',
          resend_status='scheduled' if scheduled_at else 'queued')
    return added

import logging
import re
import time
from datetime import timezone as dt_timezone

from celery import shared_task
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import CharField, Exists, OuterRef, Subquery, Value
from django.db.models.functions import Concat
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from judge.mass_email import NAME_PLACEHOLDERS, render_email, reply_to, sender
from judge.models import MassEmail, MassEmailRecipient
from judge.utils.celery import Progress
from judge.utils.resend import ResendClient, ResendError, is_configured

__all__ = ('send_mass_email', 'move_resend_contact')
logger = logging.getLogger('judge.tasks.mass_email')

# MassEmail.broadcast_name, which is also the name of its segment.
SEGMENT_NAME = re.compile(r'^cpcjudge-(\d+): ')
# How often a task handing an email to Resend says it is still alive (`updated`). The page offers to retry only after
# 15 minutes without that (judge/admin/mass_email.py, STUCK_AFTER), so a slow delivery is never taken for a dead one.
HEARTBEAT_SECONDS = 60
PENDING_STATES = (MassEmailRecipient.PENDING, MassEmailRecipient.FAILED)
# What cpc-cancelar-cuenta leaves in a row instead of the address of a cancelled account, followed by the number of
# the row (DESTINATARIO_BORRADO there): an email takes no two rows with the same address.
CANCELLED_ADDRESS = 'cuenta-cancelada-'


class Superseded(Exception):
    """Somebody retried the email while this task was still alive: the newer task carries on, and this one stops."""


def _save(mail, **fields):
    for name, value in fields.items():
        setattr(mail, name, value)
    mail.save(update_fields=list(fields) + ['updated'])


def _still_mine(task, mail_id):
    """The email, locked, if this task is still the one handing it over; Superseded otherwise. Inside a transaction.

    Each start and each retry gives the email a new task_id (judge/admin/mass_email.py) and queues the task with it
    (correos-masivos-3, 2026-10-28): until then a retry while the first attempt was still alive, only slow, let both
    reach the broadcast, and everybody received the email twice."""
    current = MassEmail.objects.select_for_update().get(pk=mail_id)
    own = getattr(task.request, 'id', None)
    if current.status != MassEmail.SENDING or (current.task_id and own and current.task_id != own):
        raise Superseded()
    return current


class Heartbeat:
    """Called before each step: stops the task if it was superseded, and every HEARTBEAT_SECONDS keeps `updated`
    fresh, which says that it is alive."""

    def __init__(self, task, mail):
        self.mail, self.last = mail, time.monotonic()
        own = getattr(task.request, 'id', None)
        self.mine = MassEmail.objects.filter(pk=mail.pk, status=MassEmail.SENDING)
        if own and mail.task_id:
            self.mine = self.mine.filter(task_id=own)

    def __call__(self, now=False):
        if not self.mine.exists():
            raise Superseded()
        if now or time.monotonic() - self.last >= HEARTBEAT_SECONDS:
            self.mine.update(updated=timezone.now())
            self.last = time.monotonic()


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
        if mail.task_id and self.request.id and mail.task_id != self.request.id:
            return 'superseded'
        # Alive from the start: after a long wait in the queue, «Retry» was offered while this already ran.
        MassEmail.objects.filter(pk=mail.pk).update(updated=timezone.now())
    # Celery has no request, so without this its messages (the progress page, the error kept with the email) came
    # out in the language of the settings, English, on a site in Spanish. The language of the email is the best
    # guess of the language of whoever wrote it.
    with translation.override(mail.language or 'es'):
        try:
            return _send(self, mail, ResendClient())
        except Superseded:
            return 'superseded'
        except Exception as error:
            message = str(error) if isinstance(error, ResendError) else '%s: %s' % (type(error).__name__, error)
            # Only while the email is still this task's: a retry may have taken it over meanwhile.
            if MassEmail.objects.filter(pk=mail.pk, status=MassEmail.SENDING, task_id=mail.task_id) \
                    .update(status=MassEmail.FAILED, error=message[:2000], updated=timezone.now()):
                mail.status, mail.error = MassEmail.FAILED, message[:2000]
            raise


def _broadcast_status(client, broadcast_id):
    """What Resend says of a broadcast, or None if Resend no longer has it."""
    try:
        return client.broadcast(broadcast_id).get('status') or ''
    except ResendError as error:
        if error.status == 404:
            return None
        raise


def _free_segments(client, mail):
    """Resend's free plan holds three segments, so the segments of emails that are done are deleted before a new
    one is made: sent and delivered, cancelled, or failed before asking for a broadcast. Emails still being sent, and
    scheduled ones until Resend has sent them, keep theirs, because Resend reads the segment when it sends. Returns
    the ids of the emails whose segments stay, for the message if Resend still has no room.

    Whatever goes wrong with one segment leaves that one for the next time, and the others are still freed
    (2026-10-28): until then an error here failed the email being sent, whatever that segment was."""
    holding = []
    for segment in client.segments():
        match = SEGMENT_NAME.match(segment.get('name') or '')
        if not match or int(match.group(1)) == mail.pk:
            continue
        other = int(match.group(1))
        try:
            freed = _free_segment(client, segment, other)
        except Exception:
            logger.warning('Segment %s of mass email %d could not be freed now', segment.get('id'), other,
                           exc_info=True)
            freed = False
        if not freed:
            holding.append(other)
    return holding


def _free_segment(client, segment, other_id):
    """Deletes the segment of another email if nobody will read it again; True if it did.

    The email is claimed first, with an UPDATE that only matches it as it was read, and the segment is deleted in
    Resend after that (2026-10-28, round 2). Until then a failed email retried at that very moment lost its segment
    in the middle of its delivery, and an email deleted meanwhile failed the one being sent."""
    other = MassEmail.objects.filter(pk=other_id).first()
    if other is None:
        # Deleted from the site: nobody can send it again.
        client.delete_segment(segment['id'])
        return True
    claim = MassEmail.objects.filter(pk=other.pk, status=other.status, resend_segment_id=other.resend_segment_id)
    values = {'updated': timezone.now()}
    if other.status == MassEmail.CANCELLED:
        pass
    elif other.status == MassEmail.FAILED:
        if other.resend_broadcast_id or other.broadcast_requested:
            # It asked Resend for its broadcast: a retry sends it, and Resend reads this segment then.
            return False
        claim = claim.filter(broadcast_requested=False, resend_broadcast_id='')
    elif other.status in (MassEmail.SENT, MassEmail.SCHEDULED) and other.resend_broadcast_id:
        status = _broadcast_status(client, other.resend_broadcast_id)
        status = 'deleted' if status is None else status
        if other.status == MassEmail.SCHEDULED and status == 'sent':
            # Resend sent it at its time, and nothing here learnt it (correos-masivos-1, 2026-10-28): it kept its
            # segment for good, and after two or three scheduled emails no email could be sent.
            values['status'] = MassEmail.SENT
        elif other.status == MassEmail.SCHEDULED and status in ('canceled', 'deleted', 'draft'):
            # Cancelled in Resend's dashboard (a cancelled broadcast reads as a draft again) or deleted there: it
            # will never go out.
            values['status'] = MassEmail.CANCELLED
        elif status not in ('sent', 'canceled', 'deleted'):
            return False
        values['resend_status'] = status[:20]
    else:
        return False
    own = other.resend_segment_id == segment['id']
    if own:
        values['resend_segment_id'] = ''
    with transaction.atomic():
        if not claim.update(**values):
            # It changed since it was read (retried, cancelled, deleted...): it is looked at again next time.
            return False
        if own and other.status == MassEmail.FAILED:
            # A failed email rebuilds its list in a new segment if it is retried. The others keep who they reached:
            # «Who it was sent to» read every sent email as pending.
            other.recipients.filter(state=MassEmailRecipient.ADDED).update(state=MassEmailRecipient.PENDING)
    client.delete_segment(segment['id'])
    return True


def _no_room(error, holding):
    """Why Resend made no segment for this email, and which emails hold the ones there are. Until 2026-10-28 (round 2)
    it said to delete the ones left over in Resend: every one left belongs to an email that is not done, and deleting
    the list of a scheduled email still waiting made it reach nobody, while it later showed as sent."""
    message = _('Resend did not create the list of this email (%(error)s). Its free plan holds three lists, called '
                'segments, and each email keeps its own until Resend has sent it.') % {'error': str(error)[:300]}
    emails = ['#%d «%s» (%s)' % (other.pk, other.subject[:60], other.get_status_display())
              for other in MassEmail.objects.filter(pk__in=holding).order_by('pk')[:10]]
    if not emails:
        return message + ' ' + _('None of those lists belongs to an email of this site.')
    return message + ' ' + _('Wait until the scheduled emails go out (or cancel one that is no longer needed), retry '
                             'the failed ones, and then retry this one. Emails holding a list now: %(emails)s.') % \
        {'emails': ', '.join(emails)}


def scrub_cancelled(rows):
    """Leaves the rows (a queryset of MassEmailRecipient) of cancelled accounts that still have an address as
    cpc-cancelar-cuenta leaves them: «cuenta-cancelada-<row>», the name the account has now, and Unsubscribed.
    Cancelled means inactive and without an address, as the tool leaves an account. Returns how many.

    The one rule for the task, before handing a row over, and for the page that confirms a send, on the rows it just
    wrote (2026-10-28, round 4): a send confirmed while an account was being cancelled read the address before, and
    its row could only be written after the cancellation, too late for the tool."""
    cancelled = User.objects.filter(profile=OuterRef('profile'), is_active=False, email='')
    return rows.filter(Exists(cancelled), email__contains='@').update(
        email=Concat(Value(CANCELLED_ADDRESS), 'id', output_field=CharField()),
        username=Subquery(cancelled.values('username')[:1]), state=MassEmailRecipient.UNSUBSCRIBED, error='')


def _account(row):
    """(is_active, email, username) of the account of a row as it is now, or None."""
    if not row.profile_id:
        return None
    return User.objects.filter(profile=row.profile_id).values_list('is_active', 'email', 'username').first()


def _reachable(row, account):
    """Whether the address of a row may still be handed to Resend. Not the text a cancelled account leaves instead
    of the address (cpc-cancelar-cuenta: no @), nor an address whose account was deactivated, or changed its
    address, after the email was confirmed (2026-10-28, round 2): the list is fixed at that moment, and a retry days
    later wrote to them anyway, even recreating in Resend the contact the cancellation had deleted."""
    return '@' in row.email and bool(account) and account[0] and \
        (account[1] or '').strip().lower() == row.email.strip().lower()


def _hand_over(client, mail, contacts, pk):
    """One person of the list to the segment of the email.

    The row is read again right before Resend, and locked until Resend has answered (2026-10-28, round 2): the list
    read at the start could be minutes old, and a row left out meanwhile (by cpc-cancelar-cuenta, or by another
    attempt) was still handed over and its state written back. While the lock lasts, the tool waits, so the contact
    it deletes after cancelling an account is never made again behind it.

    The row of a cancelled account that still has the address is left as the tool leaves it (2026-10-28, round 3;
    scrub_cancelled): a send confirmed while the account was being cancelled read the address before the
    cancellation, and kept it in its list for good."""
    with transaction.atomic():
        row = MassEmailRecipient.objects.select_for_update().filter(pk=pk, state__in=PENDING_STATES).first()
        if row is None or scrub_cancelled(MassEmailRecipient.objects.filter(pk=pk)):
            return
        state, error = MassEmailRecipient.UNSUBSCRIBED, ''
        if _reachable(row, _account(row)):
            contact = contacts.get(row.email.strip().lower())
            try:
                if contact is not None and contact.get('unsubscribed'):
                    state = MassEmailRecipient.UNSUBSCRIBED
                elif contact is not None:
                    client.add_to_segment(contact['id'], mail.resend_segment_id)
                    state = MassEmailRecipient.ADDED
                else:
                    client.create_contact(row.email, row.username, mail.resend_segment_id)
                    state = MassEmailRecipient.ADDED
            except ResendError as failure:
                state, error = MassEmailRecipient.FAILED, str(failure)[:255]
        MassEmailRecipient.objects.filter(pk=pk).update(state=state, error=error)


def _segment_of(broadcast):
    # `audience_id` is what Resend called segments before.
    return broadcast.get('segment_id') or broadcast.get('audience_id') or ''


def _made_before(client, mail, segment_id, asked_before):
    """The broadcast that an earlier attempt made for this email, if Resend has it: one with its name and for its
    segment. Another email can have the same name (its number given out again after a backup was restored, with the
    same subject), and then it has a segment of its own. A broadcast whose segment Resend does not say is taken only
    when an attempt is known to have asked for it, as before 2026-10-28 (round 3)."""
    for broadcast in client.broadcasts_named(mail.broadcast_name):
        segment = _segment_of(broadcast) or _segment_of(client.broadcast(broadcast['id']))
        if segment == segment_id or (not segment and asked_before):
            return broadcast['id']
    return None


def _ask_for_broadcast(client, mail, current, scheduled_at, asked_before, look):
    """The broadcast of the email, made or sent once; Resend's status of it, or None if Resend no longer has the one
    recorded. Called holding the lock of the email, as the task that still hands it over. With `look`, it first
    looks for one that an earlier attempt may have made."""
    broadcast_id = current.resend_broadcast_id
    if not broadcast_id and look:
        broadcast_id = _made_before(client, mail, current.resend_segment_id, asked_before)
        if broadcast_id:
            MassEmail.objects.filter(pk=mail.pk).update(resend_broadcast_id=broadcast_id)
    if broadcast_id:
        # Before anything, what became of it. Only a draft is sent, and it is sent once: no longer a draft after
        # that, a later attempt only reads it.
        status = _broadcast_status(client, broadcast_id)
        if status == 'draft':
            client.send_broadcast(broadcast_id, scheduled_at, 'cpcjudge-mass-email-%d-send' % mail.pk)
            status = 'scheduled' if scheduled_at else 'queued'
        return status
    subject, html, text = render_email(mail, for_resend=True)
    payload = {'segment_id': mail.resend_segment_id, 'from': mail.from_email or sender(), 'subject': subject,
               'html': html, 'text': text, 'name': mail.broadcast_name, 'send': True}
    # The preview text is also the hidden first line of the page (mass_email/email.html), where Resend puts the name
    # of each person; preview_text gets no name (correos-masivos-4, 2026-10-28). A draft saved with a name in it
    # before the form refused that goes without preview_text, as its test did.
    if mail.preheader and not any(placeholder in mail.preheader for placeholder in NAME_PLACEHOLDERS):
        payload['preview_text'] = mail.preheader
    if reply_to():
        payload['reply_to'] = reply_to()
    if scheduled_at:
        payload['scheduled_at'] = scheduled_at
    broadcast_id = client.create_broadcast(payload, 'cpcjudge-mass-email-%d' % mail.pk)
    MassEmail.objects.filter(pk=mail.pk).update(resend_broadcast_id=broadcast_id)
    return 'scheduled' if scheduled_at else 'queued'


def _finish(task, mail, status, added):
    """Writes down how the email ended, from what Resend says of its broadcast, while this task still hands it over.

    Resend no longer having the broadcast recorded for the email (`status` None) means that it was deleted there, or
    that the key now belongs to another account. A new one is never made then (2026-10-28, round 2): the first may
    have gone out, and nothing here can tell. The email ends cancelled, which frees its segment, and says so; to send
    it, it is duplicated as a new draft."""
    if status is None:
        broadcast_id = MassEmail.objects.filter(pk=mail.pk).values_list('resend_broadcast_id', flat=True).first()
        values = {'status': MassEmail.CANCELLED, 'resend_status': 'deleted',
                  'error': _('Resend no longer has this email (%(id)s): it was deleted there, or the key now belongs '
                             'to another account. It is not sent again, because it may have gone out already: check '
                             'it in Resend, and duplicate it as a new draft to send it.') % {'id': broadcast_id}}
    else:
        values = {'status': MassEmail.SCHEDULED if status == 'scheduled' else
                  MassEmail.CANCELLED if status == 'canceled' else MassEmail.SENT,
                  'resend_status': status[:20], 'error': ''}
    with transaction.atomic():
        _still_mine(task, mail.pk)
        MassEmail.objects.filter(pk=mail.pk).update(recipient_count=added, updated=timezone.now(), **values)
    return added


def _send(task, mail, client):
    heartbeat = Heartbeat(task, mail)
    pending = mail.recipients.filter(state__in=PENDING_STATES)
    scheduled_at = mail.scheduled_at.astimezone(dt_timezone.utc).isoformat() if mail.scheduled_at else None
    with Progress(task, pending.count() + 2, stage=_('Handing the email to Resend')) as progress:
        if mail.resend_broadcast_id:
            # A previous attempt made the broadcast and stopped before writing down how it ended. What Resend says
            # of it decides (2026-10-28, round 2): until then this marked it as sent without looking, and a draft
            # that a failure left behind never went out.
            status = _broadcast_status(client, mail.resend_broadcast_id)
            if status != 'draft':
                return _finish(task, mail, status, mail.recipients.filter(state=MassEmailRecipient.ADDED).count())

        contacts = client.contacts()
        segment_made_now = False
        if not mail.resend_segment_id:
            holding = _free_segments(client, mail)
            heartbeat(now=True)
            try:
                segment = client.create_segment(mail.broadcast_name)
            except ResendError as error:
                raise ResendError(_no_room(error, holding)) from error
            _save(mail, resend_segment_id=segment)
            segment_made_now = True
        progress.did(1)

        for pk in list(pending.values_list('pk', flat=True)):
            heartbeat()
            _hand_over(client, mail, contacts, pk)
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

        # Only the task still handing the email over asks for its broadcast. The request is marked first, under the
        # lock of the email, and kept: if Resend's answer is lost, the next attempt looks the broadcast up by name
        # instead of asking again.
        with transaction.atomic():
            current = _still_mine(task, mail.pk)
            asked_before = current.broadcast_requested
            if not current.resend_broadcast_id and not asked_before:
                MassEmail.objects.filter(pk=mail.pk).update(broadcast_requested=True, updated=timezone.now())
        # Then it asks holding that lock until the answer is written down (2026-10-28, round 2). An attempt that
        # takes over meanwhile waits for it, and finds the broadcast instead of making a second one: until then a
        # retry while this one was asking sent everything twice. A Resend error keeps what was written down.
        # It also looks first whenever the segment comes from an earlier attempt (2026-10-28, round 3): a backup
        # restored from the middle of a delivery brings the email back with its segment and without the mark, after
        # its broadcast went out, and a retry sent it again. A segment made by this attempt has no broadcast yet.
        failure = None
        with transaction.atomic():
            current = _still_mine(task, mail.pk)
            try:
                status = _ask_for_broadcast(client, mail, current, scheduled_at, asked_before,
                                            look=asked_before or not segment_made_now)
            except ResendError as error:
                failure = error
        if failure is not None:
            raise failure
        progress.did(1)

    return _finish(task, mail, status, added)


def _carry_to(user_id, old_key):
    """Where the unsubscription of the old address goes: (address, username), or None.

    The address the account that changed it (`user_id`) has now (2026-10-28, round 4): usually the new one. If it
    changed its address again before this ran (from A to B, then to C, with Resend down), C (round 5): the move from
    B to C finds no contact for B, and C received the next email. Nothing if the account is back at the old address,
    which keeps its own contact, or has no address: a cancelled account has none. An account deactivated meanwhile
    keeps its address, and the unsubscription goes there too (round 6): until then it got nothing, and the
    unsubscription went for good with the contact of the old address."""
    account = User.objects.filter(pk=user_id).values_list('email', 'username').first()
    current = (account[0] or '').strip() if account else ''
    if not current or current.lower() == old_key:
        return None
    return current, account[1]


@shared_task(bind=True, max_retries=6)
def move_resend_contact(self, old_email, new_email, first_name, user_id):
    """After somebody changes the email of their account (correos-masivos-6, 2026-10-28). Their contact in Resend
    belongs to the address: an unsubscription stayed with the old one, so the next email reached the new one, and the
    old address stayed in Resend. Now an unsubscription goes to the new address too, and the contact of the old one
    is deleted, unless another account has that address now.

    The unsubscription goes only to the address the same account (`user_id`) has when this runs (2026-10-28, rounds
    3 to 6, _carry_to): this may run up to hours later, retried, and it made a contact for an address that the
    account had left again or that its cancellation had just taken out of Resend, or unsubscribed another person who
    took the address meanwhile. Every message carries `user_id` (round 6): the form without it never reached the
    installed site, and its rule (any active account with the new address) was the one way this unsubscribed
    somebody else. With it went the last use of `first_name`, which stays so that the arguments keep their places:
    the contact takes the username the account has when this runs.

    What this cannot avoid (round 6), because Resend keeps one contact per address and not per account:
    * An account that takes the old address before this runs inherits its unsubscription: the contact stays while
      an account has the address.
    * The unsubscription carried to an address that other accounts share unsubscribes them too: this does not look
      at who else has it, and Resend could not tell them apart anyway.
    * If the account is cancelled while this runs, between reading its address (_carry_to) and making its contact
      (one call to Resend), the contact of that address is made after cpc-cancelar-cuenta deleted hers, and stays.
      The tool says so in its own notes.

    And what is left open (round 7): making the contact of the new address and unsubscribing it are two calls. If
    the second fails, the new address stays subscribed until a retry gets through, and an email sent meanwhile
    reaches it. Making the contact already unsubscribed, in one call, needs checking first against the real API that
    it takes that field."""
    old_key, new_key = (old_email or '').strip().lower(), (new_email or '').strip().lower()
    if not is_configured() or not old_key or old_key == new_key:
        return 'nothing to do'
    try:
        client = ResendClient()
        contacts = client.contacts()
        old = contacts.get(old_key)
        if old is None:
            return 'no contact'
        target = _carry_to(user_id, old_key) if old.get('unsubscribed') else None
        if target is not None:
            address, name = target
            new = contacts.get(address.lower())
            if new is None:
                client.unsubscribe(client.create_contact(address, name))
            elif not new.get('unsubscribed'):
                client.unsubscribe(new['id'])
        if User.objects.filter(email__iexact=old_key).exists():
            return 'kept: another account has the old address'
        client.delete_contact(old['id'])
    except ResendError as error:
        raise self.retry(exc=error, countdown=300 * (self.request.retries + 1))
    return 'moved'

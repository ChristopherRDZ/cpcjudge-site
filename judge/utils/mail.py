import hashlib
import re
import smtplib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Pattern

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.forms import ValidationError
from django.template import loader
from django.utils.translation import gettext


bad_mail_regex: List[Pattern[str]] = list(map(re.compile, settings.BAD_MAIL_PROVIDER_REGEX))

# What a failed send raises: SMTP refusals, and timeouts, DNS and TLS failures (all OSError).
SEND_ERRORS = (smtplib.SMTPException, OSError)

DAY_SECONDS = 24 * 60 * 60


def validate_email_domain(email: str) -> None:
    if '@' in email:
        domain = email.split('@')[-1].lower()
        if domain in settings.BAD_MAIL_PROVIDERS or any(regex.match(domain) for regex in bad_mail_regex):
            raise ValidationError(gettext('Your email provider is not allowed due to history of abuse. '
                                          'Please use a reputable email provider.'))


# Inspired by django.contrib.auth.forms.PasswordResetForm.send_mail
def send_mail(
    context: Dict[str, Any],
    *,
    from_email: Optional[str] = None,
    to_email: str,
    subject_template_name: str,
    email_template_name: str,
    html_email_template_name: Optional[str] = None,
) -> None:
    subject = loader.render_to_string(subject_template_name, context)
    # Email subject *must not* contain newlines
    subject = ''.join(subject.splitlines())
    body = loader.render_to_string(email_template_name, context)

    email_message = EmailMultiAlternatives(subject, body, from_email, [to_email])
    if html_email_template_name is not None:
        html_email = loader.render_to_string(html_email_template_name, context)
        email_message.attach_alternative(html_email, 'text/html')

    email_message.send()


# The email provider only accepts so many messages a day, and refuses the rest. Every email the site
# sends is counted here per UTC day, which is when providers reset their quotas, and views check
# DMOJ_EMAIL_DAILY_LIMIT before doing anything that sends, so they can refuse with a message instead of
# failing half-way.

def _increment(key: str, amount: int, timeout: int) -> int:
    cache.add(key, 0, timeout)
    try:
        return cache.incr(key, amount)
    except ValueError:
        # The key expired between add() and incr().
        cache.add(key, amount, timeout)
        return amount


def _today() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def emails_sent_today() -> int:
    return cache.get('mailbudget!' + _today(), 0)


def email_budget_available(count: int = 1, reserve: int = 0) -> bool:
    """Whether `count` more emails fit in today's limit while leaving `reserve` of it unused."""
    return emails_sent_today() + count + reserve <= settings.DMOJ_EMAIL_DAILY_LIMIT


def spend_email_budget(count: int = 1) -> None:
    _increment('mailbudget!' + _today(), count, 2 * DAY_SECONDS)


def _attempt_key(kind: str, target: str) -> str:
    return 'mailattempt!%s!%s' % (kind, hashlib.sha256(target.strip().lower().encode()).hexdigest())


def claim_email_attempt(kind: str, target: str, limit: int) -> bool:
    """Count one more email of `kind` to `target`; False once there have been more than `limit`
    in the 24 hours since the first one."""
    return _increment(_attempt_key(kind, target), 1, DAY_SECONDS) <= limit


def release_email_attempt(kind: str, target: str) -> None:
    """Give back an attempt whose email could not be sent, so it does not count against the user."""
    try:
        cache.decr(_attempt_key(kind, target))
    except ValueError:
        pass


def error_email_allowed(recipients: int) -> bool:
    """Error reports to ADMINS get a small share of each day, and never the reserve, so a burst of
    errors cannot use up the emails users need."""
    if _increment('mailerrors!' + _today(), 1, 2 * DAY_SECONDS) > settings.DMOJ_EMAIL_ERROR_DAILY_LIMIT:
        return False
    if not email_budget_available(recipients, reserve=settings.DMOJ_EMAIL_DAILY_RESERVE):
        return False
    spend_email_budget(recipients)
    return True

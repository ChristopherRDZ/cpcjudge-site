"""A small client for the Resend API (https://resend.com/docs/api-reference), for emails to many users.

The site's own emails (activation, password reset...) do not come through here: they keep using SMTP. This is
only for Broadcasts, Resend's way of sending one email to a list of people (a segment), which also adds the
unsubscribe link and honours it.

Resend allows 10 requests per second per account, shared by every key and application of that account. The
client stays well below that (DMOJ_RESEND_REQUESTS_PER_SECOND), waits and retries when Resend answers 429 or a
5xx, and retries network errors a few times.
"""
import threading
import time
from urllib.parse import quote

import requests
from django.conf import settings

API = 'https://api.resend.com'
RETRIES = 5


class ResendError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class ResendNotConfigured(ResendError):
    pass


def is_configured():
    return bool(getattr(settings, 'DMOJ_RESEND_API_KEY', None))


class ResendClient:
    def __init__(self, api_key=None, requests_per_second=None, session=None, sleep=None, timeout=20):
        self.api_key = api_key or getattr(settings, 'DMOJ_RESEND_API_KEY', None)
        if not self.api_key:
            raise ResendNotConfigured('DMOJ_RESEND_API_KEY is not set')
        rate = requests_per_second or getattr(settings, 'DMOJ_RESEND_REQUESTS_PER_SECOND', 4)
        self.interval = 1.0 / max(rate, 0.1)
        self.session = session or requests.Session()
        self.sleep = sleep or time.sleep
        self.timeout = timeout
        self._last = 0.0
        self._lock = threading.Lock()

    # --- transport ---------------------------------------------------------------------------------------------

    def _throttle(self):
        with self._lock:
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()

    def request(self, method, path, json=None, params=None, headers=None, missing_ok=False, repeatable=True):
        """One call, waiting its turn. A 429 means Resend did nothing, so it is always tried again. A network error
        or a 5xx may come after Resend did act: those are tried again only when `repeatable`, and never for what
        would send emails twice."""
        all_headers = {'Authorization': 'Bearer ' + self.api_key, 'User-Agent': 'cpcjudge-site'}
        all_headers.update(headers or {})
        for attempt in range(RETRIES):
            self._throttle()
            try:
                response = self.session.request(method, API + path, json=json, params=params, headers=all_headers,
                                                timeout=self.timeout)
            except requests.RequestException as error:
                if attempt == RETRIES - 1 or not repeatable:
                    raise ResendError('Could not reach Resend: %s' % type(error).__name__)
                self.sleep(2 ** attempt)
                continue
            if response.status_code == 429 or (response.status_code >= 500 and repeatable):
                if attempt == RETRIES - 1:
                    raise ResendError(self._message(response), response.status_code)
                try:
                    delay = float(response.headers.get('retry-after') or 0)
                except ValueError:
                    delay = 0
                self.sleep(min(max(delay, 2 ** attempt), 60))
                continue
            if response.status_code == 404 and missing_ok:
                return None
            if response.status_code >= 400:
                raise ResendError(self._message(response), response.status_code)
            if not response.content:
                return {}
            return response.json()
        raise ResendError('Resend did not answer')  # pragma: no cover

    @staticmethod
    def _message(response):
        try:
            data = response.json()
            message = data.get('message') or data.get('name') or ''
        except ValueError:
            message = ''
        return ('Resend answered %d: %s' % (response.status_code, message or response.reason or '')).strip()

    def _paginate(self, path, params=None):
        params = dict(params or {}, limit=100)
        while True:
            page = self.request('GET', path, params=params)
            data = page.get('data') or []
            yield from data
            if not page.get('has_more') or not data:
                return
            params['after'] = data[-1]['id']

    # --- contacts and segments ---------------------------------------------------------------------------------

    def contacts(self):
        """Every contact of the account, as {lowercase email: contact}."""
        return {contact['email'].strip().lower(): contact for contact in self._paginate('/contacts')}

    def segments(self):
        return list(self._paginate('/segments'))

    def create_segment(self, name):
        return self.request('POST', '/segments', json={'name': name})['id']

    def delete_segment(self, segment_id):
        self.request('DELETE', '/segments/%s' % quote(segment_id, safe=''), missing_ok=True)

    def create_contact(self, email, first_name, segment_id):
        # `unsubscribed` is never sent: a new contact starts subscribed, and an existing one keeps its choice.
        return self.request('POST', '/contacts', json={'email': email, 'first_name': first_name,
                                                       'segments': [segment_id]})['id']

    def add_to_segment(self, contact_id, segment_id):
        self.request('POST', '/contacts/%s/segments/%s' % (quote(contact_id, safe=''), quote(segment_id, safe='')))

    def resubscribe(self, contact_id):
        self.request('PATCH', '/contacts/%s' % quote(contact_id, safe=''), json={'unsubscribed': False})

    # --- broadcasts --------------------------------------------------------------------------------------------

    def create_broadcast(self, payload, idempotency_key=None):
        headers = {'Idempotency-Key': idempotency_key} if idempotency_key else None
        return self.request('POST', '/broadcasts', json=payload, headers=headers, repeatable=False)['id']

    def find_broadcast(self, name):
        for broadcast in self._paginate('/broadcasts'):
            if broadcast.get('name') == name:
                return broadcast['id']
        return None

    def broadcast(self, broadcast_id):
        return self.request('GET', '/broadcasts/%s' % quote(broadcast_id, safe=''))

    def send_broadcast(self, broadcast_id, scheduled_at=None):
        payload = {'scheduled_at': scheduled_at} if scheduled_at else {}
        self.request('POST', '/broadcasts/%s/send' % quote(broadcast_id, safe=''), json=payload, repeatable=False)

    def cancel_broadcast(self, broadcast_id):
        self.request('POST', '/broadcasts/%s/cancel' % quote(broadcast_id, safe=''))

    def count_recipients(self, broadcast_id, kind):
        return sum(1 for _ in self._paginate('/broadcasts/%s/recipients' % quote(broadcast_id, safe=''),
                                             {'type': kind}))

    # --- single emails -----------------------------------------------------------------------------------------

    def send_email(self, payload):
        """One email, through the transactional API: counts against its daily quota. Used for tests only."""
        return self.request('POST', '/emails', json=payload, repeatable=False)['id']

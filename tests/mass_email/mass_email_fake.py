"""Resend in memory for the tests of emails to users: contacts, segments, broadcasts and single emails, with
the cursor pagination of the API and injected failures. It never goes to the network.

    with fake_resend() as resend:
        ...
        resend.broadcasts  # what it was asked
"""
import uuid
from contextlib import contextmanager
from unittest import mock
from urllib.parse import unquote

API = 'https://api.resend.com'


class FakeResponse:
    def __init__(self, status, data=None, headers=None):
        self.status_code = status
        self._data = data
        self.headers = headers or {}
        self.content = b'' if data is None else b'{}'
        self.reason = 'fake'

    def json(self):
        if self._data is None:
            raise ValueError('no body')
        return self._data


class FakeResend:
    def __init__(self):
        self.contacts = {}      # id -> {'id', 'email', 'first_name', 'unsubscribed', 'segments': set()}
        self.segments = {}      # id -> name
        self.broadcasts = {}    # id -> payload + 'id', 'status'
        self.emails = []        # payloads of POST /emails
        self.events = {}        # broadcast id -> {kind: [emails]}
        self.calls = []         # (method, path)
        self.failures = []      # [(method, path prefix, status or 'network', times)]
        self.lose_broadcast_answer = False
        self.segment_limit = 3  # the free marketing plan of Resend

    # --- helpers for the tests ---------------------------------------------------------------------------------

    def add_contact(self, email, unsubscribed=False, first_name=''):
        contact_id = str(uuid.uuid4())
        self.contacts[contact_id] = {'id': contact_id, 'email': email, 'first_name': first_name,
                                     'unsubscribed': unsubscribed, 'segments': set()}
        return contact_id

    def fail(self, method, prefix, status, times=1):
        self.failures.append([method, prefix, status, times])

    def segment_emails(self, segment_id):
        return sorted(contact['email'] for contact in self.contacts.values() if segment_id in contact['segments'])

    def count(self, method, prefix):
        return sum(1 for m, path in self.calls if m == method and path.startswith(prefix))

    # --- the API -----------------------------------------------------------------------------------------------

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        assert url.startswith(API), url
        assert headers['Authorization'].startswith('Bearer '), headers
        path = url[len(API):]
        self.calls.append((method, path))
        for failure in self.failures:
            if failure[0] == method and path.startswith(failure[1]) and failure[3] > 0:
                failure[3] -= 1
                if failure[2] == 'network':
                    import requests
                    raise requests.ConnectionError('injected')
                return FakeResponse(failure[2], {'message': 'injected %d' % failure[2]}, {'retry-after': '0'})
        parts = [unquote(part) for part in path.strip('/').split('/')]
        handler = getattr(self, '_%s_%s' % (method.lower(), parts[0]), None)
        if handler is None:
            return FakeResponse(404, {'message': 'no route'})
        return handler(parts[1:], json or {}, params or {})

    @staticmethod
    def _page(items, params):
        limit = int(params.get('limit', 20))
        after = params.get('after')
        if after:
            ids = [item['id'] for item in items]
            items = items[ids.index(after) + 1:]
        return FakeResponse(200, {'object': 'list', 'has_more': len(items) > limit, 'data': items[:limit]})

    def _contact(self, key):
        for contact in self.contacts.values():
            if key == contact['id'] or key.lower() == contact['email'].lower():
                return contact
        return None

    def _get_contacts(self, parts, data, params):
        listed = [{key: value for key, value in contact.items() if key != 'segments'}
                  for contact in self.contacts.values()]
        return self._page(listed, params)

    def _post_contacts(self, parts, data, params):
        if not parts:
            if self._contact(data['email']):
                return FakeResponse(409, {'message': 'Contact already exists'})
            assert 'unsubscribed' not in data, 'a contact must never be created with a subscription choice'
            # As the real API (seen in production on 2026-10-21): each segment is an object with its id.
            segments = data.get('segments', [])
            if not all(isinstance(item, dict) and isinstance(item.get('id'), str) for item in segments):
                return FakeResponse(422, {'message': 'Invalid input: expected object, received string'})
            contact_id = self.add_contact(data['email'], first_name=data.get('first_name', ''))
            self.contacts[contact_id]['segments'] |= {item['id'] for item in segments}
            return FakeResponse(200, {'object': 'contact', 'id': contact_id})
        contact = self._contact(parts[0])
        if contact is None or len(parts) != 3 or parts[1] != 'segments' or parts[2] not in self.segments:
            return FakeResponse(404, {'message': 'not found'})
        contact['segments'].add(parts[2])
        return FakeResponse(200, {'id': parts[2]})

    def _patch_contacts(self, parts, data, params):
        contact = self._contact(parts[0])
        if contact is None:
            return FakeResponse(404, {'message': 'not found'})
        contact['unsubscribed'] = data['unsubscribed']
        return FakeResponse(200, {'object': 'contact', 'id': contact['id']})

    def _get_segments(self, parts, data, params):
        return self._page([{'id': key, 'name': name} for key, name in self.segments.items()], params)

    def _delete_segments(self, parts, data, params):
        if self.segments.pop(parts[0], None) is None:
            return FakeResponse(404, {'message': 'not found'})
        for contact in self.contacts.values():
            contact['segments'].discard(parts[0])
        return FakeResponse(200, {'object': 'segment', 'id': parts[0], 'deleted': True})

    def _post_segments(self, parts, data, params):
        if self.segment_limit is not None and len(self.segments) >= self.segment_limit:
            return FakeResponse(422, {'message': 'You have reached the maximum number of segments'})
        segment_id = str(uuid.uuid4())
        self.segments[segment_id] = data['name']
        return FakeResponse(200, {'object': 'segment', 'id': segment_id, 'name': data['name']})

    def _post_broadcasts(self, parts, data, params):
        if not parts:
            assert data['segment_id'] in self.segments
            broadcast_id = str(uuid.uuid4())
            status = 'draft' if not data.get('send') else 'scheduled' if data.get('scheduled_at') else 'queued'
            self.broadcasts[broadcast_id] = dict(data, id=broadcast_id, status=status,
                                                 recipients=self.segment_emails(data['segment_id']))
            if self.lose_broadcast_answer:
                self.lose_broadcast_answer = False
                return FakeResponse(502, {'message': 'bad gateway'})
            return FakeResponse(200, {'object': 'broadcast', 'id': broadcast_id})
        broadcast = self.broadcasts.get(parts[0])
        if broadcast is None:
            return FakeResponse(404, {'message': 'not found'})
        if parts[1:] == ['send']:
            broadcast['status'] = 'scheduled' if data.get('scheduled_at') else 'queued'
            broadcast['recipients'] = self.segment_emails(broadcast['segment_id'])
            return FakeResponse(200, {'id': broadcast['id']})
        if parts[1:] == ['cancel']:
            if broadcast['status'] != 'scheduled':
                return FakeResponse(422, {'message': 'not scheduled'})
            broadcast['status'] = 'draft'
            return FakeResponse(200, {'object': 'broadcast', 'id': broadcast['id']})
        return FakeResponse(404, {'message': 'no route'})

    def _get_broadcasts(self, parts, data, params):
        if not parts:
            listed = [{'id': b['id'], 'name': b.get('name'), 'status': b['status'], 'segment_id': b['segment_id']}
                      for b in self.broadcasts.values()]
            return self._page(listed, params)
        broadcast = self.broadcasts.get(parts[0])
        if broadcast is None:
            return FakeResponse(404, {'message': 'not found'})
        if parts[1:] == ['recipients']:
            emails = self.events.get(parts[0], {}).get(params['type'], [])
            return self._page([{'id': 'r%d' % index, 'email': email} for index, email in enumerate(emails)], params)
        return FakeResponse(200, dict(broadcast, object='broadcast'))

    def _post_emails(self, parts, data, params):
        self.emails.append(data)
        return FakeResponse(200, {'id': str(uuid.uuid4())})


@contextmanager
def fake_resend():
    resend = FakeResend()
    with mock.patch('judge.utils.resend.requests.Session', return_value=resend), \
            mock.patch('judge.utils.resend.time.sleep'):
        yield resend

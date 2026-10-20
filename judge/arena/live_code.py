"""What each player is typing, kept in the shared cache so the projector screens can show it live.

The arena editor posts the whole text whenever it changes, about three times a second at most and one request at a
time; screens ask for it about as often and only get a copy back when the sequence number moved. Nothing goes through the event daemon, whose
history buffer is shared by the whole site.

Everything here degrades instead of failing: with the cache down the projector just stops showing code, and the
rate limits stop counting. The game itself lives in the database and is not affected.
"""
import logging

from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger('judge.arena')

TTL = 24 * 3600
MAX_TEXT = 65536
# A single snapshot growing by more than this many characters is flagged to the organizer as a possible paste.
JUMP_CHARACTERS = 160


def _key(game_id, slot):
    return 'arena:code:%d:%s' % (game_id, slot)


def _seq_key(game_id, slot):
    return 'arena:codeseq:%d:%s' % (game_id, slot)


def _draft_key(game_id, slot, order):
    return 'arena:draft:%d:%s:%d' % (game_id, slot, order)


def _flags_key(game_id, slot):
    return 'arena:flags:%d:%s' % (game_id, slot)


def _counter(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10 ** 6 else 0


def _cursor(value):
    if isinstance(value, dict):
        row, column = value.get('row'), value.get('column')
        if all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10 ** 6 for v in (row, column)):
            return {'row': row, 'column': column}
    return None


def store(game_id, slot, order, language, text, cursor=None, counters=None, reason=''):
    """Save the latest code of a player. Returns the new sequence number, or None if the cache is unavailable."""
    try:
        previous = cache.get(_key(game_id, slot))
        cache.add(_seq_key(game_id, slot), 0, TTL)
        seq = cache.incr(_seq_key(game_id, slot))
        now = timezone.now().timestamp()
        cache.set(_key(game_id, slot), {
            'seq': seq, 'problem': order, 'language': language, 'text': text, 'cursor': _cursor(cursor),
            'time': now,
        }, TTL)
        cache.set(_draft_key(game_id, slot, order), {'language': language, 'text': text, 'time': now}, TTL)
        flags = cache.get(_flags_key(game_id, slot)) or {'paste': 0, 'blur': 0, 'jumps': 0}
        counters = counters if isinstance(counters, dict) else {}
        flags['paste'] = max(flags.get('paste', 0), _counter(counters.get('paste')))
        flags['blur'] = max(flags.get('blur', 0), _counter(counters.get('blur')))
        same_problem = previous is not None and previous.get('problem') == order
        if same_problem and reason != 'template' and len(text) - len(previous.get('text') or '') > JUMP_CHARACTERS:
            flags['jumps'] = flags.get('jumps', 0) + 1
        cache.set(_flags_key(game_id, slot), flags, TTL)
        return seq
    except Exception:
        logger.warning('Arena live code could not be stored; screens will not show it.')
        return None


def latest(game_id, slot):
    try:
        return cache.get(_key(game_id, slot))
    except Exception:
        return None


def latest_seq(game_id, slot):
    snapshot = latest(game_id, slot)
    return snapshot['seq'] if snapshot else 0


def drafts(game_id, slot, orders):
    try:
        found = cache.get_many([_draft_key(game_id, slot, order) for order in orders])
    except Exception:
        return {}
    return {order: found[_draft_key(game_id, slot, order)] for order in orders
            if _draft_key(game_id, slot, order) in found}


def flags(game_id, slot):
    try:
        return cache.get(_flags_key(game_id, slot)) or {'paste': 0, 'blur': 0, 'jumps': 0}
    except Exception:
        return {'paste': 0, 'blur': 0, 'jumps': 0}


def allow(scope, ident, limit, window=1):
    """Fixed-window rate limit in the shared cache. Allows everything if the cache is down."""
    if limit <= 0:
        return True
    bucket = int(timezone.now().timestamp()) // window
    key = 'arena:rl:%s:%s:%d' % (scope, ident, bucket)
    try:
        cache.add(key, 0, window * 2 + 1)
        return cache.incr(key) <= limit
    except Exception:
        return True

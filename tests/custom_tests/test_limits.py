"""Exercise the limiter from judge/views/problem.py without importing Django.

The helper block is lifted verbatim out of the repository source and executed against
stubs, so the code under test is the same text that will be deployed. Nothing
here reaches the database, Redis, the site tree or any running service.
"""
import io
import re
import sys
from datetime import timedelta
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / 'judge/views/problem.py'
START = '# Per-user limits for the custom test tool.'
END = "    return _custom_test_window_refusal(profile) or _custom_test_in_flight_refusal(profile)"

failures = []


def check(label, condition):
    print('%-58s %s' % (label, 'ok' if condition else 'FAIL'))
    if not condition:
        failures.append(label)


class FakeClock:
    def __init__(self):
        self.seconds = 1_000_000_000

    def now(self):
        return FakeMoment(self.seconds)


class FakeMoment:
    def __init__(self, seconds):
        self.seconds = seconds

    def timestamp(self):
        return self.seconds

    def __sub__(self, delta):
        return FakeMoment(self.seconds - int(delta.total_seconds()))


class FakeCache:
    """Mimics the add/incr contract of django.core.cache with Redis."""

    def __init__(self, broken=False):
        self.store = {}
        self.broken = broken
        self.timeouts = {}

    def add(self, key, value, timeout=None):
        if self.broken:
            raise RuntimeError('redis down')
        if key in self.store:
            return False
        self.store[key] = value
        self.timeouts[key] = timeout
        return True

    def incr(self, key, delta=1):
        if self.broken:
            raise RuntimeError('redis down')
        if key not in self.store:
            raise ValueError('Key %r not found' % key)
        self.store[key] += delta
        return self.store[key]


class FakeQuery:
    def __init__(self, owner):
        self.owner = owner

    def filter(self, **kwargs):
        self.owner.last_filter = kwargs
        return self

    def count(self):
        return self.owner.in_flight


class FakeSubmission:
    def __init__(self, in_flight=0):
        self.in_flight = in_flight
        self.last_filter = None

    @property
    def objects(self):
        return FakeQuery(self)


class FakeSettings:
    pass


class FakeProfile:
    def __init__(self, pk):
        self.pk = pk


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


class FakeLogging:
    def __init__(self):
        self.logger = FakeLogger()

    def getLogger(self, name):
        return self.logger


def load(cache, submission, settings, clock, logging_module):
    source = io.open(SOURCE, encoding='utf-8').read()
    start = source.index(START)
    end = source.index(END) + len(END)
    block = source[start:end]
    namespace = {
        'settings': settings, 'timezone': clock, 'cache': cache,
        'Submission': submission, 'logging': logging_module, 'timedelta': timedelta,
        'CUSTOM_TEST_MARKER': 'CPC custom test v1:',
    }
    exec(compile(block, 'candidate-block', 'exec'), namespace)
    return namespace


def fresh(in_flight=0, broken_cache=False, **overrides):
    cache = FakeCache(broken=broken_cache)
    submission = FakeSubmission(in_flight=in_flight)
    settings = FakeSettings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    clock = FakeClock()
    logging_module = FakeLogging()
    namespace = load(cache, submission, settings, clock, logging_module)
    return namespace, cache, submission, clock, logging_module


def main():
    # Defaults agreed with the owner: 2 in flight, 12 per minute, 200 per hour.
    ns, _cache, _sub, _clock, _log = fresh()
    check('defaults are 2 / 12 / 200', (ns['CUSTOM_TEST_MAX_IN_FLIGHT'],
                                        ns['CUSTOM_TEST_MAX_PER_MINUTE'],
                                        ns['CUSTOM_TEST_MAX_PER_HOUR']) == (2, 12, 200))

    # A single run is allowed and the counters start at one.
    ns, cache, _sub, _clock, _log = fresh()
    profile = FakeProfile(7)
    check('first run is allowed', ns['_custom_test_refusal'](profile) is None)
    check('minute counter reached 1', list(cache.store.values()) == [1, 1])

    # Twelve runs pass, the thirteenth is refused inside the same minute.
    ns, _cache, _sub, _clock, _log = fresh()
    profile = FakeProfile(7)
    verdicts = [ns['_custom_test_refusal'](profile) for _ in range(13)]
    check('runs 1..12 allowed', all(v is None for v in verdicts[:12]))
    check('run 13 refused by the minute window',
          verdicts[12] is not None and 'Demasiadas pruebas seguidas' in verdicts[12])

    # The refusal clears once the fixed window rolls over.
    ns, _cache, _sub, clock, _log = fresh()
    profile = FakeProfile(7)
    for _ in range(13):
        ns['_custom_test_refusal'](profile)
    clock.seconds += 60
    check('next minute is allowed again', ns['_custom_test_refusal'](profile) is None)

    # Counters are per user: one user's burst does not refuse another.
    ns, _cache, _sub, _clock, _log = fresh()
    noisy, quiet = FakeProfile(7), FakeProfile(8)
    for _ in range(13):
        ns['_custom_test_refusal'](noisy)
    check('a different user is unaffected', ns['_custom_test_refusal'](quiet) is None)

    # The hourly ceiling, isolated by disabling the minute one so that the
    # faster window cannot mask it. The clock stays inside a single hour.
    ns, _cache, _sub, _clock, _log = fresh(CPC_CUSTOM_TEST_MAX_PER_MINUTE=0)
    profile = FakeProfile(7)
    verdicts = [ns['_custom_test_refusal'](profile) for _ in range(210)]
    check('hourly ceiling allows exactly 200',
          sum(v is None for v in verdicts) == 200 and verdicts[199] is None)
    check('run 201 refused by the hour window',
          verdicts[200] is not None and 'por hora' in verdicts[200])

    # The hour counter only advances when the minute check lets the run through,
    # so a burst that is already being refused does not burn the hourly budget.
    ns, cache, _sub, _clock, _log = fresh()
    profile = FakeProfile(7)
    for _ in range(30):
        ns['_custom_test_refusal'](profile)
    hour_key = [k for k in cache.store if k.startswith('custom-test:hour:')][0]
    check('a refused burst does not consume the hourly budget',
          cache.store[hour_key] == 12)

    # Concurrency: two in flight is the ceiling, so a third is refused.
    ns, _cache, _sub, _clock, _log = fresh(in_flight=1)
    check('one test in flight still allows another',
          ns['_custom_test_refusal'](FakeProfile(7)) is None)
    ns, _cache, sub, _clock, _log = fresh(in_flight=2)
    verdict = ns['_custom_test_refusal'](FakeProfile(7))
    check('two in flight refuses the third',
          verdict is not None and 'en ejecución' in verdict)
    check('in-flight query filters on user, status, age and marker',
          set(sub.last_filter) == {'user', 'status__in', 'date__gte', 'problem__summary__startswith'})
    check('in-flight query only counts QU/P/G',
          sub.last_filter['status__in'] == ('QU', 'P', 'G'))
    check('in-flight age window is 10 minutes',
          ns['CUSTOM_TEST_IN_FLIGHT_MAX_AGE'] == timedelta(minutes=10))

    # A stuck submission older than the window stops blocking the user. The
    # cutoff is what the query is given; prove it moves with the clock.
    ns, _cache, sub, clock, _log = fresh(in_flight=2)
    ns['_custom_test_refusal'](FakeProfile(7))
    cutoff = sub.last_filter['date__gte']
    check('cutoff is 600 s behind now', clock.now().timestamp() - cutoff.timestamp() == 600)

    # A cache outage must not take the tool down.
    ns, _cache, _sub, _clock, log = fresh(broken_cache=True)
    check('broken cache still allows the run',
          ns['_custom_test_refusal'](FakeProfile(7)) is None)
    check('broken cache is logged once', len(log.logger.warnings) == 1)
    ns, _cache, _sub, _clock, _log = fresh(in_flight=2, broken_cache=True)
    check('broken cache still enforces concurrency',
          ns['_custom_test_refusal'](FakeProfile(7)) is not None)

    # Private-settings overrides, including the disable switch.
    ns, _cache, _sub, _clock, _log = fresh(CPC_CUSTOM_TEST_MAX_PER_MINUTE=3)
    profile = FakeProfile(7)
    verdicts = [ns['_custom_test_refusal'](profile) for _ in range(4)]
    check('CPC_CUSTOM_TEST_MAX_PER_MINUTE override applies',
          all(v is None for v in verdicts[:3]) and verdicts[3] is not None)
    ns, _cache, _sub, _clock, _log = fresh(in_flight=99, CPC_CUSTOM_TEST_MAX_IN_FLIGHT=0)
    check('a ceiling of 0 disables that control',
          ns['_custom_test_refusal'](FakeProfile(7)) is None)

    # A malformed override must fall back rather than crash or disable silently.
    ns, _cache, _sub, _clock, _log = fresh(CPC_CUSTOM_TEST_MAX_PER_MINUTE='doce')
    profile = FakeProfile(7)
    verdicts = [ns['_custom_test_refusal'](profile) for _ in range(13)]
    check('a non-integer override falls back to the default 12',
          all(v is None for v in verdicts[:12]) and verdicts[12] is not None)
    ns, _cache, _sub, _clock, _log = fresh(CPC_CUSTOM_TEST_MAX_IN_FLIGHT=True, in_flight=2)
    check('True is not accepted as the number 1',
          ns['_custom_test_refusal'](FakeProfile(7)) is not None)

    # The cache keys must expire on their own; no key may be written forever.
    ns, cache, _sub, _clock, _log = fresh()
    ns['_custom_test_refusal'](FakeProfile(7))
    check('every cache key carries a finite timeout',
          cache.timeouts and all(isinstance(t, int) and 0 < t <= 7200
                                 for t in cache.timeouts.values()))

    # The view must refuse before it parses the body or touches the filesystem.
    source = io.open(SOURCE, encoding='utf-8').read()
    post = source[source.index('def custom_test_run(request):'):]
    body = post[:post.index('import json')]
    check('the refusal runs before the body is parsed', '_custom_test_refusal' in body)
    check('the refusal answers 429', 'status=429' in post[:post.index('import json')])
    check('GET polling is untouched by the limiter',
          post.count('_custom_test_refusal') == 1)

    print()
    if failures:
        print('%d check(s) failed:' % len(failures))
        for item in failures:
            print('  - ' + item)
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())

# Custom-test admission checks

Run from the checkout with Python 3:

```sh
python -B tests/custom_tests/test_limits.py
```

These 27 checks extract the limit helpers from `judge/views/problem.py` and run
them with in-memory stand-ins for the clock, cache, settings and database count.
They do not import Django, connect to any service, create users or submit code.

They cover per-user and per-window separation, configured limits, expiry, cache
outages, the response status and the check happening before the request body is
parsed. They do not test simultaneous requests; see the
[known limitation](../../docs/security/custom-tests.md).

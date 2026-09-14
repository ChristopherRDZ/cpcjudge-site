# Custom-test admission checks

Run from this source checkout with Python 3:

```sh
python -B tests/custom_tests/test_limits.py
```

These 27 synthetic checks extract the limiter helpers from
`judge/views/problem.py` and execute them with in-memory doubles for the clock,
cache, settings and query result. They neither import Django nor connect to
services, create users, submit code or modify application data.

Coverage includes user/window separation, configured ceilings, expiration,
cache outages, response status and placement before body parsing. This is a
publication of the checks used for the deployed 2026-09-14 change, with its
source path adapted to this repository.

The sequential tests do not prove atomic admission under concurrency. See the
[documented race and retention limits](../../docs/security/custom-tests.md).

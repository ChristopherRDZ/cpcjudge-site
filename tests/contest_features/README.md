# Contest tool regression tests

Run from a clean checkout with the project's Python dependencies and GNU gettext
(`msgfmt`) installed:

```sh
python tests/contest_features/run.py
```

The runner builds a temporary copy without local settings, uses SQLite in
memory and an isolated memory cache, disables events and compression, and runs
the balloon/location tests plus the synthetic freeze, reveal and award checks.
It never imports your private settings or connects to a live database or Redis.
It does not start a web server or install dependencies. It prints the temporary
directory for inspection.

`test_balloons.py` covers eligibility, freeze behavior, access control,
delivery/undo, locations, escaped text, pagination, incomplete/tampered forms,
overlapping editor changes and preservation of rows outside the submitted page.
The concurrent delivery test needs a disposable MariaDB test database with real
row locks; it is skipped by this SQLite runner. Historical MariaDB validation
passed all 24 tests. Never run database tests with production settings.

The standalone freeze/reveal/award scripts use synthetic data. Browser checks,
private deployment checks and production data are not shipped with this suite.

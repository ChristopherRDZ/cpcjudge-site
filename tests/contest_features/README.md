# Contest tool regression tests

Run from a clean checkout with the project's Python dependencies and GNU gettext
(`msgfmt`) installed:

```sh
python tests/contest_features/run.py
```

The runner builds a temporary copy of the code without your local settings, uses
an in-memory SQLite database and cache, disables events and compression, and runs
the balloon and location tests plus the freeze, reveal and award checks. It never
reads your private settings or connects to a live database or Redis. It prints
the temporary directory so you can inspect it.

`test_balloons.py` covers eligibility, the freeze rule, access control,
delivery and undo, locations, escaped text, pagination, incomplete or tampered
forms, conflicting edits and rows outside the submitted page. The concurrent
delivery test needs real row locks (MariaDB or MySQL) and is skipped on SQLite.

Never run database tests with production settings.

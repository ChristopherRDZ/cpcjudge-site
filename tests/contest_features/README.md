# Contest tool regression tests

Run from a clean checkout with the project's Python dependencies and GNU gettext
(`msgfmt`) installed:

```sh
python tests/contest_features/run.py
```

The runner builds a temporary copy of the code without your local settings, uses
an in-memory SQLite database and cache, disables events and compression, and runs
the balloon, location and contest-problem removal tests plus the freeze, reveal
and award checks. It never
reads your private settings or connects to a live database or Redis. It prints
the temporary directory so you can inspect it.

`test_balloons.py` covers eligibility, the freeze rule, access control,
delivery and undo, locations, escaped text, pagination, incomplete or tampered
forms, conflicting edits and rows outside the submitted page. The concurrent
delivery test needs real row locks (MariaDB or MySQL) and is skipped on SQLite.

`test_contest_problem_removal.py` covers removing a problem from a contest in the
admin: a contest author removes one from their own contest and the submissions
stay, nobody reaches somebody else's contest, and the contest problem list is the
only exemption from the owner-only deletion lock.

Never run database tests with production settings.

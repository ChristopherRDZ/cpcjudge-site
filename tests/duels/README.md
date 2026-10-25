# Duel tests

Run from a clean checkout with the project's Python dependencies and GNU gettext
(`msgfmt`) installed:

```sh
python tests/duels/run.py
```

The runner builds a temporary copy of the code without your local settings, uses
an in-memory SQLite database and cache, and runs every `test_*.py` here with the
interface in Spanish, then the page and message tests again in English. Judging
is replaced by a no-op and the bridge is simulated the way it really works: the
submission gets its verdict and then `update_contest()` runs. It never reads your
private settings, connects to a live database, Redis or judge, or sends an email.
It prints the temporary directory so you can inspect it.

The tests cover:

- scoring per game (partial or all-or-nothing points, penalties, ties decided by
  time) and best-of-N series;
- tournaments: the bracket built by itself, byes, the third-place match and
  problems chosen by hand or dealt from a problem bank, never repeated;
- the engine end to end: calling players, the countdown, submissions, verdicts,
  a game ending by itself once nobody can win, and versioned organizer actions
  that report conflicts instead of overwriting each other;
- the lock that keeps players on the duel page, its expiry after a period
  without activity, and submissions refused outside the duel;
- projector screens and their secret links, live code and the request limits;
- the player page: the site's navigation bar, the result explained from each
  side, the review mode and "Use as input";
- the admin: the Battles section, who sees it, finding and deleting games, a game
  in progress that cannot be deleted, and the hidden contests behind games, left
  out of the contest lists and deleted with their game;
- who organizes what: `manage_arena` for one's own battles and the ones where one
  is a co-organizer, `manage_all_arena` for all of them, the Organizers panel of
  tournaments and quick duels, screens and problem banks per organizer, and
  private problems in banks hidden from organizers who cannot see them;
- `/duelos/` and the forwarding from `/arena/`;
- migration 0157, which only creates the `judge_arena*` tables.

The look of the pages (bracket lines, countdown, live code in the browser) is
JavaScript and CSS and is not covered here.

Never run database tests with production settings.

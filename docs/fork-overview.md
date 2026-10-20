# What this fork adds

Every change this fork makes to upstream DMOJ, grouped the way the README
presents them, with the date each one was added.

## Contests

| Change | Notes |
| --- | --- |
| Scoreboard freeze | Freezes the board, participations, submission lists and details, live updates, statistics, per-problem counters and the API. Your own row marks results the board is not showing. Migration `0153`. 2026-09-16 |
| Reveal ceremony | ICPC-style reveal, adjustable speed, medal ranges, ties, first-to-solve, award cards for contestants and teams. 2026-09-16 |
| Balloon desk | Pending and delivered lists, undo, per-problem colors, per-participant locations, staff permission. Migration `0154`. 2026-09-16 |
| Announcements and clarifications | Site-wide or per-contest, plain text, rate limited; public answers are sent as announcements and move, update or withdraw with their question. Migrations `0150`–`0152`. 2026-09-15 |
| Self-refreshing rankings | Polls only while the tab is visible, backs off, stops after the contest. 2026-09-15 |
| Resilient live updates | Heartbeat in the event daemon, exponential backoff in the browser, long-polling fallback. 2026-09-15 |
| Contest cloning | A clone keeps the freeze settings but starts unrevealed. 2026-09-21 |
| Removing contest problems | Contest authors can remove a problem from a contest they edit; the problem and its submissions stay. The only exemption from the owner-only deletion lock. 2026-10-07 |
| Contest PDF | Every statement of a contest in one PDF, for participants once it started and for organizers at any time; cached by content, rendered once at a time. 2026-10-19 |

## Duels

| Change | Notes |
| --- | --- |
| 1v1 duels | First-to-solve battles, alone or as best-of-N series, with partial or all-or-nothing points and optional penalties. At `/duelos/`; `/arena/` forwards there. Migration `0157`. 2026-10-17 |
| Tournaments | Knockout brackets of up to 64 players, seeded by hand, at random or by rating, with an optional third-place match; problems by hand or from problem banks. 2026-10-17 |
| Organizer panel | One permission, `judge.manage_arena`; versioned actions so several organizers can work at once; a guide in Spanish. 2026-10-17 |
| Player lock | Players stay on the duel page and submit only from it while their battle lasts; expires after a period without activity. 2026-10-17 |
| Projector screens | Secret, revocable links with combined, scoreboard, live code, statements and bracket views. 2026-10-17 |
| Duel contests out of the lists | The hidden contest behind each game stays out of `/contests/` and the admin's contest lists, and is deleted with its game. 2026-10-19 |

## Teams

| Change | Notes |
| --- | --- |
| Teams and invitations | Team model, membership, invitations, My teams page. Migration `0155`. 2026-09-18 |
| Individual, team and mixed contests | Participation mode per contest, separate rankings. 2026-09-18 |
| One access code per roster | Whoever registers carries contest access for the whole roster; personal blockers are still checked one by one. 2026-09-19 |
| Member selection | Pick who competes when registering, with the minimum and maximum measured on that selection. 2026-09-19 |
| Teams in the admin | `Team` and `TeamInvitation` registered, search by member, inline membership editing; a new owner is added to the members automatically. 2026-09-19 |

## Interface

| Change | Notes |
| --- | --- |
| Dark theme for everyone | No longer gated behind the `test_site` permission; `auto` follows the system. 2026-09-18 |
| Rebuilt custom test | Full-width editor, no line wrapping, output up to 64 KB, translated messages. 2026-09-18 |
| Translated navigation bar | Through a `dmoj-user` catalog that did not exist upstream. 2026-09-19 |
| Test case autofill | Pairs files from a zip, natural ordering, batch detection, point distribution. 2026-09-19 |
| Interactive problems | Interactor upload on the data page, `interactive:` written on every save, testlib detection with a bundled `testlib.h`, optional contestant feedback, a guide with a downloadable example. Migration `0156`. 2026-09-25 |
| Data archive replacement | Uploading an archive over an existing one lost both upstream; the new one is now stored and the old one removed. 2026-09-25 |
| Admin bulk selection | The bottom action bar of admin lists works again with Django 5.2 and stays in sync with the top one. 2026-09-20 |
| Sample tables | OmegaUp's `\|\|input` / `\|\|output` / `\|\|description` / `\|\|end` in statements, translations and editorials, as a compact table with its own style. 2026-10-18 |
| Inline math with dollars | `$...$` next to `~...~` in the same styles, with Pandoc's rules so prices stay text. 2026-10-18 |
| Phone layout | Phones get the 480 px layout from the first paint. Upstream switched only on `resize` and, at about one width in five, kept flipping between layouts. 2026-10-01 |
| Phone overflow fixes | MathJax formulas no longer make pages pan sideways, the contest timer keeps its clock inside the box, and the problem data page fits with its case table scrolling on its own. 2026-10-01 |
| Readable permission selector | The admin's two-list selector shows full permission names at any width. 2026-10-07 |
| Account emails | One design for activation, password reset and email change, readable in Gmail, Outlook and phone apps. 2026-10-08 |
| Site icons | A multi-size favicon, and the site's own artwork in the Windows tile and browser notifications. 2026-10-08 |
| Spanish interface | Spanish translations for every screen the fork adds. |

## Accounts

| Change | Notes |
| --- | --- |
| Registration fields | First and last name are required when registering. |
| Login by email | Sign in with the username or the email address; if several accounts share an address, the password decides which one. |
| Email limits | Two password reset emails per address and one email change per account in 24 hours, and a daily cap per UTC day with a reserve for existing accounts. 2026-10-14 |
| No stuck sign-ups | The activation email is sent inside the registration transaction; if it fails, no account is created and the address can register again. 2026-10-14 |

## Security

| Change | Notes |
| --- | --- |
| Owner account | `CPC_SERVER_OWNERS` decides who may delete from the admin and protects that account from other superusers. 2026-09-19 |
| Permanent deletion path | Dismantles the five `PROTECT` keys in order inside a transaction, after a confirmation that counts what disappears. Submissions are kept. 2026-09-19 |
| Impersonation fix | Upstream's `IMPERSONATE_REQUIRE_SUPERUSER` is ignored by the installed library, which left impersonation open to every staff account. 2026-09-19 |
| Clarification scope in the admin | Jury members can only file or move clarifications into contests they can edit, and a problem must belong to the same contest. 2026-09-21 |
| Custom test ownership | Signed ownership checks, read-only result polling, exclusion from shared histories and statistics. 2026-09-08 |
| Custom test ceilings | Two in flight, twelve per minute, two hundred per hour, per user, answered with HTTP 429. 2026-09-14 |
| Request body ceilings | 16M globally, 2M for custom tests, 500M only on the problem data upload route. 2026-09-19 |
| Upload check before buffering | Nginx asks the application whether the caller may upload problem data before reading the body. 2026-09-21 |
| Service isolation | One unprivileged account per service with systemd namespace restrictions. 2026-09-10 |
| Transport and origin | Redis authentication, loopback-only listeners, HSTS, structural CSP, referrer policy, secure cookies. 2026-09-11 |
| Visitor address behind a tunnel | Nginx takes the edge's header, from loopback only, so per-IP limits count each visitor. 2026-10-14 |
| Error report cap | Error emails to `ADMINS` are capped per day and never use the reserve that existing accounts need. 2026-10-14 |
| Event daemon input checks | Malformed messages, frames or long-polling URLs get an error instead of ending the daemon; message size caps. 2026-10-17 |

## Operations

| Change | Notes |
| --- | --- |
| Scheduled custom-test cleanup | Removes finished tests from every account, not only from whoever submits next. 2026-09-19 |
| Offline compression | Required for a read-only checkout; the guides explain what invalidates the manifest. 2026-09-14 |
| Django 5.2.17 | Upgrade with timezone, storage and dependency adaptations. 2026-09-12 |
| Reliable problem PDFs | A failed render no longer leaves an empty PDF in the cache; the PDF takes its title from the page; a patch and a unit for pdfoid. 2026-10-19 |

Git history and file-level diffs are the authoritative record.
See [MODIFICATIONS.md](../MODIFICATIONS.md) for the licensing statement.

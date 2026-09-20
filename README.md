<h1 align="center">
  <img src="https://github.com/DMOJ/online-judge/blob/master/logo.png?raw=true" width="120px">
  <br>
  DMOJ: Modern Online Judge
</h1>
<p align="center">
  <a href="https://github.com/DMOJ/online-judge/actions?query=workflow%3Abuild">
    <img alt="Build Status" src="https://img.shields.io/github/actions/workflow/status/DMOJ/online-judge/build.yml?branch=master"/>
  </a>
  <a href="LICENSE">
    <img alt="License" src="https://img.shields.io/github/license/DMOJ/online-judge"/>
  </a>
  <a href="https://dmoj.ca/about/discord/">
    <img src="https://img.shields.io/discord/677340492651954177?color=%237289DA&label=Discord"/>
  </a>
</p>

## A DMOJ fork for ICPC-style contests

This branch is the source of the DMOJ installation operated by **CPC-UAEH**. It
adds contest tooling, team support, a Spanish interface and a set of hardening
changes to upstream DMOJ, and it is published so that anyone can fork it and run
the same thing.

**Start here:** [Setting up this fork](docs/setup-guide.md) — install a stock
DMOJ first, then follow that guide.
Full change list: [fork overview](docs/fork-overview.md) ·
[MODIFICATIONS.md](MODIFICATIONS.md).

Based on DMOJ commit `97f3722ef3f7ca9731727c220622bdd1eab7d4b3` and distributed
under the GNU Affero General Public License version 3. Production credentials,
user data, problem packages, uploaded media, logs and generated static files are
deliberately not part of this repository.

---

## Contests

**Scoreboard freeze.** A configurable freeze window that actually freezes: the
board, the participation pages, the API, the submission list, the live event
feed, the submission detail and the statistics. A scoreboard that is frozen
while the submission list stays open is not frozen. Unfreezing is explicit.

![Frozen scoreboard](docs/screenshots/scoreboard-freeze.png)

**Reveal ceremony.** The ICPC reveal, driven entirely by the server so a slow
projector laptop cannot get the order wrong. Adjustable speed, medal ranges,
respected ties, first-to-solve awards, and an award card for each contestant as
their position becomes final.

![Reveal ceremony](docs/screenshots/reveal-ceremony.png)

**Balloon desk.** Pending and delivered lists that update live, delivery history
with undo, per-problem colors chosen with a real color picker, per-participant
locations, and a staff permission of its own. Accepted submissions inside the
frozen window earn no balloon.

![Balloon desk](docs/screenshots/balloon-desk.png)

**Announcements and clarifications.** Announcements reach the whole site or a
single contest and are archived on a per-contest tab. Contestants ask the jury
about the contest or about one problem; answers are private to whoever asked or
broadcast to everyone. Bodies are plain text, rate limited and length capped.

![Announcements](docs/screenshots/announcements.png)

**Rankings that refresh themselves**, polling only while the tab is visible and
backing off when the server is slow, over an event daemon with a heartbeat and a
browser client that reconnects with exponential backoff.

## Teams

**Teams, invitations and team contests.** Contests run in individual, team or
mixed mode, with separate rankings for each.

![My teams](docs/screenshots/teams-my-teams.png)

**One access code for the whole roster.** Whoever registers the team types the
code once and the entire roster is in. Contest access is checked against that
person; what nobody can delegate is still checked one by one — a disabled
account, a ban from that contest, running the contest yourself — and the message
names who is blocking and why.

**Pick who competes.** When registering a team you tick which members take part,
and the minimum and maximum team sizes are measured on that selection.

![Contest registration](docs/screenshots/contest-join.png)

Teams are visible and editable from the admin, searchable by member, with
membership edited inline.

![Teams in the admin](docs/screenshots/admin-teams.png)

## Interface

**Dark theme for every account.** Upstream hides it behind the `test_site`
permission; here it is available to everyone, and `auto` follows the operating
system so a reader in dark mode gets a dark site without asking.

![Light and dark](docs/screenshots/dark-mode.png)

**Rebuilt custom test.** The editor uses the available width and height and no
longer wraps long lines; output is no longer cut at 64 bytes; the messages
inside the JavaScript are translated.

![Custom test](docs/screenshots/custom-test.png)

**Test cases filled in from the zip.** Upload an archive whose files are named
`case1.in` and `case1.out` and the rows build themselves: pairing by extension
or by folder, natural ordering so `case2` comes before `case10`, batch detection
from the `1-1` / `1_2` convention, and an exact integer point split. Files left
without a pair are reported rather than dropped silently.

![Test case autofill](docs/screenshots/testcase-autofill.png)

**A Spanish interface**, including the navigation bar, which upstream leaves
untranslated because it reads from a catalog that did not exist.

## Security

**An owner account that other superusers cannot touch.** `is_superuser` short
circuits Django's permission check, so no permission can separate one superuser
from another; the separation is enforced in the admin instead. The account named
in `CPC_SERVER_OWNERS` is the only one that may delete anything, hand out the
Staff and Superuser checkboxes, or read two-factor secrets, and it is the only
one that can edit itself.

![Owner-only deletion](docs/screenshots/admin-owner-lock.png)

**A permanent deletion path.** Five `PROTECT` keys stop a contest or a team with
history from being deleted by accident. None of them was removed; instead there
is an explicit route that dismantles the dependencies in order inside a
transaction, behind a confirmation that counts exactly what will disappear.
Submissions themselves are kept — only their link to the contest goes.

**Impersonation, actually restricted.** `IMPERSONATE_REQUIRE_SUPERUSER` was
being ignored by the installed library, which reads a dictionary instead, so
every staff account could impersonate any non-superuser. Fixed, with the audit
log deliberately left on.

**Custom tests.** Signed ownership checks, read-only result polling, exclusion
from shared submission histories and statistics, and per-user ceilings of two in
flight, twelve per minute and two hundred per hour, answered with HTTP 429.

**Deployment hardening.** One unprivileged account per service under systemd
namespace restrictions, Redis authentication, loopback-only listeners, request
body ceilings with a single documented exception for problem archives, HSTS,
structural CSP, referrer policy and secure cookies. The
[maintenance record](docs/security/README.md) documents each change with its
verification scope and its remaining limitations.

## Running it

[Setting up this fork](docs/setup-guide.md) walks through settings, migrations,
translations, styles, the owner account, services, the front end and the
scheduled cleanup, and ends with a checklist of things that have each caught a
real regression. [Sanitized deployment examples](deploy/examples/README.md)
contain the systemd units, the front-end configuration and the maintenance
timer, with every path and host name invented.

---

# Upstream DMOJ

Everything below is the upstream project's own README, kept as it is. The
installation instructions it links to are the ones to follow first; this fork's
[setup guide](docs/setup-guide.md) picks up from there.

A modern open-source online judge and contest platform system. It has been used to host thousands of competitions, including several national olympiads.

See it live at [dmoj.ca](https://dmoj.ca/)!

## Features

* [Support for over **60 language runtimes**](https://github.com/DMOJ/online-judge#supported-languages)
* Highly robust judging system:
   * Supports **interactive** and **signature-graded** tasks
   * Supports **runtime data generators** and **custom output validators**
   * Specifying **per-language resource limits**
   * Capable of scaling to hundreds of judging servers
* Extremely configurable contest system:
   * Supports ICPC/IOI/AtCoder/ECOO formats out-of-the-box
   * **System testing** supported
   * **Hidden scoreboards** and **virtual participation**
   * [Elo-MMR](https://arxiv.org/abs/2101.00400)-style **rating**
   * **Plagiarism detection** via [Stanford MOSS](https://theory.stanford.edu/~aiken/moss/)
   * Restricting contest access to particular organizations or users
* Rich problem statements, with support for **LaTeX math and diagrams**
   * Automatic **PDF generation** for easy distribution
   * Built-in support for **editorials**
* **Live updates** for submissions
* Internationalized site interface
* Home page blog and activity stream
* Fine-grained permission control for staff
* OAuth login with Google, Facebook, and Github
* Two-factor authentication support

## Installation

Check out the install documentation at [docs.dmoj.ca](https://docs.dmoj.ca/#/site/installation). Feel free to reach out to us on [Discord](https://dmoj.ca/about/discord/) if you have any questions.

## Screenshots

### Sleek problem statements
Problems are written in Markdown, with LaTeX-enabled math and figures, as well as syntax highlighting. Problem statements can be saved to PDF for ease of distribution to contestants.

![](https://i.imgur.com/7KD7h5r.png)

### Submit in over 60 languages
Contestants may submit in over 60 programming languages with syntax highlighting. Problem authors can restrict problems to specific languages, and set language-specific resource limits.

![](https://i.imgur.com/8CjfHQb.png)

### Live submission status
Submission pages feature live updates, and submissions may be aborted by both submission authors and administrators. Compilation errors and warnings for a number of languages feature color highlighting.

![](https://i.imgur.com/Hom0U3R.png)

Global, per-problem, and per-contest submission lists are live-updating, and can be filtered by status and language.

![](https://i.imgur.com/rc7orzj.png)

### Extensible contest system
Contests feature an optional rating system, and can be configured to run in any timeframe. Users are also able to participate virtually after the contest ends. ICPC, IOI, AtCoder, and ECOO contest formats are supported out-of-the-box, and new formats can be added with custom code.

![](https://i.imgur.com/0V1fzZi.png)

Contests may be limited to particular organizations, or require access codes to join. Hidden scoreboards are supported. The contest system integrates with [Stanford MOSS](https://theory.stanford.edu/~aiken/moss/) to provide plagiarism checking.
Editorial support is built-in, and editorials are automatically published once a contest ends.

### Home page blog and activity stream

Announcements from administrators, ongoing contests, recent comments and new problems are easily accessible from the home page.

![](https://i.imgur.com/zpQAoDB.png)

### Internationalized interface
Use the site in whatever language you're most comfortable in &mdash; visit [translate.dmoj.ca](https://translate.dmoj.ca/) to check the translation status of your preferred language. Problem authors can provide statements in multiple languages, and DMOJ will display the most relevant one to a reader.

![](https://i.imgur.com/OeuI0o5.png)

### Highly featured administration interface
The DMOJ admin interface is highly versatile, and can be efficiently used for anything from managing users to authoring problem statements.

![](https://static.dmoj.ca/data/_other/readme/problem-admin.png)

![](https://static.dmoj.ca/data/_other/readme/admin-dashboard.png)

## Supported languages

Check out [**DMOJ/judge-server**](https://github.com/DMOJ/judge-server) for more judging backend details.

Supported languages include:
* C++ 11/14/17/20 (GCC and Clang)
* C 99/11
* Java 8-22
* Python 2/3
* PyPy 2/3
* Pascal
* Mono C#/F#/VB

The judge can also grade in the languages listed below:
* Ada
* Algol 68
* AWK
* COBOL
* D
* Dart
* Fortran
* Forth
* Go
* Groovy
* GAS x86/x64/ARM
* Haskell
* INTERCAL
* Kotlin
* Lua
* LLVM IR
* NASM x86/x64
* Objective-C
* OCaml
* Perl
* PHP
* Pike
* Prolog
* Racket
* Ruby
* Rust
* Scala
* Chicken Scheme
* sed
* Steel Bank Common Lisp
* Swift
* Tcl
* Turing
* V8 JavaScript
* Brain\*\*\*\*
* Zig

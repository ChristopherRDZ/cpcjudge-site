> **Contest features, 2026-09-15.** Announcements and clarifications,
> self-refreshing contest rankings and resilient live updates, with the new
> interface translated into Spanish. See
> [the highlights](#contest-features-added-by-cpc-uaeh--2026-09-15).

> **Django 5.2.17 production source.** The `cpc-production` branch includes the
> deployed Django upgrade. See the [upgrade record](docs/django52/README.md) for
> adaptations, configuration guidance and the scope of validation.

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

## CPC-UAEH production fork

The `cpc-production` branch contains the source code corresponding to the
modified DMOJ version operated by CPC-UAEH. It is based on DMOJ commit
`97f3722ef3f7ca9731727c220622bdd1eab7d4b3` and is distributed under the GNU
Affero General Public License version 3.

See [MODIFICATIONS.md](MODIFICATIONS.md) for the changes made to the upstream
project. Production credentials, user data, problem packages, uploaded media,
logs, and generated static files are intentionally not part of this source
repository.

The [security maintenance record](docs/security/README.md) documents the deployed
application fixes and generic deployment controls, with their validation limits.

## Contest features added by CPC-UAEH — 2026-09-15

Highlights of the contest tooling built on top of the upstream feature set:

- **Announcements and clarifications.** Announcements reach either the whole site
  or a single contest, are shown as a dismissable overlay and stay archived on a
  per-contest Clarifications tab. Contestants ask the jury questions, optionally
  about one problem; the jury answers privately to whoever asked, or broadcasts
  the answer to every contestant, in which case it is also archived as an
  announcement of that contest. Announcement and question bodies are plain text:
  neither HTML nor Markdown is interpreted. Questions are rate limited per user
  and capped in length. Adds migrations `0150`–`0152`.
- **Self-refreshing contest rankings.** The scoreboard now refreshes on its own
  instead of requiring a manual reload. It reuses the existing ranking fragment
  endpoint, polls only while the tab is visible and the viewer may see the full
  scoreboard, preserves the reader's column preferences across refreshes, backs
  off when the server answers slowly and stops shortly after the contest ends.
- **Resilient live updates.** The event daemon sends a periodic heartbeat and the
  browser client reconnects with exponential backoff, falling back to long
  polling and reconnecting when the tab or the network comes back. Long-lived
  submission, ticket and clarification pages no longer show a disconnection
  warning after an idle edge timeout.
- **Spanish interface for the new screens.** Every string introduced by the
  features above is translated in `locale/es`.

The complete list of fork changes remains in [MODIFICATIONS.md](MODIFICATIONS.md).

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

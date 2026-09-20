# CPC-UAEH modifications to DMOJ

As of 2026-09-19, this source includes team support and team contests, an owner
account that restricts deletion in the administration interface, a dark theme
available to every account, test cases filled in from a problem data archive,
frozen scoreboards, a reveal ceremony, balloon operations, contest announcements
and clarifications, self-refreshing rankings, resilient live updates, custom-test
admission controls and the deployed Django 5.2.17 upgrade.
Timezone, storage/dependency and contest administration adaptations are
documented in the [upgrade record](docs/django52/README.md). A grouped list of
every change is in the [fork overview](docs/fork-overview.md).

This repository contains a modified version of
[DMOJ](https://github.com/DMOJ/online-judge), originally distributed under the
GNU Affero General Public License version 3.

## Version information

- Upstream base commit: `97f3722ef3f7ca9731727c220622bdd1eab7d4b3`
- Initial publication date: 2026-09-07
- Modified version maintained by: CPC-UAEH
- Corresponding source branch: `cpc-production`

## Summary of modifications

- CPC-UAEH branding, icons, site footer, and registration email presentation.
- First-name and last-name fields in account registration.
- Authentication using either a username or an email address.
- A signed-in custom code testing workflow and its judge bridge support.
- Minor registration and presentation adjustments.

## Administration and deletion control — 2026-09-19

- `CPC_SERVER_OWNERS` in settings names the accounts allowed to delete from the
  administration interface. The restriction is applied in the admin rather than
  through a permission, because `PermissionsMixin.has_perm` returns True for any
  superuser without consulting permissions or backends. Two layers: the wrapped
  `has_delete_permission` of every registered ModelAdmin and inline, and the
  `delete_view`, `delete_model` and `delete_queryset` of `ModelAdmin`.
- The owner account is protected from other superusers: its user and profile
  cannot be edited, the Staff and Superuser fields are removed from the form for
  anybody else rather than shown read-only, and TOTP records are reserved.
- A permanent deletion path for teams and contests that dismantles the five
  `PROTECT` relations in order inside a transaction, behind a confirmation that
  enumerates what will be destroyed. Submissions are preserved; only their link
  to the contest is removed. No `PROTECT` relation was dropped.
- `IMPERSONATE_REQUIRE_SUPERUSER` was ignored by the installed
  django-impersonate, which reads an `IMPERSONATE` dictionary; impersonation was
  therefore open to every staff account. Corrected, with the audit log enabled.
- Teams and team invitations registered in the administration interface, with
  search by member, member and participation columns, inline membership editing
  and invitation cancellation.
- No migration and no schema or data change.

## Teams and team contests — 2026-09-18 and 2026-09-19

- Teams, memberships and invitations, with a My teams page. Migration `0155`.
- Individual, team and mixed participation modes per contest, a single official
  registration, shared virtual participations and separate rankings.
- Registration admits the whole roster on one access code: contest access is
  evaluated for whoever registers, while conditions that cannot be delegated —
  an inactive account, a ban from that contest, organising or testing it — are
  still evaluated per member, and the message names who is blocked and why.
- The members who compete are selected at registration time and frozen; minimum
  and maximum team sizes are measured on that selection.
- Member photographs in the award ceremony and balloons per team.

## Interface — 2026-09-18 and 2026-09-19

- The dark theme no longer depends on the `judge.test_site` permission and is
  available to every account; `auto` follows the operating system preference.
  The `{% compress %}` blocks of `base.html` were left byte-identical and the
  dark stylesheet is linked outside them, so the offline manifest stays valid.
- The custom test page was rebuilt: full width and height editor without line
  wrapping, output no longer truncated at 64 bytes, a configurable output prefix
  and translated messages inside the JavaScript.
- The navigation bar is translated through a `dmoj-user` catalog, which upstream
  does not provide.
- Test cases are filled in from an uploaded archive: pairing by extension or by
  folder, natural ordering, batch detection, an exact integer point split, and a
  report of files left without a pair. The view also reports how many form rows
  fit, since the form posts 14 fields per row.
- A scheduled cleanup for finished custom tests across all accounts, published
  as a deployment example rather than as application code.

## Freeze, reveal and balloon operations — 2026-09-16

- Configurable scoreboard freeze with snapshots and filtering across shared
  submission views, statistics, APIs and public events; explicit reveal.
- Reveal ceremony with animated standings, adjustable speed, result colors,
  per-participant award cards, medal ranges, tied ranks and first-to-solve awards.
- Balloon desk with dedicated staff permissions, live updates, delivery/undo
  history, problem colors and the rule that frozen-window ACs earn no balloon.
- Theme-aware contrast, explicit All filter and searchable, paginated location
  fields; updates preserve untouched rows and detect conflicting editor changes.
- Native color picker, editable hex and No color control for contest problems,
  including dynamically added administration rows.
- Migrations `0153` and `0154`, Spanish translations and isolated regression tests.

See the [organiser guide](docs/contest-tools.md) and
[test instructions](tests/contest_features/README.md).

## Contest features — 2026-09-15

- Site-wide and per-contest announcements, shown as a plain-text overlay and
  archived on a per-contest clarifications tab, with an administration form that
  picks the target and when the overlay stops appearing.
- Contest clarifications: contestants ask the jury about the contest or about a
  single problem, and answers are either private to whoever asked or broadcast
  to every contestant, in which case they are archived as an announcement of
  that contest. Question bodies are plain text, length capped and rate limited
  per user.
- Contest rankings that refresh themselves while the tab is visible, reusing the
  existing ranking fragment endpoint, which now also carries the editor controls
  and the table identifier it previously omitted.
- A heartbeat in the event daemon and reconnection with exponential backoff in
  the browser client, with long polling as a fallback.
- Spanish translations for the strings these features introduce.
- Django migrations `0150`, `0151` and `0152`.

## Security maintenance through 2026-09-14

- Signed ownership checks and read-only result polling for custom tests, with
  bounded cleanup of verified completed tests and transaction-safe file handling.
- Personal test exclusion from shared submission histories, API lists and
  statistics, with explicit owner/administrator access to source details.
- Documented deployment isolation, private configuration/backup boundaries,
  Redis authentication, Nginx request/origin restrictions and HTTPS controls.
- Updated public configuration examples and private-artifact ignore rules.
- Per-user custom-test fixed windows and an in-flight admission check, with
  HTTP 429 feedback; documented concurrency and retention limitations.
- A uWSGI request-timeout example, structural CSP/referrer headers, added judge
  isolation controls, Supervisor retirement, DNS/error-reporting repairs and
  offline-compression build requirements.

See the [security maintenance record](docs/security/README.md) for implementation
dates, verification scope and remaining limitations. Infrastructure changes are
represented by documentation and generic examples; their actual host settings,
credentials, user data and recovery records remain private.

## Django 5.2 upgrade — 2026-09-12

- Django 5.2.17, django-mptt 0.18.0 and django_compressor 4.6.0 requirements.
- Standard-library UTC and ZoneInfo support, updated static storage guidance,
  and one CompressorFinder in the base settings.
- Contest participant bans applied after a valid saved form, preserving
  participation when an administrative form is rejected.
- Published historical synthetic compatibility tests and tested dependency
  constraints. See the upgrade record for postdeployment checks and limits.

Git history and file-level diffs are the authoritative record of the exact
changes. The complete corresponding source is available without charge at
<https://github.com/ChristopherRDZ/online-judge/tree/cpc-production>.

## License

This modified version is distributed under the GNU Affero General Public
License version 3. The complete license text is provided in [LICENSE](LICENSE).
Existing copyright and license notices from DMOJ and bundled third-party
components are retained.

Branding artwork is included as part of the deployed version by CPC-UAEH. Its
inclusion does not grant rights to third-party trademarks beyond those provided
by their respective owners.

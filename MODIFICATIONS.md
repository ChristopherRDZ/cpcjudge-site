# CPC-UAEH modifications to DMOJ

As of 2026-09-15, this source includes contest announcements and clarifications,
self-refreshing rankings, resilient live updates, custom-test admission controls
and the deployed Django 5.2.17 upgrade.
Timezone, storage/dependency and contest administration adaptations are
documented in the [upgrade record](docs/django52/README.md).

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

# CPC-UAEH modifications to DMOJ

As of 2026-09-12, this source includes the deployed Django 5.2.17 upgrade.
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

## Security maintenance through 2026-09-11

- Signed ownership checks and read-only result polling for custom tests, with
  bounded cleanup of verified completed tests and transaction-safe file handling.
- Personal test exclusion from shared submission histories, API lists and
  statistics, with explicit owner/administrator access to source details.
- Documented deployment isolation, private configuration/backup boundaries,
  Redis authentication, Nginx request/origin restrictions and HTTPS controls.
- Updated public configuration examples and private-artifact ignore rules.

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

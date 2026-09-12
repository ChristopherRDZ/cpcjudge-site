# Django 5.2 production upgrade

Status: **Django 5.2.17 deployed and subsequently checked**, recorded on
2026-09-12 UTC. The `cpc-production` branch includes the upgrade and the existing
application hotfixes. The earlier laboratory results remain historical evidence;
publishing this source does not deploy it to another installation.

## Deployed adaptations

| File | Adaptation |
| --- | --- |
| `judge/jinja2/datetime.py`, `judge/views/user.py`, `judge/views/contests.py` | Use `datetime.timezone.utc` instead of the removed Django alias |
| `judge/timezone.py` | Use `zoneinfo.ZoneInfo` for user timezones |
| `judge/admin/contest.py` | Apply participant bans after validated save; see [admin-validation.md](admin-validation.md) |
| `dmoj/settings.py` | Remove `USE_L10N` and add `CompressorFinder` |
| `requirements.txt` | Pin Django 5.2.17, django-mptt 0.18.0 and django_compressor 4.6.0 |

All seven application/requirements files match the deployed source and laboratory hashes
recorded in [candidate-code-sha256.json](candidate-code-sha256.json).
The tested minifiers are rcssmin 1.2.1 and rjsmin 1.2.4. The complete recorded
distribution versions are in [constraints-tested.txt](constraints-tested.txt).
These constraints are a record of the tested environment, not permission to
upgrade an active environment or a claim that every distribution is current.

Preserve the DMOJ VCS forks and their commit identities, including wpadmin,
fernet-fields, jsonfield and ansi2html. A matching version number from PyPI does
not establish that it contains the fork used in the test environment. Keep
the original requirement origins and review those identities when rebuilding.

Private static storage overrides also require the small
[settings fragment](settings.example.py): use `STORAGES` instead of
`STATICFILES_STORAGE`, retaining the default filesystem storage, and remove
private `USE_L10N` overrides. Keep `CompressorFinder` exactly once: remove an
old private append if the base settings now provide it. Preserve the
service-specific media/problem/log paths and credentials. The fragment is not a
complete configuration.

## Historical verification

- Original application suite: 79/79 on baseline and candidate.
- Additional compatibility checks: baseline 24/25, candidate 25/25. The baseline
  failure was the contest administration side effect fixed separately here.
- Covered administration, registration/login/password reset, TOTP, encrypted
  fields, timezones/calendar, ICPC participation/results, personal test privacy,
  static/media handling, SQL listings and API behavior.
- Separate private-network integration exercised uWSGI, bridge/judge, events
  and Celery/Redis, including actual Python, C++ and Java evaluation.
- A synthetic migration rehearsal preserved application data and contest
  results. The same 200 migration names were present; this candidate added no
  migrations. Automatic migration generation also reported inherited model
  drift, so do not generate a new migration without a separate review.

The additional synthetic test module is preserved at
[tests/django52/compat_tests.py](../../tests/django52/compat_tests.py).
See its [execution boundary](../../tests/django52/README.md). Host launchers,
raw logs, operational inventories and private fixture infrastructure are not
part of the public repository. These counts are recorded laboratory results;
source publication rechecked hashes and syntax, not the complete integration.

## Postdeployment checks and maintenance

Read-only checks after deployment confirmed the expected application and
dependency versions, consistent migration history with no planned migrations,
and successful HTTP/API/calendar/static responses. Login and registration
forms were also observed in a browser. These checks did not repeat credentialed
login, administrative writes, new submissions or the full laboratory suite.

The static manifest format and existing compressed output were checked for this
upgrade. No new static asset generation was needed for this specific deployment.
That is not a general instruction to skip static builds: deployments with
read-only static trees must provide all required generated output, or a narrowly
scoped writable output directory. Offline compression requires coverage of
template context variants and is not enabled by this source publication.

Before another rollout, recheck source/configuration drift, verify a backup
including the virtual environment, rehearse recovery and test with the current
private configuration. Do not copy laboratory settings onto production or
restore obsolete Redis settings. Keep manifests, generated resources and
application source consistent when building or rolling back a release.

The laboratory did not test full contest-scale load, every widget visually,
external SMTP/OAuth services, physical WebAuthn devices or recovery on another
machine. Source publication did not run production migrations, install packages
or restart services. Operational settings, inventories, logs and recovery
artifacts remain private.

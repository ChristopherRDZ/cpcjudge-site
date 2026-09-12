# Django 5.2 laboratory candidate

Status: tested in isolation on 2026-09-11; **not deployed**. This branch builds on
the production-source hotfixes. `cpc-production` continues to represent the
deployed application and its original Django requirement.

## Preserved candidate

| File | Adaptation |
| --- | --- |
| `judge/jinja2/datetime.py`, `judge/views/user.py`, `judge/views/contests.py` | Use `datetime.timezone.utc` instead of the removed Django alias |
| `judge/timezone.py` | Use `zoneinfo.ZoneInfo` for user timezones |
| `judge/admin/contest.py` | Apply participant bans after validated save; see [admin-validation.md](admin-validation.md) |
| `dmoj/settings.py` | Remove `USE_L10N` and add `CompressorFinder` |
| `requirements.txt` | Pin Django 5.2.17, django-mptt 0.18.0 and django_compressor 4.6.0 |

All seven application/requirements files match the laboratory candidate hashes
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
private `USE_L10N` overrides. Preserve the service-specific media/problem/log
paths and credentials. The fragment is not a complete configuration.

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

## Deployment boundary

Before any future rollout, recheck source and configuration drift, obtain a
verified backup including the virtual environment, rehearse recovery, and test
the candidate with the then-current private Redis/HTTPS configuration. The
laboratory preceded those later infrastructure updates. Do not copy laboratory
settings onto production or restore an older unauthenticated Redis URL.

The laboratory did not test full contest-scale load, every widget visually,
external SMTP/OAuth services, physical WebAuthn devices or recovery on another
machine. No production migration, package installation or service restart was
performed to create these commits.

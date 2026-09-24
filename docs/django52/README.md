# Django 5.2

This fork runs on **Django 5.2.17**. Upstream DMOJ at the fork's base commit used
Django 4.2, whose extended support ended in April 2026.

## What changed in the code

| File | Change |
| --- | --- |
| `judge/jinja2/datetime.py`, `judge/views/user.py`, `judge/views/contests.py` | `datetime.timezone.utc` instead of the removed Django alias |
| `judge/timezone.py` | `zoneinfo.ZoneInfo` for user time zones |
| `judge/admin/contest.py` | participant bans applied after the form is saved; see [admin-validation.md](admin-validation.md) |
| `dmoj/settings.py` | `USE_L10N` removed, `CompressorFinder` added |
| `requirements.txt` | Django 5.2.17, django-mptt 0.18.0, django_compressor 4.6.0 |

[`constraints-tested.txt`](constraints-tested.txt) lists the exact versions of
every package in an environment where this worked, including the minifiers
(rcssmin 1.2.1, rjsmin 1.2.4). Use it as a reference, not as a lock file.

Several requirements point to DMOJ's own forks (wpadmin, fernet-fields,
jsonfield, ansi2html). Install them from the sources in `requirements.txt`: the
PyPI package with the same version number is not the same code.

## Upgrading an existing installation

1. Take a database dump and a copy of `local_settings.py`.
2. Install the new requirements in a fresh virtualenv.
3. Apply the [settings changes](settings.example.py) to your private settings:
   `STORAGES` instead of `STATICFILES_STORAGE`, no `USE_L10N`, and
   `CompressorFinder` only once.
4. Run `manage.py migrate`. The Django upgrade itself adds no migrations; the
   fork's features add `0150` to `0155`.
5. Rebuild static files, and the offline compression manifest if you use it.
6. Restart the web service, Celery and the bridge, and check login, a submission,
   the admin and a contest page.

Do not run `makemigrations` to "fix" warnings: the inherited models have small
differences in metadata (choices, labels) that do not need a migration.

## Tests

[tests/django52](../../tests/django52/README.md) contains an optional
compatibility test module. It needs a disposable database and must never run
with production settings.

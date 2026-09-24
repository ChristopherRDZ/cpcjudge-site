# Setting up this fork

This guide configures the fork's contest tools, team support and deployment
controls on top of a working DMOJ installation.

First follow the [upstream installation instructions](https://docs.dmoj.ca/) and
verify login, problem creation and submission judging before continuing.

Paths used throughout: `/srv/dmoj/site` for the checkout, `/srv/dmoj/venv` for
the virtualenv, `/srv/dmoj/problems` for problem data, `judge.example.org` for
the host name. Substitute your own.

---

## 1. Get the code

```sh
git clone --recursive https://github.com/ChristopherRDZ/cpcjudge-site.git /srv/dmoj/site
cd /srv/dmoj/site
git checkout cpc-production
```

`--recursive` matters: `resources/libs` is a submodule and the styles do not
build without it. If you already cloned without it, run
`git submodule update --init --recursive`.

If you are moving an existing installation, take a database dump first. Several
of the steps below add tables.

## 2. Private settings

Copy the example and edit it:

```sh
cp dmoj/local_settings.example.py dmoj/local_settings.py
```

The example carries the usual DMOJ settings plus the ones this fork adds. The
four that matter most:

```python
# Who may delete from the admin. Everyone else, superusers included, cannot.
CPC_SERVER_OWNERS = ('your-owner-username',)

# django-impersonate reads only this dictionary. The loose IMPERSONATE_*
# variables DMOJ ships are silently ignored by the installed version.
IMPERSONATE = {'REQUIRE_SUPERUSER': True, 'ALLOW_SUPERUSER': False,
               'DISABLE_LOGGING': False, 'URI_EXCLUSIONS': (r'^admin/',)}

# Each test-case row posts 14 fields. The Django default of 1000 caps the
# problem data form at roughly 70 rows, which the zip autofill hits at once.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10240

# Custom-test ceilings per user.
CPC_CUSTOM_TEST_MAX_IN_FLIGHT = 2
CPC_CUSTOM_TEST_MAX_PER_MINUTE = 12
CPC_CUSTOM_TEST_MAX_PER_HOUR = 200
```

Also set `SITE_NAME`, `SITE_LONG_NAME` and `SITE_ADMIN_EMAIL`, and review the team
settings at the end of the example (`CPC_TEAMS_ENABLED` pauses new teams and team
registrations without touching existing ones).

Never commit `dmoj/local_settings.py`. It is already in `.gitignore`.

## 3. Database

```sh
/srv/dmoj/venv/bin/python manage.py migrate
```

This fork adds migrations `0150` through `0155`: announcements, clarifications,
scoreboard freeze, balloons and teams. Keep the database backup from step 1
before applying them to an existing installation.

## 4. Translations

The interface ships Spanish and English catalogs:

```sh
/srv/dmoj/venv/bin/python manage.py compilemessages -l es -l en
```

Compile **both** catalogs so each language displays the correct translations.

> **Do not run `makemessages` on this tree.** Since Django 5.2 the extractor no
> longer recognises `{{ make_tab(..., _('Text')) }}`, and regenerating the
> catalog silently drops around 90 live translations: contest tabs, the
> calendar, the footer. Add new entries by hand, or extract into a scratch copy
> and merge only what is new.

Some message ids in the team and admin code are written in Spanish, because that
is the language they were authored in. They therefore need an entry in **both**
catalogs: an identity entry in `es` and a real translation in `en`. If you add
strings, keep that pattern or English users will see Spanish text.

## 5. Styles and static files

```sh
./make_style.sh
/srv/dmoj/venv/bin/python manage.py collectstatic --noinput
/srv/dmoj/venv/bin/python manage.py compilejsi18n
```

`make_style.sh` builds both the light and the dark stylesheet. The dark theme in
this fork is available to every account, not only to users with the
`test_site` permission as upstream has it.

### Offline compression

If you enable `COMPRESS_OFFLINE` (recommended for a read-only deployment; see
[offline compression](security/offline-compression.md)):

```sh
/srv/dmoj/venv/bin/python manage.py compress --force
```

Changes that affect a rendered compression block can invalidate the offline
manifest. Regenerate it after changes to templates, stylesheets, scripts or
translations, then restart the web service. Verify the affected pages in both
languages; a stale manifest can cause HTTP 500 responses on specific pages.

## 6. The owner account

Create your superuser as usual, then put its **exact** username in
`CPC_SERVER_OWNERS`. Case matters.

That single setting is what separates the owner from the other superusers. It
lives in code rather than in the database on purpose: a flag, a group or a
permission would let any superuser grant it to themselves from the admin. What
it controls:

- deleting anything from the admin, including the bulk actions;
- editing the owner's own user and profile;
- handing out the Staff and Superuser checkboxes;
- viewing two-factor secrets;
- the permanent-delete path for teams and contests.

An empty setting disables deletion from the admin for every account. Configure
the intended owner accounts before granting staff access.

## 7. Services

Bridge, event daemon, Celery and the web application all run separately. The
[deployment examples](../deploy/examples/README.md) contain one systemd unit per
service, each under its own unprivileged account, plus the shared namespace
restrictions.

Judges connect to the bridge; see
[`judge.example.yml`](../deploy/examples/judge.example.yml) and register each one
in the admin to get its key.

## 8. Front end

Use [`nginx.conf`](../deploy/examples/nginx.conf) and its
[service unit](../deploy/examples/systemd/dmoj-nginx.service) as the starting
point. These details are deliberate and easy to lose:

- **`client_max_body_size 16M` globally, 500M only on the problem data route.**
  Problem archives can be large; nothing else needs more.
- **The problem data route asks the application before reading the upload**
  (`auth_request` to `/internal/problem-data-upload-gate`). Anonymous users and
  accounts that cannot edit problems get 403 without the server receiving the
  file. Keep `client_max_body_size 0` and `uwsgi_pass_request_body off` in that
  internal location, or every real upload fails with 500.
- **`client_body_temp_path` on disk** (`/var/lib/dmoj-nginx/client`, created by
  `StateDirectory=` in the unit), not on a RAM-backed tmpfs.
- **`client_max_body_size 2M` on `/custom-test/run/`**, which is all a custom
  test needs.
- **`location /static/` with the trailing slash.** Without it, `/static../X`
  serves any file of the checkout.

If a CDN sits in front of the site, its own upload limit also applies:
Cloudflare's Free and Pro plans refuse requests over 100 MB.

After editing, `nginx -t` and then **reload**. Changes to the unit or to a
`listen` line need `systemctl daemon-reload` and a restart instead. See
[Nginx](security/nginx.md) for the reasons behind each setting.

## 9. Custom-test cleanup

A custom test creates three things: a data directory, a hidden `Problem` row
named after the feature, and a submission. Upstream cleans them up only when the
same user runs another test, so accounts that test once and never return leave
their rows behind and the admin problem list fills up.

Install the scheduled cleanup:

```sh
sudo install -d -m 0755 /usr/local/lib/dmoj-maintenance
sudo install -m 0755 deploy/examples/cleanup_custom_tests.py \
    /usr/local/lib/dmoj-maintenance/cleanup_custom_tests.py
sudo install -m 0644 deploy/examples/systemd/dmoj-custom-test-cleanup.service \
    deploy/examples/systemd/dmoj-custom-test-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dmoj-custom-test-cleanup.timer
```

Run it once by hand first. Without `--ejecutar` it only reports:

```sh
sudo -u dmoj-uwsgi env PYTHONDONTWRITEBYTECODE=1 HOME=/nonexistent \
    /srv/dmoj/venv/bin/python -B /usr/local/lib/dmoj-maintenance/cleanup_custom_tests.py
```

It never touches a test that is still being judged, nor one newer than the grace
period (20 minutes by default, `--minutos`).

## 10. Your own name and branding

The fork ships with the CPC-UAEH name and artwork. To use your own:

| What | Where |
| --- | --- |
| Site name and admin email | `SITE_NAME`, `SITE_LONG_NAME`, `SITE_ADMIN_EMAIL` in `local_settings.py` |
| Logo in the navigation bar | `resources/icons/logo.png` and `resources/icons/logo_dark.png` (dark theme) |
| Favicons and app icons | the other images in `resources/icons/` |
| Logo on the error page and in the README | `logo.png` in the repository root |
| Footer text | the `Hecho por CPC-UAEH.` line in `templates/base.html` |
| Link preview description | the `og:description` default in `templates/base.html` |
| Welcome text of the activation email | `templates/registration/activation_email.html` |
| Home page text | a blog post or flat page created from the admin |

The footer and email texts are translatable strings. If you change them, add the
new text to both catalogs (`locale/es` and `locale/en`) as described in section
4, and rebuild the offline manifest if you use it.

## 11. Check it works

Start the services and verify these workflows:

1. The home page, in **both** languages. `Accept-Language: en` and `es`.
2. `/problems/`, `/submissions/` and `/stats/language/` in several languages.
   These pages carry compressed blocks that contain translated strings, which is
   exactly where a stale offline manifest shows up.
3. Sign in, run a custom test, and check the output is not truncated.
4. Create a team, invite somebody, and register the team for a contest.
5. As a problem editor, open a problem's data page and upload a zip whose files
   are named `case1.in` / `case1.out`; confirm the cases fill in by themselves.
   Signed out, the same page must answer 403.
6. `/static../robots.txt` must answer 404.
7. Sign in as a second superuser and confirm the delete actions are **not**
   there, and that it cannot edit the owner account.

---

## After you change anything

| You changed | You must also |
| --- | --- |
| a template, stylesheet or script | regenerate the offline manifest, restart the web service |
| a translation catalog | `compilemessages -l es -l en`, restart the web service |
| a `.scss` file | `./make_style.sh`, then `collectstatic`, then the manifest |
| `nginx.conf` | `nginx -t`, then reload (restart only for a `listen` change) |
| a systemd unit | `daemon-reload`, then restart that service |
| Python code | reload the web service; restart Celery and the bridge if their code changed |

## Troubleshooting

**Pages answer 500 in some languages but not others.** Stale offline compression
manifest. Regenerate it and restart the web service.

**A page that used to be translated shows English again.** Somebody ran
`makemessages`. Restore the catalog from Git and add the new entries by hand.

**The problem data form answers HTTP 400 with many test cases.** Raise
`DATA_UPLOAD_MAX_NUMBER_FIELDS`. The form posts 14 fields per row, so 10240
fields is about 730 rows. There is a second ceiling in the formset itself, at
1001 rows, above which Django truncates silently, so staying under it is wise.

**Every large POST answers 500 and the application log is empty.** The request
never reached the application. Look at the front-end journal for permission
errors on the temporary directories.

**Problem data uploads, or just opening a problem's data page, answer 500.** Look
for `auth request unexpected status` in the Nginx journal: the internal
`/internal/problem-data-upload-gate` location is missing `client_max_body_size 0`,
or the application does not have that URL.

**Local test POSTs fail the CSRF check but browsers work.** Direct requests to the
origin lack the `X-Forwarded-Proto: https` header that the tunnel adds. See
[HTTPS](security/https.md).

**A standalone script that starts Django dies in `logging/config.py`.** Its log
handlers depend on the environment the service units provide. Set
`LOGGING_CONFIG = None` on `django.conf.settings` **before** `django.setup()`.
Wrapping the settings in your own module does not work: `dmoj/__init__.py`
imports Celery, and Celery reads settings while it is being imported.

**New `.pyc` files appear world-writable.** Set `PYTHONDONTWRITEBYTECODE=1` in
the service units, and pass `-B` when running `manage.py` manually. Check file
permissions separately on filesystems such as Windows drives mounted under WSL.

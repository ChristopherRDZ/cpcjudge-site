# Setting up this fork

This guide takes you from a working stock DMOJ to this fork, running with the
contest tooling, the team support and the hardening described in the README.

It does **not** replace the upstream installation instructions. Install a normal
DMOJ first, following <https://docs.dmoj.ca/>, and make sure you can sign in,
create a problem and get a submission judged. Everything below assumes that
already works, because if something breaks afterwards you want to know it was
one of these steps.

Paths used throughout: `/srv/dmoj/site` for the checkout, `/srv/dmoj/venv` for
the virtualenv, `/srv/dmoj/problems` for problem data, `judge.example.org` for
the host name. Substitute your own.

---

## 1. Get the code

```sh
git clone --recursive https://github.com/ChristopherRDZ/online-judge.git /srv/dmoj/site
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

Never commit `dmoj/local_settings.py`. It is already in `.gitignore`.

## 3. Database

```sh
/srv/dmoj/venv/bin/python manage.py migrate
```

This fork adds migrations `0150` through `0155`: announcements, clarifications,
scoreboard freeze, balloons and teams. None of them rewrites existing rows, but
take the dump from step 1 anyway.

## 4. Translations

The interface ships Spanish and English catalogs:

```sh
/srv/dmoj/venv/bin/python manage.py compilemessages -l es -l en
```

Compile **both**. Compiling only one leaves the other language showing raw
strings from the wrong language, and nothing warns you.

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

**Remember this rule, it is the single easiest way to take the site down:** any
change to a template, stylesheet, script or translation catalog invalidates the
manifest, and the affected pages answer HTTP 500 until you regenerate it and
restart the web service. It fails per page and per language, so a quick check of
the home page will not catch it.

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

Leaving it empty means nobody can delete from the admin, which is a valid choice
but surprises people. Set it before you hand out staff accounts.

## 7. Services

Bridge, event daemon, Celery and the web application all run separately. The
[deployment examples](../deploy/examples/README.md) contain one systemd unit per
service, each under its own unprivileged account, plus the shared namespace
restrictions.

Judges connect to the bridge; see
[`judge.example.yml`](../deploy/examples/judge.example.yml) and register each one
in the admin to get its key.

## 8. Front end

Use [`nginx.conf`](../deploy/examples/nginx.conf) as the starting point. Two
details in it are deliberate and easy to lose:

- **`client_max_body_size 16M` globally, 500M only on the problem data route.**
  Real problem archives reach about 100 MB, so the upload route needs the
  exception; nothing else does. Be aware that Nginx buffers the request body
  before Django checks the session, so that route accepts large uploads from
  anonymous clients too. Point `client_body_temp_path` somewhere with room, and
  think twice if it is a RAM-backed tmpfs.
- **`client_max_body_size 2M` on `/custom-test/run/`**, which is all a custom
  test ever needs.

After editing, `nginx -t` and then **reload**, not restart. A restart recreates
the temporary directories, and if their ownership comes out wrong every large
POST answers HTTP 500 without leaving a trace in the application log.

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

## 10. Check it works

Start the services and walk through this list. Each item has caught a real
regression at least once:

1. The home page, in **both** languages. `Accept-Language: en` and `es`.
2. `/problems/`, `/submissions/` and `/stats/language/` in several languages.
   These pages carry compressed blocks that contain translated strings, which is
   exactly where a stale offline manifest shows up.
3. Sign in, run a custom test, and check the output is not truncated.
4. Create a team, invite somebody, and register the team for a contest.
5. Upload a problem data zip whose files are named `case1.in` / `case1.out` and
   confirm the cases fill in by themselves.
6. Sign in as a second superuser and confirm the delete actions are **not**
   there, and that it cannot edit the owner account.

---

## After you change anything

| You changed | You must also |
| --- | --- |
| a template, stylesheet or script | regenerate the offline manifest, restart the web service |
| a translation catalog | `compilemessages -l es -l en`, restart the web service |
| a `.scss` file | `./make_style.sh`, then `collectstatic`, then the manifest |
| `nginx.conf` | `nginx -t`, then reload (not restart) |
| a systemd unit | `daemon-reload`, then restart that service |

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

**A standalone script that starts Django dies in `logging/config.py`.** Its log
handlers depend on the environment the service units provide. Set
`LOGGING_CONFIG = None` on `django.conf.settings` **before** `django.setup()`.
Wrapping the settings in your own module does not work: `dmoj/__init__.py`
imports Celery, and Celery reads settings while it is being imported.

**New `.pyc` files appear world-writable.** Set `PYTHONDONTWRITEBYTECODE=1` in
the service units, and pass `-B` when you run `manage.py` by hand. This bites
hardest on filesystems that do not carry Unix permissions, such as a Windows
drive mounted under WSL.

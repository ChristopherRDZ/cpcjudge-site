# Deployment examples

Configuration examples for running this fork. Every path, host name, port and
account in them is invented. Adapt them to your server; they are not drop-in
configuration.

They all assume the layout used in [the setup guide](../../docs/setup-guide.md):

| Path | Contents |
| --- | --- |
| `/srv/dmoj/site` | this checkout, read-only to the services |
| `/srv/dmoj/venv` | Python virtualenv |
| `/srv/dmoj/problems` | problem data, writable by the web and bridge |
| `/srv/dmoj/media` | uploaded images |
| `judge.example.org` | public host name |

## What is here

| File | Purpose |
| --- | --- |
| [`nginx.conf`](nginx.conf) | Front end: body limits, the upload check, static files, events |
| [`uwsgi.ini`](uwsgi.ini) | Application socket and request timeout |
| [`runtime-hardening.settings.py`](runtime-hardening.settings.py) | Offline compression, custom-test limits, error logging |
| [`judge.example.yml`](judge.example.yml) | Judge configuration skeleton |
| [`cleanup_custom_tests.py`](cleanup_custom_tests.py) | Scheduled custom-test cleanup |
| [`systemd/`](systemd) | One unit per service, each under its own account |

## Accounts

Each service runs as its own unprivileged user, so that a flaw in one of them
does not reach the others: `dmoj-uwsgi`, `dmoj-celery`, `dmoj-bridge`,
`dmoj-events`, `dmoj-proxy` and `dmoj-judge`. The units share the restrictions in
[`systemd/common-hardening.conf`](systemd/common-hardening.conf).

Make `/srv/dmoj/problems` group-writable by the accounts that need it (for
example `root:dmoj-uwsgi 2775`); keep everything else under `/srv/dmoj`
read-only to the services. The examples do not create accounts, directories or
ACLs: create them yourself, then check what each service can actually read and
write.

## Things that are easy to get wrong

- **Keep secrets out of Git:** `dmoj/local_settings.py`, `websocket/config.js`,
  judge keys, database and Redis passwords, tunnel credentials.
- **Offline compression.** With `COMPRESS_OFFLINE` on, regenerate the manifest and
  restart the web service after changing any template, stylesheet, script or
  translation catalog, or the affected pages answer 500. See
  [offline compression](../../docs/security/offline-compression.md).
- **The upload route.** Keep the `auth_request` check and its internal location
  exactly as in the example, with `client_max_body_size 0` there; buffer bodies on
  disk (`/var/lib/dmoj-nginx`, from `StateDirectory=` in the unit), not in RAM. See
  [Nginx](../../docs/security/nginx.md).
- **`location /static/` keeps its trailing slash.** Without it, `/static../X`
  serves files from the checkout.
- **Reload the front end** when only `nginx.conf` changed. Unit changes and
  `listen` changes need `daemon-reload` and a restart.
- **The cleanup tool speaks Spanish.** Its flags are `--ejecutar` (actually
  delete) and `--minutos` (grace period, 20 by default), and its messages are in
  Spanish. Without `--ejecutar` it only reports.

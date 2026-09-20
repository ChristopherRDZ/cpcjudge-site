# Sanitized deployment examples

These files describe the shape of the deployment this fork runs on. Every path,
host name, port and account in them is invented. They are examples to adapt, not
drop-in production configuration.

All of them assume the layout used throughout [the setup guide](../../docs/setup-guide.md):

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
| [`nginx.conf`](nginx.conf) | Front end, request body ceilings and the upload exception |
| [`uwsgi.ini`](uwsgi.ini) | Application socket and request timeout |
| [`runtime-hardening.settings.py`](runtime-hardening.settings.py) | Offline compression and custom-test ceilings |
| [`judge.example.yml`](judge.example.yml) | Judge configuration skeleton |
| [`cleanup_custom_tests.py`](cleanup_custom_tests.py) | Scheduled custom-test cleanup |
| [`systemd/`](systemd) | One unit per service, each under its own account |

## Accounts

Each service runs as its own unprivileged user, so that a flaw in one of them
does not reach the others: `dmoj-uwsgi`, `dmoj-celery`, `dmoj-bridge`,
`dmoj-events`, `dmoj-proxy` and `dmoj-judge`. The units apply the same set of
namespace restrictions, collected in
[`systemd/common-hardening.conf`](systemd/common-hardening.conf).

`/srv/dmoj/problems` is group-writable by the accounts that need it
(`root:dmoj-uwsgi 2775` in this fork's deployment); everything else under
`/srv/dmoj` is read-only to the services.

## Things that are easy to get wrong

- **Keep secrets out of Git.** `dmoj/local_settings.py`, `websocket/config.js`,
  judge keys, database and Redis passwords, tunnel credentials.
- **Offline compression.** If `COMPRESS_OFFLINE` is on, regenerate the manifest
  and restart the web service after touching any template, stylesheet, script or
  translation catalog. A stale manifest answers HTTP 500 on the affected pages.
  See [offline compression](../../docs/security/offline-compression.md).
- **Request body ceilings.** The 500M exception on the problem data route exists
  because real archives reach ~100 MB. Nginx buffers the body before Django
  checks the session, so that route accepts large anonymous uploads too, and the
  buffer lands wherever `client_body_temp_path` points. On a host where that
  path is a RAM-backed tmpfs, size it deliberately.
- **Reload, do not restart, the front end** when only `nginx.conf` changed. A
  restart recreates the temporary directories, and getting their ownership wrong
  produces a silent HTTP 500 on every large POST that never reaches the
  application log.
- **The cleanup tool speaks Spanish.** Its flags are `--ejecutar` and
  `--minutos`, and it prints in Spanish, because it is published exactly as it
  runs in production except for the paths. Without `--ejecutar` it only reports.

The uWSGI and systemd examples illustrate the restrictions this fork applies;
they do not create the accounts, directories or ACLs a deployment needs. Verify
the effective access of every runtime role before trusting them.

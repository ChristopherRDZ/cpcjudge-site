# Service isolation

Run each part of the site under its own unprivileged, non-login account, so that
a flaw in one of them does not reach the others. The
[systemd examples](../../deploy/examples/systemd) do this with one unit per
service and a shared set of restrictions in
[`common-hardening.conf`](../../deploy/examples/systemd/common-hardening.conf).

| Service | Account | Reads | Writes |
| --- | --- | --- | --- |
| Web (uWSGI) | `dmoj-uwsgi` | code, virtualenv, its settings | problem data, media, its logs and socket |
| Celery | `dmoj-celery` | code, virtualenv, its settings, problem data | its logs |
| Bridge | `dmoj-bridge` | code, virtualenv, its settings, problem data | its logs |
| Events | `dmoj-events` | event daemon and its configuration | nothing |
| Nginx | `dmoj-proxy` | static files, media, the web socket | its temporary directories |
| Judges | `dmoj-judge` | judge environment, its key, problem data | private runtime state |

The units use no capabilities, `NoNewPrivileges`, private `/tmp` and devices,
read-only code and virtualenv, and hide everything a service does not need:
home directories, other services' credentials and, on WSL, Windows files and
interoperability.

The web socket is group-restricted (mode 660); Nginx needs to be in its group,
without access to the application's settings.

## Only one process manager

Start each service from exactly one place. If an old Supervisor or init script
can still start the web application, a second copy will fight over the socket
and, when it exits, delete it, leaving Nginx with 502 errors.

## Stopping the bridge and judges

Stop new work first, then wait for the queue, the judges and Celery to go idle.
The bridge can take a while to stop while judges are connected.

## Judges

Judges accept their own restrictions (`PrivateDevices`, `ProtectKernelTunables`,
`ProtectKernelModules`, `ProtectControlGroups`, `RestrictSUIDSGID`,
`RestrictAddressFamilies`). Do **not** add `LockPersonality`: the executors call
`personality(ADDR_NO_RANDOMIZE)` for consistent memory accounting, and blocking
it changes results silently. Do not assume every web restriction fits a judge.

## WSL notes

- Clearing `WSL_INTEROP` is not enough: also make `/init` and the Windows mounts
  inaccessible to the services.
- `/etc/resolv.conf` usually points into `/mnt/wsl`. Hiding `/mnt/wsl` leaves the
  service without DNS, with no error at startup; registration emails then fail.
  If you hide it, expose the resolver file again, and test name resolution as the
  service user inside its namespace.
- Keep `django.request` errors logged to the journal, not only by email: if email
  is what broke, the email report is lost too. See the
  [settings fragment](../../deploy/examples/runtime-hardening.settings.py).

## Temporary directories

Check that Nginx can write its temporary directories after isolating it. If it
cannot, large POST requests fail with 500 and never reach the application log.
A GET that works proves nothing about POST buffering.

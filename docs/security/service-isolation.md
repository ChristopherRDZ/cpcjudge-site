# Service isolation

The deployment moved judges to systemd on 2026-09-09 and the web application,
Celery, judge bridge, event daemon and dedicated Nginx instance on 2026-09-10.
Each role uses a dedicated non-login account. The web and proxy no longer run
as root. Judges start automatically with the host service manager.

This document describes the controls to reproduce. Actual unit files, numeric
account IDs, host mount layouts and recovery inventories remain private.

| Role | Read access | Required writable state |
| --- | --- | --- |
| Web | Application, virtual environment, its private settings | Problem data, uploaded media, application logs and socket |
| Celery | Application, virtual environment, its private settings and required problem data | Its own logs/runtime |
| Bridge | Application, virtual environment, its private settings and problem data | Its own logs/runtime |
| Events | Event daemon code, Node dependencies and its configuration | Private temporary/runtime state |
| Proxy | Public static/media files and application socket | Its own runtime/cache |
| Judges | Judge environment, assigned configuration and problem data | Private execution/runtime state |

The application roles use empty capability sets, `NoNewPrivileges`, read-only
code and virtual environments, private temporary directories and devices, and
mount restrictions around the required paths. Unneeded home directories,
Windows files, WSL interoperability, service-manager sockets and other roles'
credentials are hidden. Judge units have their own tested restrictions; do not
assume every web hardening directive can be applied unchanged to a judge sandbox.

On WSL, clearing `WSL_INTEROP` alone is insufficient. The native interoperability
entry point and host-mounted paths must also be inaccessible to the service.
Filesystem protection and namespace restrictions complement one another.

Uploaded media is outside the Python source package. Service-specific log
directories keep the source tree read-only. The web socket is group-restricted
with mode 660, and its directory is recreated at boot. The proxy must belong to
the socket's permitted group without gaining access to application secrets.

## Startup and recovery

One process manager must own each service. On 2026-09-14 obsolete Supervisor
definitions were removed after preserving private recovery material, and its
daemon was stopped and disabled. Starting an old web definition can contend
for, and later remove, the active Unix socket. Use the current systemd units.

Before stopping the bridge or judges, pause incoming evaluation work and wait
for submission queues, active judge workers and Celery work to finish. A bridge
can take time to stop while judges remain connected. Recovery must preserve
uploads created since migration and reject conflicting or unknown file changes.

Validation covered role identities, access boundaries, normal HTTP/resources,
event endpoints and judge connectivity. Later host-startup checks confirmed
automatic startup; restarting the web service recovered a socket removed by an
obsolete Supervisor launch. These observations do not certify every sandbox
boundary or every supported language against hostile programs.

## Additional judge restrictions — 2026-09-13

The judge unit now also uses `PrivateDevices`, `ProtectKernelTunables`,
`ProtectKernelModules`, `ProtectControlGroups`, `RestrictSUIDSGID` and
`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`. Language self-tests,
isolated evaluations and later user-reported submissions passed.

Do not add `LockPersonality` to this judge configuration. Its executors use
`personality(ADDR_NO_RANDOMIZE)` for consistent memory accounting; blocking the
call silently changes that behavior even when ordinary verdict tests pass.
`SystemCallFilter` and `MemoryDenyWriteExecute` were not added.

## DNS and temporary storage — 2026-09-13/14

On WSL, `/etc/resolv.conf` can point into `/mnt/wsl`. Masking that directory
breaks DNS inside a service without preventing its startup or ordinary GETs.
The web/Celery namespaces now expose only the resolver file within a private
temporary mount. Other host and interoperability restrictions remain in place.
Adapt the mapping to the actual resolver target; do not copy a host-specific
drop-in blindly. Verify DNS and TLS connectivity as the service user inside its
namespace after every mount change, including after WSL restarts.

Keep request-error logging available locally as well as through email. If DNS
breaks both activation email and email-only error reporting, registration may
return 500 without an accessible diagnostic. See the
[configuration fragment](../../deploy/examples/runtime-hardening.settings.py).

Validate ownership of the proxy's writable body/response temporary paths after
isolating its service. The deployment corrected paths owned by a different UID,
which prevented larger POST requests from being buffered to disk. A successful
small login-form GET does not validate POST body buffering, email delivery or
an actual registration.

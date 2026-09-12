# Security maintenance record

These documents describe application changes and deployment controls applied
through 2026-09-11. Examples use generic paths and hostnames; production settings,
credentials, data, logs and detailed recovery inventories remain private.

- [Custom test lifecycle](custom-tests.md): read-only polling, signed ownership
  and bounded cleanup.
- [Personal test privacy](custom-test-privacy.md): histories, API, statistics and
  direct source access.
- [Service isolation](service-isolation.md): dedicated identities, systemd,
  read-only code, judge startup and WSL boundaries.
- [Private files and backups](private-files-and-backups.md): permissions,
  configuration copies, backup coverage and publication boundaries.
- [Redis](redis.md): authenticated application clients and persisted ACLs.
- [Nginx](nginx.md): no directory indexes or public event-publishing route,
  private origin listener and custom test request-size limit.
- [HTTPS](https.md): secure cookies and HSTS at the edge.

Documented checks are scoped observations, not a claim that the system has no
vulnerabilities. Code publication does not install dependencies, change host
configuration, migrate data or restart services. Deployment examples require
adaptation and validation in a separate environment.

Django 5.2 preparation is maintained on the separate `cpc-django52-candidate`
branch. It is not the deployed runtime represented by `cpc-production`.

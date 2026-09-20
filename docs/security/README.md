# Security maintenance record

These documents describe application changes and deployment controls applied
through 2026-09-19. Examples use generic paths and hostnames; production settings,
credentials, data, logs and detailed recovery inventories remain private.

- [Custom test lifecycle](custom-tests.md): read-only polling, signed ownership
  and bounded cleanup, per-user admission limits, scheduled retention and remaining
  concurrency limits.
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
- [Offline compression](offline-compression.md): deterministic builds for a
  read-only static tree and required template-context coverage.
- [Owner accounts and impersonation](../setup-guide.md#6-the-owner-account):
  protected administrative privileges and owner-only deletion; the
  [private settings section](../setup-guide.md#2-private-settings) configures
  restricted impersonation with audit logging.
- [Scheduled cleanup](../setup-guide.md#9-custom-test-cleanup): maintenance timer
  for finished custom tests, with ownership checks and a grace period.

Documented checks are scoped observations, not a claim that the system has no
vulnerabilities. Code publication does not install dependencies, change host
configuration, migrate data or restart services. Deployment examples require
adaptation and validation in a separate environment.

The [Django 5.2 upgrade](../django52/README.md) is deployed and included in
`cpc-production`. Its record distinguishes historical laboratory tests from
postdeployment read-only checks and documents configuration/build boundaries.

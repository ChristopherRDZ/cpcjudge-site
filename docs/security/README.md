# Security guides

Application protections are part of the code. Deployment controls have to be set
up on each server; the [examples](../../deploy/examples/README.md) show one way.

| Guide | What it covers |
| --- | --- |
| [Nginx](nginx.md) | private origin, headers, body limits, the upload check, static files, the event daemon |
| [HTTPS](https.md) | secure cookies, HSTS, how the HTTPS scheme reaches Django |
| [Redis](redis.md) | password-protected Redis for sessions, cache and Celery |
| [Service isolation](service-isolation.md) | one account per service, systemd restrictions, judges, WSL |
| [Private files and backups](private-files-and-backups.md) | settings, permissions, backups |
| [Offline compression](offline-compression.md) | read-only static files and when to rebuild |
| [Custom tests](custom-tests.md) | ownership, per-user limits, cleanup |
| [Custom test privacy](custom-test-privacy.md) | what custom tests are hidden from |
| [Owner accounts](../setup-guide.md#6-the-owner-account) | who can delete from the admin, impersonation |

## Known limitations

- The Content Security Policy does not restrict JavaScript. Problem statements,
  contest descriptions, blog posts and the site announcement accept HTML, so
  anyone allowed to edit them can run scripts in other users' browsers. The owner
  lock stops accidental deletions by other administrators; it does not protect
  against an administrator acting in bad faith.
- The custom-test in-flight limit is not atomic under simultaneous requests.
- Email addresses are not unique in the database. Registration and the email
  change form check them, but the admin does not. Login by email tries the
  password against each account with that address (up to five).

These guides describe recommended settings. They are not a guarantee that a
deployment has no vulnerabilities.

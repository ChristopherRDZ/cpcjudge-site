# Sanitized deployment examples

These files document the shape of a deployment without exposing production
paths or credentials. They are examples, not drop-in production configuration.

- Keep `dmoj/local_settings.py`, `websocket/config.js`, tunnel credentials,
  judge keys, database passwords, and email credentials outside Git.
- Run the application under a dedicated unprivileged account.
- Restrict Unix sockets to the application and proxy groups.
- Keep database and Redis listeners on loopback or a protected private network.
- Test all deployment changes outside the live checkout before rollout.

See the maintenance notes on [service isolation](../../docs/security/service-isolation.md)
and [private files and backups](../../docs/security/private-files-and-backups.md).
The uWSGI example illustrates a restricted application socket; it does not
provide the accounts, ACLs, boot-time runtime directory or complete systemd
namespace setup needed for a deployment. Keep those host-specific definitions
private and validate the effective access of every runtime role.

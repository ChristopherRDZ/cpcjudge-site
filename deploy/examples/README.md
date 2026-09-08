# Sanitized deployment examples

These files document the shape of a deployment without exposing production
paths or credentials. They are examples, not drop-in production configuration.

- Keep `dmoj/local_settings.py`, `websocket/config.js`, tunnel credentials,
  judge keys, database passwords, and email credentials outside Git.
- Run the application under a dedicated unprivileged account.
- Restrict Unix sockets to the application and proxy groups.
- Keep database and Redis listeners on loopback or a protected private network.
- Test all deployment changes outside the live checkout before rollout.

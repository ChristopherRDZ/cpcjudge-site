# Redis authentication

DMOJ keeps sessions in the cache (`cached_db`). A Redis without a password on
loopback still lets any local process read those sessions and take over
accounts, administrators included. Binding to loopback is not enough on a shared
host.

## Configuration

1. Create a dedicated ACL user for the site, with a strong password.
2. Put the cache, Celery broker and Celery result URLs with that user in
   `local_settings.py`. The [example](../../dmoj/local_settings.example.py) has
   no fallback to an anonymous connection on purpose.
3. Check that the site, Celery and the bridge work with the new credentials.
4. Disable the `default` user, and persist the ACL configuration so it survives
   a Redis restart.

URL-encode special characters in the password when building a connection URL.
Do not pass the password on a command line, where other processes can read it;
for `redis-cli`, use the `REDISCLI_AUTH` environment variable.

## Rolling back

If you restore an older `local_settings.py` without the credentials, the site
loses its cache. Re-enable the `default` user at the same time, or restore both
sides together.

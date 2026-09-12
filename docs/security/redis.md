# Redis authentication

Redis authentication was enabled on 2026-09-11 using a dedicated ACL user and
disabling the anonymous/default user after application clients were migrated.
It was subsequently confirmed to survive a host/service restart.

The deployment uses Django's `cached_db` session engine. A local Redis listener
without authentication lets other local processes inspect cached sessions;
loopback binding alone does not isolate applications sharing a host.

The public settings example therefore requires explicit private environment
values for cache, Celery broker and result-backend URLs. It has no fallback to
an unauthenticated Redis connection. Provision the ACL user and password outside
Git. Supply credentials through a protected mechanism, not a command argument,
terminal transcript or copied production URL. Encode URL-special characters
when assembling a connection URL.

## Migration order used

1. Identify all Redis clients and prepare a private configuration/ACL backup.
2. Create the dedicated user while the previous client identity still works.
3. Verify cache and broker operations under the actual application identities.
4. Update private connection settings while preserving file ACLs, then reload
   only the affected application clients in a controlled sequence.
5. Disable anonymous/default access once authenticated clients are healthy.
6. Persist the ACL configuration and test persistence in a disposable instance.

The verification covered anonymous denial, authenticated connections, cache and
Celery health, preserved sessions, application responses and judge connections.
Do not flush the live cache or queues as an authentication check.

Restoring old application settings without coordinating Redis authentication
can break the application. Recovery must account for both sides of the change.
The deployed ACL user still has broad command permissions; per-role or
per-command restrictions require a separate compatibility review.

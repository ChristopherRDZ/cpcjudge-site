# Safe custom test lifecycle

Implemented in the deployed application on 2026-09-08.

The custom test endpoint now treats result polling as a read-only operation.
It verifies the submitting user, the temporary problem and a signed identity
before returning a result. Normal submissions, another user's submissions and
unverified legacy temporary problems are rejected. Repeated GET requests do not
delete problems, submissions or files.

New temporary problems carry a Django-signed marker binding the problem ID,
problem code and owner. The signing salt is a public purpose identifier, not a
credential; the Django signing key remains in private configuration.

A valid POST may clean up at most ten of its owner's verified temporary tests
older than five minutes. Active jobs and problems associated with contests,
authors, curators or additional submissions are preserved. Directory checks
reject symlinks and paths outside the configured problem root. File removal is
deferred until the database transaction commits.

Creation uses exclusive directories and a transaction for new database rows.
Uncertain bridge responses preserve the test data because the judge may already
have accepted the job. Client errors no longer include internal exception text.

## Validation and limits

The deployment candidate passed thirteen groups of isolated checks using real
Django models/signals, synthetic files and an in-memory database, including
ownership, repeated polling, collisions, rollback and preservation of ordinary
problems. The lifecycle implementation was subsequently extended by the
admission controls below. The lifecycle checks are historical deployment
evidence, not a new test run during source publication.

Legacy unverified tests are retained. The most recent test can remain stored if
its owner never submits another one. Background retention is still not implemented.
The proxy body-size limit is a separate deployment control.

## Per-user admission controls — 2026-09-14

The deployed POST handler checks admission before reading JSON, cleaning old
tests or creating files. A refusal returns HTTP 429 with an `error` message
displayed by the existing custom test page. GET polling is unchanged.

| Setting | Default | Meaning |
| --- | --- | --- |
| `CPC_CUSTOM_TEST_MAX_IN_FLIGHT` | 2 | Refuse when this many personal tests are in QU/P/G within the last ten minutes |
| `CPC_CUSTOM_TEST_MAX_PER_MINUTE` | 12 | Shared-cache fixed window per user |
| `CPC_CUSTOM_TEST_MAX_PER_HOUR` | 200 | Shared-cache fixed window per user |

Positive integers enable each ceiling; zero or negative integers disable it.
Other types, including booleans, fall back to defaults. Cache counters have a
finite expiry and are shared between web workers. If the cache is unavailable,
time windows fail open with a warning and the database count still runs.

The original isolated limiter checks covered 27 cases. Source publication
preserves those checks in [tests/custom_tests](../../tests/custom_tests/README.md).
Historical deployment checks covered HTTP 429 handling in code, ordinary GET
responses across languages, the proxy body-size limit and a separate uWSGI
timeout experiment. Authenticated browser interaction was not repeated during
publication.

The in-flight count is an admission check, not an atomic reservation. Concurrent
requests can pass before either creates its submission. An isolated review
reproduced three admissions against a ceiling of two. The shared rate windows
remain effective with working Redis, but a strict concurrent limit needs a
separate change coordinating admission and creation. Fixed-window boundaries,
the ten-minute age cutoff and per-user scope also matter when sizing capacity.

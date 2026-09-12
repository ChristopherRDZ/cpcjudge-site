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
problems. The production source published here matches that candidate byte for
byte. The checks are historical deployment evidence, not a new test run during
source publication.

Legacy unverified tests are retained. The most recent test can remain stored if
its owner never submits another one. This change does not implement background
retention, per-user rate limits or concurrent-job limits. The proxy body-size
limit is a separate deployment control.

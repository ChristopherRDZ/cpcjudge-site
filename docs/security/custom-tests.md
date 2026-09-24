# Custom tests

The custom test page (`/custom-test/`) runs a user's code with their own input.
Each run creates a hidden temporary problem (`ct_` plus 12 hex digits), its data
directory and a submission.

## Ownership and polling

- The temporary problem carries a Django-signed marker that binds its ID, code
  and owner. Results are returned only when that signature matches the user
  asking.
- Polling for a result is read-only: repeated requests never delete anything.
- Directory handling rejects symbolic links and anything outside the configured
  problem root, and files are removed only after the database transaction
  commits.

## Limits per user

A refused run answers **HTTP 429** with a message the page displays.

| Setting | Default | Meaning |
| --- | --- | --- |
| `CPC_CUSTOM_TEST_MAX_IN_FLIGHT` | 2 | runs still queued or being judged (within the last 10 minutes) |
| `CPC_CUSTOM_TEST_MAX_PER_MINUTE` | 12 | runs per minute |
| `CPC_CUSTOM_TEST_MAX_PER_HOUR` | 200 | runs per hour |
| `CPC_CUSTOM_TEST_OUTPUT_PREFIX` | 65536 | bytes of output returned to the page (1 KB to 1 MB) |

Zero or a negative value disables a limit. The per-minute and per-hour windows
are counted in the shared cache; if the cache is down they are skipped with a
warning and the in-flight check still applies.

The in-flight check is not atomic: several simultaneous requests can pass it
before any of them creates its submission. The rate windows still apply.

Keep the Nginx body limit on `/custom-test/run/` (2M in the example) as well.

## Cleanup

Starting a new run removes up to ten of the same user's finished tests older than
five minutes. Tests of users who never come back stay behind, so install the
[scheduled cleanup](../setup-guide.md#9-custom-test-cleanup), which checks every
account. Both skip tests still being judged, tests whose signature does not
match, and anything that gained authors, contests or other submissions.

See also [privacy of custom tests](custom-test-privacy.md).

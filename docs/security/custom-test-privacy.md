# Privacy of personal test submissions

Implemented in the deployed application on 2026-09-08, after the
[custom test lifecycle correction](custom-tests.md).

Personal tests are excluded from normal submission histories, per-user and
per-problem listings, AJAX rows, public API lists and global submission result
statistics. This exclusion also applies to their owner and administrators in
the ordinary histories; results remain available through the custom test tool.
The statistics cache uses a new key so an older aggregate is not reused.

Direct access to a personal test's source is restricted to its owner or the
explicit `judge.view_all_submission` permission. Normal problem solution-sharing
rules do not grant access to personal tests. The existing detail views and API
reuse this access check.

The reserved `ct_` prefix is used for exclusion/access policy here. It is not
authorization to delete a problem. Deletion requires the signed identity and
additional checks described in the lifecycle document.

## Validation and limits

Eight groups of checks passed with a disposable MariaDB database, real models,
templates and synthetic accounts with different permissions. A separate
read-only comparison checked normal application listings. The three source
files published here match the final deployment candidates byte for byte.

This policy does not conceal data from an administrator holding the explicit
permission, and hiding a test from listings does not remove its stored data.

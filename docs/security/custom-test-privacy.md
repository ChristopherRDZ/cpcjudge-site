# Privacy of custom tests

Custom tests are personal. They are excluded from:

- the global, per-user and per-problem submission lists and their live updates;
- the submissions API;
- submission statistics.

This also applies to their owner and to administrators in those lists; results
are seen through the custom test page.

The source of a custom test can only be opened by its owner or by an account
with the `judge.view_all_submission` permission. The problem's normal
solution-sharing rules never make a custom test public.

The `ct_` prefix decides what is hidden. It is never enough to delete anything:
deletion also requires the signed ownership check described in
[custom tests](custom-tests.md).

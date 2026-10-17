# Email limit tests

Run from a clean checkout with the project's Python dependencies and GNU gettext
(`msgfmt`) installed:

```sh
python tests/email_limits/run.py
```

The runner builds a temporary copy of the code without your local settings, uses
an in-memory SQLite database, cache and mail outbox, and runs
`test_email_limits.py`. It never reads your private settings, connects to a live
database or Redis, or sends an email. It prints the temporary directory so you can
inspect it.

The tests cover:

- two password reset emails per address in 24 hours, case-insensitive, with the
  same answers for an address that has no account;
- one email change request per user in 24 hours, which needs room for both of its
  emails;
- the daily limit, counted per UTC day, and the reserve that registrations and
  error reports cannot use;
- a failed send: password reset gives the attempt back without saying so, email
  change says so, and registration creates no account, so the same address can
  register again;
- the per-IP password reset limit that DMOJ already had;
- error report emails to `ADMINS`, capped per day;
- the messages in English and Spanish.

Never run database tests with production settings.

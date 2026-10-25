# Email: limits and failures

Transactional email providers accept only so many messages per day, or charge per
message, and refuse or bill the rest. Without limits, one person repeating a
password reset, or a script registering many addresses, can use up the quota and
leave every other user without their emails. This fork counts every email the site
sends and refuses with a clear message before the provider does.

## What is limited

| Setting | Default | Counts |
| --- | --- | --- |
| `DMOJ_PASSWORD_RESET_DAILY_LIMIT` | 2 | Password reset emails to one address in the 24 hours after the first |
| `DMOJ_EMAIL_CHANGE_DAILY_LIMIT` | 1 | Email change requests from one account in 24 hours (each sends two emails) |
| `DMOJ_EMAIL_DAILY_LIMIT` | 90 | Every email of the site per UTC day |
| `DMOJ_EMAIL_DAILY_RESERVE` | 20 | Part of the daily limit that registrations and error reports may not use |
| `DMOJ_EMAIL_ERROR_DAILY_LIMIT` | 10 | Error reports to `ADMINS` per UTC day, on top of `DMOJ_EMAIL_THROTTLING` |

[Emails to users](../mass-email.md), written in the admin for many accounts, are not
counted here and never use this SMTP: they go through Resend Broadcasts, with their
own quota and unsubscribe handling.

The day is counted in UTC because that is when most providers reset their quotas.
**Set `DMOJ_EMAIL_DAILY_LIMIT` below your provider's daily quota.** If your provider
has no daily quota and charges per message instead, use it as a spending cap.

The reserve keeps existing users able to reset their password on a day of many
registrations: registrations stop when the day's count plus the reserve reaches the
limit, while password resets and email changes can use the whole limit.

DMOJ's own per-IP limits stay in place: `DMOJ_PASSWORD_RESET_LIMIT_COUNT` and
`DMOJ_EMAIL_CHANGE_LIMIT_COUNT` requests per `..._LIMIT_WINDOW` minutes. They count
per visitor only if Django sees each visitor's address. Behind a tunnel or a proxy
on the same machine it sees loopback for everybody, and each of those limits
becomes a single counter for the whole site; see [Nginx](nginx.md#visitor-address).
A classroom or a contest hall behind one school network shares one address, so
raise the password reset count if many people may reset at once.

## What users see

- **Password reset:** after the limit for an address, a message saying the maximum
  was already sent and to check the spam folder. The same answers are given whether
  or not an account uses that address, so the page does not reveal who is
  registered.
- **Email change:** after one request in 24 hours, a message saying so.
- **Registration:** if the day's limit is reached, or the activation email cannot be
  sent, the account is **not created** and the page says so. The activation email is
  sent inside the transaction that creates the account, so a failure never leaves an
  inactive account with no email whose address can no longer be used to register.
- **Daily limit reached:** password reset and email change say the site reached its
  email limit for today.

All of these answer with HTTP 429, or 503 when sending failed, and are translated.

## When an email does not arrive

Django hides a failed password reset email on purpose: the page still says the
email was sent, so it does not tell anybody which addresses have accounts. The
failure is logged, and the attempt is given back so it does not count against the
user. Registration and email change do report a failed send.

Look in the web service journal:

```sh
journalctl -u dmoj-web | grep -iE 'judge.mail|Failed to send|smtp'
```

and check the provider: credentials, a verified sending domain and, with prepaid
providers, remaining credit.

## Sender addresses

Set `DEFAULT_FROM_EMAIL` and `SERVER_EMAIL` to explicit addresses on your own domain
instead of `EMAIL_HOST_USER`. With many providers the SMTP user is not an address
(for example `apikey` or `emailapikey`), and copying it into the sender makes every
send fail, so registration would stop working the moment the provider changes.

Turn off the provider's click tracking if it has one. It rewrites the activation and
password reset links through the provider's domain, which looks suspicious to
users and to spam filters.

## Known limitations

- The counters are fixed windows kept in the cache. Two requests at the same moment
  can both pass the last free slot, so the daily limit can be exceeded by a message
  or two; leave some room below the provider's quota.
- If the cache is cleared, the counters start again.
- Error reports are capped only when the `mail_admins` handler in your logging
  configuration is `dmoj.throttle_mail.ThrottledEmailHandler`.

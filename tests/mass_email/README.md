# Tests of emails to users

Run from a clean checkout with the project's Python dependencies and GNU gettext
(`msgfmt`) installed:

```sh
python tests/mass_email/run.py
```

The runner builds a temporary copy of the code without your local settings, uses
an in-memory SQLite database and cache, and runs every `test_*.py` here with the
interface in Spanish, then the email footer tests again in English. Resend is
replaced by `mass_email_fake.py`, an in-memory imitation of its API (contacts,
segments, broadcasts, cursor pagination and injected failures), and email goes
to memory. It never reads your private settings, connects to a live database,
Redis or Resend, or sends an email. It prints the temporary directory so you can
inspect it.

The tests cover:

- who receives an email: accounts with a confirmed email only, the filters
  (contests, virtual participations, organizations, groups, recent activity,
  sign-up dates, solved problems, rating), people always or never included, and
  the three audiences (everyone, the filters, people chosen by name), described
  in words;
- how an email looks: the Markdown and HTML that survive cleaning, absolute
  links and images, the plain text version and the `{{usuario}}` placeholder;
- the Resend client: its request limit, retries on 429 and 5xx, and pagination;
- the sending task: the segment and its contacts, people who unsubscribed left
  out, scheduled emails, retries that never send twice, and the first error
  reported when nobody could be added;
- the admin: who sees which emails (`send_mass_email` for one's own,
  `edit_all_mass_email` for all), the live counter, the send page that asks to
  type the number of recipients, delivery figures, the unsubscribed list with
  masked addresses, and the page that follows a task;
- the footer of every email the site sends: the line saying the club is
  independent, and the privacy notice link in emails to users;
- migration 0158 and the `send_mass_email` permission.

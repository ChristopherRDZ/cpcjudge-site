# Optional Django 5.2 compatibility tests

`compat_tests.py` exercises registration, login, password reset, two-factor
authentication, encrypted fields, time zones, the calendar, ICPC participation,
custom test privacy, static and media handling, SQL listings and the API. Its
users, passwords, judge key and events are made up.

It is kept outside the normal test discovery because it needs a prepared
environment. Only load it through Django's test runner in a disposable setup:

- a separate copy of the code with the pinned dependencies;
- test settings with a throwaway MariaDB database and a local-memory cache (the
  tests clear the cache);
- empty, writable problem, media and static directories, with static files
  already built;
- the local-memory email backend and no external integrations.

Never load it with production settings: database transactions do not undo files,
cache contents or anything sent outside.

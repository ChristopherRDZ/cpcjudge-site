# Interactive problem tests

Run from a clean checkout with the project's Python dependencies and GNU gettext
(`msgfmt`) installed:

```sh
python tests/interactive_problems/run.py
```

The runner builds a temporary copy of the code without your local settings, uses
an in-memory SQLite database and cache and a temporary problem data directory,
disables events and compression, and runs `test_interactive_problems.py`. It
never reads your private settings, connects to a live database or Redis, or
contacts a judge. It prints the temporary directory so you can inspect it.

The 23 tests post the problem data form as the browser does and cover:

- the `interactive:` block written to `init.yml` for C++, Python and testlib
  interactors, with and without contestant feedback;
- testlib detection, including a byte order mark and Windows line endings, and
  the bundled `testlib.h` copied next to the interactor;
- rejected uploads: wrong extension, no extension, over 1 MB, and the same name as
  the generator;
- saving the page again, clearing the interactor, replacing it under the same or
  another name, and renaming the problem;
- replacing and clearing the data archive (the upstream bug that lost archives);
- inserts from code that predates the new columns;
- the notice and guide link on the data page, the Spanish interface, the guide,
  the example download, and access for anonymous users, ordinary accounts and
  manually managed problems.

Grading is up to the judge and is not exercised here. To check your judges, use
the guide's example on a hidden problem: `solucion.cpp` must be accepted on all
four cases.

Never run database tests with production settings.

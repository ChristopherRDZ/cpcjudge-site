# Statement tests

Run from a clean checkout with the project's Python dependencies and GNU gettext
(`msgfmt`) installed:

```sh
python tests/statements/run.py
```

The runner builds a temporary copy of the code without your local settings, uses
an in-memory SQLite database and cache, and runs every `test_*.py` here once with
the interface in Spanish and once in English. pdfoid is replaced by a stub that
records what it receives, so no browser or PDF service is needed. It never reads
your private settings or connects to a live database, Redis or pdfoid. It prints
the temporary directory so you can inspect it.

The tests cover:

- the sample table (`||input`, `||output`, `||description`, `||end`): rows,
  headers in each language, verbatim input and output, Markdown descriptions,
  text left alone without a closing `||end`, and the same table with and without
  sanitizing;
- its style: each column as wide as its own samples and room for the copy icon;
- `$...$` as inline math next to `~...~`, prices that stay text, escaped dollar
  signs and code spans;
- statements written the usual way, which must render exactly as before, and
  comments or blog posts, which keep the original syntax;
- problem PDFs: the title, the cache, a failed render that leaves no empty file
  behind, private problems and the PDF turned off;
- the contest PDF: who may download it and when, the order and language of the
  statements, the cache keyed by content, one render at a time with a page that
  retries by itself, a failed render, and the button on the contest page and the
  problem list. It is never offered for the hidden contest of a duel; that test
  uses the helpers in `tests/duels/arena_testutil.py`.

Never run database tests with production settings.

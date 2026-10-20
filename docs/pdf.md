# Problem and contest PDFs

DMOJ can turn a statement into a PDF through
[pdfoid](https://github.com/DMOJ/pdfoid), a small service that prints HTML with
headless Chrome. This fork makes those PDFs reliable and adds one PDF with every
statement of a contest. Both stay off until you configure pdfoid: without it there
is no PDF link, no download button and `/problem/<code>/pdf` answers 404.

## What changes

**Problem PDFs.** Upstream opened the cached file before rendering, so a failed
render (pdfoid down, MathJax not loading in time) left an empty PDF that was
served from then on, and a request arriving meanwhile could read a half-written
file. Now the PDF is rendered first, written to a temporary file in the cache
folder and moved into place at once. `problem/raw.html` carries a `<title>`, so
Chrome names the PDF and pdfoid does not need `exiftool`.

**Contest PDF.** `/contest/<key>/pdf` returns one file with a cover listing the
problems and each statement on its own page, with its label and limits, in the
reader's language when a problem has a translation. It is meant to keep reading
if the connection drops during a contest. A *Download every statement (PDF)*
button appears at the bottom of the contest page and of the contest's problem
list for:

- whoever is inside the contest once it started: live, virtual or spectating, the
  same people who already see its problems;
- its authors, curators and testers, and anyone who can edit every contest, at
  any time, to prepare it before the contest.

It is never offered for the hidden contests behind [duels](duels.md).

The contest PDF is cached under a fingerprint of its HTML: editing a statement, a
limit, a translation or the problem set produces a new file and removes the old
one for that contest and language. Only one request renders it at a time, with a
time limit of 100 seconds, below the 120-second uWSGI `harakiri` of the
[example](../deploy/examples/uwsgi.ini). Other requests meanwhile get a page that
says the PDF is being prepared and retries by itself every 5 seconds (HTTP 202
with `Refresh`). If rendering fails, the request that tried gets a 503 page,
nothing is cached and the next request tries again.

## Setting up pdfoid

1. **Install pdfoid** in its own directory and virtualenv, with Chrome and a
   matching ChromeDriver. Run it as its own unprivileged account, listening only on
   loopback. [`deploy/examples/systemd/dmoj-pdfoid.service`](../deploy/examples/systemd/dmoj-pdfoid.service)
   is an example unit.
2. **Apply the patch** in
   [`deploy/examples/pdfoid/`](../deploy/examples/pdfoid/README.md). Unpatched
   pdfoid leaves a ChromeDriver process running after every PDF and waits up to
   300 seconds for a page to load, longer than uWSGI waits for the request.
3. **Create the cache folder** and let only the web service write to it. It must
   exist before the site starts: `dmoj/settings.py` refuses to load if
   `DMOJ_PDF_PROBLEM_CACHE` points to a missing folder, which stops the web
   service, Celery and the bridge. A `tmpfiles.d` line keeps it across reboots:

   ```
   d /var/cache/dmoj-pdf 0750 dmoj-uwsgi dmoj-uwsgi -
   ```

   **Do not put it under `MEDIA_ROOT`** or any other folder the front end serves:
   the PDF of a private problem would be readable by anyone who guesses its name.
   With systemd sandboxing, add the folder to the web unit's `ReadWritePaths=`; a
   reload is not enough after changing the unit, restart it.
4. **Point the site at pdfoid** in `local_settings.py`:

   ```python
   DMOJ_PDF_PDFOID_URL = 'http://127.0.0.1:8887'
   DMOJ_PDF_PROBLEM_CACHE = '/var/cache/dmoj-pdf'
   ```

   `DMOJ_PDF_PROBLEM_INTERNAL` is optional: it lets Nginx send cached files with
   `X-Accel-Redirect` through an `internal` location. Without it, Django sends
   them.
5. **Restart the web service** and open `/problem/<code>/pdf` for a public problem.

Chrome downloads the site's stylesheet and MathJax while printing, so pdfoid needs
outbound network access, and statements with math wait for MathJax before the
page is printed.

## Known limitations

- pdfoid prints one PDF at a time; the first request for a large contest can take
  tens of seconds, and later ones come from the cache.
- Saving a problem removes its cached problem PDFs, as upstream does; contest PDFs
  are replaced the next time someone asks for them.

Tests: [`tests/statements`](../tests/statements/README.md).

# pdfoid patch

[`direct-quit-and-timeout.patch`](direct-quit-and-timeout.patch) changes one file of
[DMOJ/pdfoid](https://github.com/DMOJ/pdfoid), tested on commit
`55ebc64794d6531fc2837db28869e1b7c6cc6894`. pdfoid is a separate project under the
GNU AGPL v3; apply the patch to your own checkout of it:

```sh
cd /opt/dmoj-pdf/pdfoid
git apply /srv/dmoj/site/deploy/examples/pdfoid/direct-quit-and-timeout.patch
```

What it fixes in `pdfoid/backends/direct.py`:

- **A ChromeDriver left running after every PDF.** pdfoid closed the browser with
  `contextlib.closing`, which only closes the window. One `chromedriver`, and
  sometimes a Chrome, stayed alive per PDF until the service ran out of memory or
  tasks. The patch calls `quit()` in a `finally` block.
- **A 300-second wait.** Selenium waits up to 300 seconds for a page to load,
  longer than uWSGI waits for the request that asked for the PDF. The patch sets a
  page load limit, 30 seconds by default, through the `PDFOID_PAGE_LOAD_TIMEOUT`
  environment variable, and reports a timeout as a rendering error.

See [problem and contest PDFs](../../../docs/pdf.md) for the rest of the setup and
[`../systemd/dmoj-pdfoid.service`](../systemd/dmoj-pdfoid.service) for an example
unit.

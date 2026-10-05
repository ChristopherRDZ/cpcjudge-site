# Offline compression

If the checkout is read-only for the web service, as the
[isolation guide](service-isolation.md) recommends, django-compressor cannot
write its output while serving pages. Pages whose compressed blocks were not
generated yet then answer **500**, and only in some languages or for some users.
The fix is to generate everything in advance:

```python
COMPRESS_OFFLINE = True
```

```sh
/srv/dmoj/venv/bin/python manage.py collectstatic --noinput
/srv/dmoj/venv/bin/python manage.py compress --force
```

Run the build as an account that can write `static/`, then restart the web
service. Do not make the static tree writable by the web service to avoid it.

## When to rebuild

Regenerate the manifest and restart the web service whenever you change:

- a template that contains a `{% compress %}` block;
- CSS, SCSS or JavaScript in `resources/`;
- a translation catalog or the `LANGUAGES` setting;
- django-compressor or the minifiers (`rcssmin`, `rjsmin`).

Each block is keyed by its rendered content, so editing a template **outside**
its `{% compress %}` blocks does not invalidate the manifest. The viewport script
and the inline styles of `templates/base.html` sit outside its blocks for that
reason. If you edit `base.html`, either keep both blocks byte-identical or
rebuild.

A missing variant answers `OfflineGenerationError` (HTTP 500) on the affected
page. After a rebuild, load the home page, `/problems/`, `/submissions/` and
`/stats/language/` in each language (`Accept-Language`), signed out and signed
in.

The compressed blocks depend on the template context, so make sure the build
covers every language you serve and both anonymous and signed-in users.

On WSL, files written into a Windows drive from Linux may lose their ownership
attributes; check the result after publishing a build.

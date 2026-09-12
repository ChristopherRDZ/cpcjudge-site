# HTTPS cookies and edge HSTS

Secure cookies and edge HSTS were enabled on 2026-09-11 after checking that the
site's intended public hostnames serve HTTPS and redirect HTTP to HTTPS.

The public settings example includes:

```python
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

The settings affect cookies when they are issued again; this change does not
invalidate sessions already stored on the server. Verification observed the
`Secure` attribute on a new CSRF cookie. A complete session-cookie check requires
a controlled login; the latest read-only check did not create a new login.

HSTS is served by the HTTPS edge with:

```http
Strict-Transport-Security: max-age=15552000; includeSubDomains
```

`preload` is not enabled. Check all affected subdomains before opting into
`includeSubDomains`. Browsers remember HSTS independently of server state;
removing a header does not immediately undo an already cached policy.

The deployment does not turn on Django's `SECURE_SSL_REDIRECT` or originate HSTS
from Django. TLS terminates at the trusted edge and the current origin does not
report the original HTTPS scheme to Django. Introducing redirects there without
coordinating proxy-scheme handling can cause a redirect loop.

For a different proxy topology, explicitly design trusted scheme forwarding and
its access boundary before enabling additional Django HTTPS behavior. Do not
trust an arbitrary client-supplied forwarding header. The snippets above record
the implemented cookie/edge changes, not a complete HTTPS configuration.

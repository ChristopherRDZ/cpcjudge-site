# HTTPS, cookies and HSTS

## Cookies

In `local_settings.py`:

```python
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
```

Browsers then send the session and CSRF cookies only over HTTPS. Existing
sessions are not invalidated; the flag applies when a cookie is issued again.

## HSTS

Serve HSTS from the HTTPS edge (the proxy or CDN that terminates TLS), for
example:

```http
Strict-Transport-Security: max-age=15552000; includeSubDomains
```

Check every subdomain before using `includeSubDomains`, and add `preload` only
when you are sure. Browsers remember HSTS: removing the header later does not
undo it for visitors who already received it.

## Knowing the request came over HTTPS

When TLS ends at a proxy and the origin speaks plain HTTP, Django needs to know
the original scheme: it uses it for CSRF origin checks and for absolute links
such as password-reset emails.

In the example layout (tunnel → Nginx on loopback → uWSGI), the tunnel sends
`X-Forwarded-Proto: https`, Nginx passes it on, and uWSGI sets the request scheme
from it. Django then treats those requests as HTTPS without
`SECURE_PROXY_SSL_HEADER`. Requests made directly to the origin without that
header are treated as HTTP, which is why a local test POST can fail the CSRF
origin check while real browser traffic works.

Only trust a forwarded scheme header when the origin is reachable exclusively
through your proxy. That is another reason to keep the origin on loopback.

Leave `SECURE_SSL_REDIRECT` off unless you have confirmed how your proxy reports
the scheme; a mismatch causes a redirect loop. The HTTP-to-HTTPS redirect is
best done at the edge.

# Private origin and request boundaries

Directory listings were disabled on 2026-09-07. Further Nginx restrictions were
applied on 2026-09-11. The following are generic fragments to merge into the
appropriate contexts of a deployment's existing configuration. They are not a
complete configuration or a copy of the production server's routing.

On 2026-09-13 the proxy also removed obsolete `X-XSS-Protection` and added these
headers in the applicable server context (check Nginx header inheritance):

```nginx
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "frame-ancestors 'self'; object-src 'none'; base-uri 'self'" always;
```

This structural CSP restricts framing, plugin objects and base URLs. It does
not restrict JavaScript execution; a `script-src` policy requires a separate
review of inline scripts, event handlers and third-party resources. Preserve
existing HTTPS, content-type and frame protections when merging fragments.

At main configuration scope:

```nginx
worker_shutdown_timeout 10s;
```

In the server reached by a local HTTPS tunnel:

```nginx
listen 127.0.0.1:8082;
listen [::1]:8082;
server_name judge.example.org;
```

Use loopback listeners only when the trusted proxy/tunnel runs locally. Keep
the existing application, static/media, websocket and long-poll routes. Disable
`autoindex` in the public static and media locations. The example socket below
must match the application and proxy permissions:

```nginx
location = /custom-test/run/ {
    client_max_body_size 2M;
    include uwsgi_params;
    uwsgi_pass unix:/run/dmoj/site.sock;
}
```

The deployed configuration retains its existing application-routing mechanism
inside this exact location. The important boundary is a 2 MiB body limit only
for the custom test endpoint; it does not lower upload limits on unrelated
administrative routes. Bodies exceeding the limit return 413 before Django.

Remove any public proxy route to `/post_event/`. Django publishes events directly
to the private event daemon. Keep its publishing listener on loopback and keep
the public `/event/` and `/channels/` consumers working. A request to the removed
publishing route should return 404.

## Applying and verifying an equivalent change

Validate the full candidate configuration before changing a live proxy. Nginx
reload is asynchronous: old workers may retain long-lived websocket connections.
The shutdown timeout bounds that overlap and can close those connections.

Changing a wildcard listener to a loopback listener conflicted with the old
bound socket during the tested reload. The listener transition was therefore
performed as a separate controlled restart. Inspect actual bound addresses and
connection acceptance rather than assuming a successful reload changed them.

Verification covered application/login/static responses, websocket and polling
routes, 404 for the removed publishing route, 413 above the size limit, and
connection refusal through the host's non-loopback interface. These checks do
not establish per-user rate limits or job-concurrency limits; the
[application admission controls](custom-tests.md) were added on 2026-09-14.

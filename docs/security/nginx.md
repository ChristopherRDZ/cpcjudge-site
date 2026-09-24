# Nginx: origin, request sizes and uploads

The [example configuration](../../deploy/examples/nginx.conf) is the reference.
This page explains the parts that are easy to get wrong.

## Private origin

If a tunnel or reverse proxy on the same host is the only way in, listen on
loopback only:

```nginx
listen 127.0.0.1:8082;
listen [::1]:8082;
```

Changing a `listen` directive needs a **restart**; a reload keeps the old socket
and does not report an error.

## Headers

```nginx
add_header X-Content-Type-Options nosniff;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "frame-ancestors 'self'; object-src 'none'; base-uri 'self'" always;
```

This Content Security Policy blocks framing, plugins and `<base>` changes. It
does **not** restrict JavaScript: DMOJ renders HTML from problem statements,
contest descriptions, blog posts and the site announcement, so anyone who can
edit those can run scripts in other users' browsers, administrators included.
Only give those permissions to people you trust.

## Request body limits

| Route | Limit | Why |
| --- | --- | --- |
| everything | `16M` | nothing else needs more |
| `/custom-test/run/` | `2M` | a custom test is source code and input |
| `/problem/<code>/test_data` | `500M` | problem data archives can be large |

A request over its limit gets **413** before it reaches the application.

### The upload route asks first

Nginx reads the whole request body before handing it to the application, so a
large limit would normally let anyone make the server receive half a gigabyte.
The example prevents that with `auth_request`: before reading the body, Nginx
asks the application at `/internal/problem-data-upload-gate` whether the caller
could upload problem data at all. That view answers **204** for signed-in
accounts with the problem-editing permission and **403** for everybody else, and
Nginx then refuses the upload without buffering it. Which problems an account may
actually edit is still checked by the application on the real request.

Two details in that internal location are required:

- `uwsgi_pass_request_body off` and an empty `CONTENT_LENGTH`: the question is
  asked without the body.
- `client_max_body_size 0`: the subrequest inherits the upload's
  `Content-Length`, and with the general 16M limit every real upload would fail
  with **500**. If uploads fail with 500, look for `auth request unexpected
  status` in the Nginx log.

The location is marked `internal`, so requesting it from outside returns 404.

### Where bodies are buffered

Bodies larger than Nginx's memory buffer are written to `client_body_temp_path`.
Put it on disk, not on a tmpfs such as `/run`, which is RAM. The example uses
`/var/lib/dmoj-nginx/client`, created by `StateDirectory=` in the
[service unit](../../deploy/examples/systemd/dmoj-nginx.service). Adding that
line needs `systemctl daemon-reload` and a restart of the service, once.

If every large POST fails with 500 and the application log is empty, check the
ownership of the temporary directories: Nginx must be able to write there.

### Limits outside Nginx

A CDN or tunnel in front of the site may have its own limit. Cloudflare, for
example, accepts request bodies of at most 100 MB on its Free and Pro plans and
answers 413 itself above that. It also receives the complete upload before
forwarding it.

## Static files

```nginx
location /static/ {
    alias /srv/dmoj/site/static/;
}
```

Keep the trailing slash in **both** the location and the alias. With
`location /static` and an alias ending in `/`, a request for `/static../X` is
served from `/srv/dmoj/site/X`, which exposes every file in the checkout,
settings included. After a change, `/static../robots.txt` must return 404.

Disable `autoindex` for static and media locations.

## Event daemon

Browsers connect to `/event/` (WebSocket) and `/channels/` (long polling).
Django publishes events straight to the daemon's publishing port, which must stay
on loopback. Do not proxy a public `/post_event/` route: anybody could publish
messages to every connected browser.

## Applying changes

Validate with `nginx -t`, then **reload**. A reload is gradual: old workers keep
serving their open connections until `worker_shutdown_timeout` (10 seconds in the
example), which also closes long-lived WebSockets; the browser client reconnects
by itself. Check the specific route you changed, not only the home page.

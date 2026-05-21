# Deployment

Lore can run as a container, a systemd service, or a Python process behind a
reverse proxy.

## Docker

Build from the repository root:

```bash
docker build -t lore-app .
```

Run with persistent content and database storage:

```bash
docker run -d --name lore \
  -p 8078:8000 \
  -e LORE_CONTENT_DIR=/data/pages \
  -e LORE_SEARCH_DB=/data/db/search.db \
  -e LORE_VECTOR_DB=/data/db/vectors.db \
  -e LORE_API_KEYS_DB=/data/db/api_keys.db \
  -e LORE_AUTH_MODE=bearer \
  -e LORE_AUTH_SECRET=your-secret-here \
  -v /srv/lore/pages:/data/pages \
  -v /srv/lore/db:/data/db \
  lore-app
```

Verify:

```bash
curl -sS http://localhost:8078/healthz
```

## Systemd

The repository includes a systemd guide at
[`../../../docs/install/systemd.md`](../../../docs/install/systemd.md).

Minimal unit:

```ini
[Unit]
Description=Lore Knowledge Wiki
After=network.target

[Service]
Type=simple
User=lore
WorkingDirectory=/opt/lore
Environment=LORE_CONTENT_DIR=/var/lib/lore/pages
Environment=LORE_SEARCH_DB=/var/lib/lore/search.db
Environment=LORE_VECTOR_DB=/var/lib/lore/vectors.db
Environment=LORE_API_KEYS_DB=/var/lib/lore/api_keys.db
Environment=LORE_AUTH_MODE=bearer
Environment=LORE_AUTH_SECRET=your-secret-here
ExecStart=/opt/lore/.venv/bin/uvicorn lore_app.main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lore
curl -sS http://localhost:8000/healthz
```

## Environment Reference

Set these for production:

```bash
LORE_CONTENT_DIR=/var/lib/lore/pages
LORE_SEARCH_DB=/var/lib/lore/search.db
LORE_VECTOR_DB=/var/lib/lore/vectors.db
LORE_API_KEYS_DB=/var/lib/lore/api_keys.db
LORE_AUTH_MODE=bearer
LORE_AUTH_SECRET=changeme
LORE_BRAND_TITLE=LORE
LORE_BRAND_URL=/
```

When `LORE_AUTH_MODE` is `bearer` or `basic`, `LORE_AUTH_SECRET` must be a
non-empty string. If it is unset, empty, or whitespace-only, the application
refuses to start with a configuration error. For `api_key` or `none` modes,
`LORE_AUTH_SECRET` is not required.

Optional workspace mounts:

```bash
LORE_WORKSPACES='{"public":{"content_dir":"/var/lib/lore/public/pages"}}'
```

## Reverse Proxy

Caddy:

```caddyfile
lore.example.com {
  encode zstd gzip
  reverse_proxy 127.0.0.1:8000
}
```

Caddy with bearer auth handled by Lore:

```caddyfile
lore.example.com {
  encode zstd gzip
  header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains"
  }
  reverse_proxy 127.0.0.1:8000
}
```

Nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name lore.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Keep `X-Forwarded-For` intact if you want application rate limits to key on the
original client address.

# HTTPS Reverse Proxy Runbook

Date: 2026-04-05

## Goal

Run `true-learning-system` behind Caddy so student traffic terminates at HTTPS and the app container no longer needs direct public exposure.

Final-host rehearsal order now lives in:

- `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`
- local rehearsal evidence now lives in:
  - `docs/50-infra-and-ops/local-rollout-rehearsal-results-2026-04-06.md`

Artifacts added:

- `docker-compose.proxy.yml`
- `deploy/caddy/Caddyfile`

## What this changes

- `app` still listens on `8000` inside Docker
- base `docker-compose.yml` now binds `app` to `TLS_APP_BIND_HOST`, which defaults to `127.0.0.1`
- the optional proxy layer exposes:
  - `${TLS_PROXY_HTTP_PORT:-80}`
  - `${TLS_PROXY_HTTPS_PORT:-443}`
- Caddy forwards traffic to `app:8000` on the internal Docker network

## Recommended env for proxy deployment

At minimum set:

```env
TLS_APP_BIND_HOST=127.0.0.1
TLS_PROXY_BIND_HOST=0.0.0.0
TLS_PUBLIC_HOSTNAME=your-domain.example.com
TLS_PROXY_HTTP_PORT=80
TLS_PROXY_HTTPS_PORT=443
AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SAMESITE=lax
UVICORN_FORWARDED_ALLOW_IPS=*
```

Optional:

```env
TLS_MAX_REQUEST_BODY=32MB
```

App-layer defaults now also support a second line of defense through response headers:

```env
SECURITY_HEADERS_ENABLED=true
SECURITY_HEADERS_HSTS_ENABLED=true
```

If you are rehearsing on one machine only, you can temporarily use:

```env
TLS_PUBLIC_HOSTNAME=localhost
```

Caddy will use a local certificate path for localhost-style development access.

## Start commands

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml build
docker compose -f docker-compose.yml -f docker-compose.proxy.yml up -d
```

## Validation

Check the rendered stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.proxy.yml config
```

Validate the Caddy config directly:

```powershell
docker run --rm -v ${PWD}\deploy\caddy\Caddyfile:/etc/caddy/Caddyfile:ro -e TLS_PUBLIC_HOSTNAME=localhost -e TLS_MAX_REQUEST_BODY=32MB caddy:2.8 caddy validate --config /etc/caddy/Caddyfile
```

Check app health from inside the host:

```powershell
curl http://127.0.0.1:18000/health
```

Check the public proxy entry:

```powershell
curl -I https://your-domain.example.com/health
```

Run the rollout auth/cookie config verifier:

```powershell
python scripts\verify_rollout_auth_config.py --json
```

Run the production smoke test with a real student account:

```powershell
pwsh scripts\smoke_test_production.ps1 -BaseUrl https://your-domain.example.com -Email student@example.com -Password 'change-me'
```

For local self-signed HTTPS rehearsal on Windows PowerShell:

```powershell
.\scripts\smoke_test_production.ps1 -BaseUrl https://localhost:18443 -Email student@example.com -Password 'change-me' -Insecure
```

Expected:

- the app remains reachable on the loopback-bound internal port
- student traffic reaches the site through Caddy on HTTPS
- login cookie is marked `Secure`
- same-origin login succeeds through HTTPS
- cross-site login probe is rejected with `403 csrf validation failed`

For the final deployment host, do not stop at this document alone. After these checks pass, continue the full operator flow in `docs/50-infra-and-ops/rollout-final-rehearsal-checklist-2026-04-06.md`.

## Notes

- The current Caddy config adds baseline security headers and a permissive CSP.
- `main.py` now also applies the same baseline security headers at the app layer, so `/api/*` responses and auth-gate rejects still carry the expected headers even if the proxy layer is misconfigured.
- `scripts/smoke_test_production.ps1` now sends same-origin `Origin` headers for unsafe auth requests so the rollout rehearsal matches the CSRF posture used by browsers.
- The CSP is intentionally permissive because the current templates still rely on inline scripts/styles.
- The proxy container now only receives proxy-related env vars instead of the full app `.env`.
- If you need an ACME contact email, add `email you@example.com` manually to `deploy/caddy/Caddyfile`.
- For public rollout, DNS must already point your hostname to the target machine before ACME issuance can succeed.

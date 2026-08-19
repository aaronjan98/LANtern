# Deco reboot automation — deploy

Three parts: (1) the container stack, (2) the host runner + timer, (3) optional
oauth2-proxy in front of the lantern UI. Parts 1–2 are the working feature; part
3 is the "real login" hardening and needs a GitHub OAuth app you create.

## 1. Container stack (on sweetpea, in /opt/lantern)

```bash
git -C /opt/lantern fetch origin && git -C /opt/lantern checkout deco-reboot-ui
cd /opt/lantern
docker compose build api ui deco-reboot
docker compose up -d api ui deco-reboot
docker compose logs -f deco-reboot   # watch the brain come up
```

The brain seeds "last reboot = now" on first run, so it will **not** surprise-
reboot the AP on deploy; the 3-day backstop starts counting from now. Trigger the
first reboot yourself from the UI ("Reboot now") when you're ready to validate.

## 2. Host runner + systemd timer

```bash
install -m 755 /opt/lantern/deploy/deco-reboot-runner.sh /home/aj/deco-reboot/deco-reboot-runner.sh
sudo cp /opt/lantern/deploy/deco-reboot-runner.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now deco-reboot-runner.timer
systemctl list-timers deco-reboot-runner.timer
```

**Validate without touching the AP first:** uncomment `Environment=DRY_RUN=1` in
the `.service`, `daemon-reload`, click "Reboot now" in the UI, wait for an idle
window, and confirm a `succeeded (dry-run)` row appears with before/after tunnel
checks. Then re-comment DRY_RUN and `daemon-reload` for real power-cycles.

Manual end-to-end test any time:
```bash
docker exec lantern-deco-reboot-1 python3 -m lantern.deco_reboot --status
docker exec lantern-deco-reboot-1 python3 -m lantern.deco_reboot --request --by you
sudo systemctl start deco-reboot-runner.service   # if idle, executes now
```

## 3. Front lantern with oauth2-proxy (optional, GitHub — like immich)

Today `lantern.home` is LAN/Tailscale-only with no login, so the approve button
is already reachable only by you. Do this part when you want per-user GitHub login
(and/or to expose lantern publicly). No app code changes — the API already reads
`X-Forwarded-User`.

**a. Create a GitHub OAuth app** (github.com → Settings → Developer settings →
OAuth Apps → New): Homepage `https://lantern.aaronjanovitch.com`, callback
`https://lantern.aaronjanovitch.com/oauth2/callback`. Note the Client ID/Secret.

**b. Run an oauth2-proxy for lantern** (mirror your immich one; new cookie secret
via `openssl rand -base64 32`):
```
OAUTH2_PROXY_PROVIDER=github
OAUTH2_PROXY_GITHUB_USERS=aaronjan98         # only you
OAUTH2_PROXY_CLIENT_ID=<from step a>
OAUTH2_PROXY_CLIENT_SECRET=<from step a>
OAUTH2_PROXY_COOKIE_SECRET=<openssl rand -base64 32>
OAUTH2_PROXY_COOKIE_DOMAIN=.aaronjanovitch.com
OAUTH2_PROXY_COOKIE_SECURE=true
OAUTH2_PROXY_EMAIL_DOMAINS=*
OAUTH2_PROXY_UPSTREAMS=http://lantern            # the existing ui upstream
OAUTH2_PROXY_HTTP_ADDRESS=0.0.0.0:4180
OAUTH2_PROXY_REVERSE_PROXY=true
OAUTH2_PROXY_SET_XAUTHREQUEST=true               # emits X-Auth-Request-User/Email
```

**c. Protect the vhost** — in `/etc/nginx/sites-available/lantern.home`, add a
public `server_name lantern.aaronjanovitch.com` block (keep `lantern.home` open
for LAN if you like) using the snippet in `deploy/nginx-lantern-oauth2.conf`.
Reload nginx. The API then records the real GitHub identity as the approver.

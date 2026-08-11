# ECS Native Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare and execute a secure native deployment to Alibaba Cloud ECS, support private testing before ICP approval, and switch to HTTPS plus the official mini-program endpoint after approval.

**Architecture:** Nginx is the only public web entry point and proxies to one systemd-managed FastAPI process bound to `127.0.0.1:8000`. Code, data, backups, and secrets live in separate directories. Before ICP approval, only SSH is reachable; after approval, ports 80/443 are opened, HTTPS is enabled, and the mini program uses the user's personal API domain from an external deployment-target configuration.

**Tech Stack:** Ubuntu/Debian ECS, Python venv, systemd, Nginx, SQLite, Bash, Alibaba Cloud security groups, WeChat Mini Program.

## Global Constraints

- Do not open public 80/443 or resolve the production domain to the mainland ECS before ICP approval.
- Never expose port 8000 in the Alibaba Cloud security group or host firewall.
- FastAPI runs as one non-root process and binds only `127.0.0.1:8000`.
- Preserve `/srv/tenaishi/data`, `/srv/tenaishi/backups`, and `/etc/tenaishi` across code updates.
- Existing untracked `deploy/` files are user-owned work: review and extend them; do not replace them blindly.
- Never connect to or modify any company server or infer a target from existing SSH configuration; company infrastructure is outside this personal project's authorization scope.
- The personal ECS SSH target, public IP, website domain, and API domain must be supplied explicitly by the user and stored outside Git in `~/.config/tenaishi/deploy-target.env`.
- Production secrets are created on the server and never committed.
- This plan starts only after the authentication and backup plans pass their full suites.

---

## File Map

- `deploy/setup-server.sh`: idempotent base installation and directory setup.
- `deploy/update-server.sh`: backup-first, fast-forward-only application update.
- `deploy/tenaishi.service`: hardened FastAPI service.
- `deploy/nginx-personal-inventory.conf`: personal website and API TLS virtual hosts; do not reuse the company-domain template.
- `deploy/smoke-test.sh`: local and HTTPS health/security checks.
- `deploy/README.md`: pre-ICP, post-ICP, rollback, and Alibaba console steps.
- `miniprogram/utils/connection.js`: development LAN mode and production HTTPS mode.
- `miniprogram/app.js`: select the correct endpoint by mini-program environment.
- `tests/test_linux_deploy_assets.py`: static verification of deployment assets.
- `tests/miniprogram_connection.test.js`: environment-aware endpoint tests.

### Task 1: Reconcile Existing Deployment Work and Define Stable Paths

**Files:**
- Modify: `deploy/setup-server.sh`
- Modify: `deploy/tenaishi.service`
- Modify: `deploy/README.md`
- Create: `tests/test_linux_deploy_assets.py`

**Interfaces:**
- Produces fixed paths `/srv/tenaishi/app`, `/srv/tenaishi/data`, `/srv/tenaishi/backups`, and `/etc/tenaishi/tenaishi.env`.
- Produces Linux user and group `tenaishi`.

- [ ] **Step 1: Inspect and preserve the current uncommitted deploy diff**

Run:

```bash
git status --short
git diff -- deploy
find deploy -maxdepth 1 -type f -print -exec sed -n '1,220p' {} \;
```

Record which parts already satisfy the approved design. Do not run setup on the local Mac.

- [ ] **Step 2: Write failing deployment asset tests**

Assert that setup creates the four fixed paths, creates a non-login service user, installs `python3-venv nginx rsync util-linux curl`, never writes secrets, and does not open firewall ports. Assert the service binds `127.0.0.1:8000` and uses one worker.

- [ ] **Step 3: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_linux_deploy_assets -v`

Expected: current `/opt/tenaishi` paths and root-owned service fail the assertions.

- [ ] **Step 4: Make setup idempotent**

Required behavior:

```bash
id -u tenaishi >/dev/null 2>&1 || useradd --system --create-home --home-dir /srv/tenaishi --shell /usr/sbin/nologin tenaishi
install -d -o tenaishi -g tenaishi -m 0750 /srv/tenaishi/app /srv/tenaishi/data /srv/tenaishi/backups
install -d -o root -g tenaishi -m 0750 /etc/tenaishi
```

Clone only when `/srv/tenaishi/app/.git` is absent. Do not overwrite an existing environment file. Set production database and upload settings to absolute data paths.

- [ ] **Step 5: Harden the application service**

Service requirements:

```ini
User=tenaishi
Group=tenaishi
WorkingDirectory=/srv/tenaishi/app
EnvironmentFile=/etc/tenaishi/tenaishi.env
ExecStart=/srv/tenaishi/app/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/tenaishi/data /srv/tenaishi/backups
UMask=0077
```

- [ ] **Step 6: Run tests and commit**

Run: `.venv/bin/python -m unittest tests.test_linux_deploy_assets -v`

Expected: PASS.

```bash
git add deploy/setup-server.sh deploy/tenaishi.service deploy/README.md tests/test_linux_deploy_assets.py
git commit -m "feat: prepare hardened native ECS service"
```

### Task 2: Nginx Boundary, Update, and Smoke Tests

**Files:**
- Create: `deploy/nginx-personal-inventory.conf`
- Create: `deploy/update-server.sh`
- Create: `deploy/smoke-test.sh`
- Modify: `tests/test_linux_deploy_assets.py`

**Interfaces:**
- Produces website and API hosts from `TENAISHI_SITE_DOMAIN` and `TENAISHI_API_DOMAIN` in the external target configuration.

- [ ] **Step 1: Add failing static policy tests**

Assert that Nginx proxies only to `127.0.0.1:8000`, sets forwarded headers, limits uploads to 100 MB, adds HSTS only on TLS hosts, rejects dotfiles, and contains no `auth_basic` fallback. Assert update script runs a verified backup before `git pull --ff-only`.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_linux_deploy_assets -v`

Expected: the current optional Basic Auth and update behavior fail policy checks.

- [ ] **Step 3: Implement Nginx and update boundaries**

For the API host include:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location ~ /\. { deny all; }
```

`update-server.sh` must require a clean Git worktree, run `scripts/backup.sh`, perform `git pull --ff-only`, install pinned requirements, restart, wait for local health, and show the new commit hash. It must exit without switching revisions if backup fails.

- [ ] **Step 4: Add a smoke-test script**

Checks:

```bash
curl -fsS http://127.0.0.1:8000/health
source "$HOME/.config/tenaishi/deploy-target.env"
curl -fsS "https://${TENAISHI_API_DOMAIN}/health"
if curl -fsS --connect-timeout 3 "http://${TENAISHI_PUBLIC_IP}:8000/health"; then exit 1; fi
```

The public-IP check targets the personal ECS address loaded from the external configuration and must fail to connect.

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/python -m unittest tests.test_linux_deploy_assets -v`

Expected: PASS.

```bash
git add deploy/nginx-personal-inventory.conf deploy/update-server.sh deploy/smoke-test.sh tests/test_linux_deploy_assets.py
git commit -m "feat: secure ECS proxy and updates"
```

### Task 3: Private ECS Deployment Before ICP Approval

**Files:**
- Modify: `deploy/README.md`

**Interfaces:**
- Produces a running private ECS instance reachable only through SSH tunneling.

- [ ] **Step 1: Resolve exact server target with read-only checks**

Obtain the personal ECS public IP, SSH account, website domain, and API domain explicitly from the user. Do not infer them from existing SSH config. Save them outside the repository with mode `0600`:

```bash
install -d -m 0700 "$HOME/.config/tenaishi"
touch "$HOME/.config/tenaishi/deploy-target.env"
chmod 0600 "$HOME/.config/tenaishi/deploy-target.env"
```

The file must define non-empty `TENAISHI_SSH_TARGET`, `TENAISHI_PUBLIC_IP`, `TENAISHI_SITE_DOMAIN`, and `TENAISHI_API_DOMAIN`. Before connecting, show the four non-secret target values to the user and require confirmation that they belong to the personal project; then run only:

```bash
source "$HOME/.config/tenaishi/deploy-target.env"
ssh -o BatchMode=yes "$TENAISHI_SSH_TARGET" 'uname -a; id; cat /etc/os-release; df -h; free -h'
```

Expected: supported Ubuntu/Debian Linux, sufficient disk, and no permission error. Do not search for or print secrets.

- [ ] **Step 2: Verify Alibaba security group before installation**

Required inbound state before ICP:

```text
22/tcp  source: the owner's current public IP/32
80/tcp  absent
443/tcp absent
8000/tcp absent
```

Stop and ask the user if the target instance or rule scope is ambiguous.

- [ ] **Step 3: Install and create production secrets**

Upload or clone the reviewed commit, run setup, then generate on the server:

```bash
openssl rand -hex 32
/srv/tenaishi/app/.venv/bin/python -c 'import pyotp; print(pyotp.random_base32())'
```

Write results directly into `/etc/tenaishi/tenaishi.env` with mode `0640`, together with the actual WeChat AppID/AppSecret supplied through the normal project configuration channel. Never paste the resulting file into chat or command output.

- [ ] **Step 4: Bootstrap owner and run private smoke tests**

Run owner bootstrap interactively, start the service and backup timer, then verify:

```bash
systemctl status tenaishi --no-pager
systemctl status tenaishi-backup.timer --no-pager
curl -fsS http://127.0.0.1:8000/health
ss -lntp | grep -E '(:8000|:80|:443)'
```

Expected: `127.0.0.1:8000` only; no public web listener before ICP.

- [ ] **Step 5: Test through SSH tunnel**

From the owner's computer:

```bash
source "$HOME/.config/tenaishi/deploy-target.env"
ssh -L 8080:127.0.0.1:8000 "$TENAISHI_SSH_TARGET"
```

Open `http://127.0.0.1:8080/auth/login`, verify owner TOTP login, employee creation, one employee activation in the developer build, and a full backup/temporary restore drill.

- [ ] **Step 6: Record deployment verification in docs and commit**

Record only dates, versions, and pass/fail results; do not record IPs or secrets.

```bash
git add deploy/README.md
git commit -m "docs: verify private ECS deployment"
```

### Task 4: Production Mini-Program Endpoint

**Files:**
- Modify: `miniprogram/utils/connection.js`
- Modify: `miniprogram/app.js`
- Modify: `miniprogram/pages/connection/index.js`
- Modify: `miniprogram/pages/connection/index.wxml`
- Modify: `tests/miniprogram_connection.test.js`
- Modify: `tests/test_mobile_connection.py`

**Interfaces:**
- Produces the release endpoint `https://${TENAISHI_API_DOMAIN}` from the external deployment-target configuration at build time.
- Preserves LAN QR/manual connection only for `develop` and `trial` builds.

- [ ] **Step 1: Review current dirty connection changes before editing**

Run:

```bash
git diff -- app/mobile_connection_pages.py app/services/mobile_connection.py miniprogram/pages/connection/index.js miniprogram/pages/connection/index.wxml miniprogram/utils/connection.js
```

Preserve all intentional behavior already present. If current changes conflict with the release-mode requirement, resolve explicitly rather than replacing the files.

- [ ] **Step 2: Add failing environment-selection tests**

Test:

```javascript
assert.equal(
  connection.baseUrlForEnvironment('release', {
    releaseBaseUrl: 'https://personal-inventory.example.test'
  }),
  'https://personal-inventory.example.test'
)
assert.equal(connection.canEditConnection('release'), false)
assert.equal(connection.canEditConnection('develop'), true)
```

Assert release mode ignores any saved LAN address.

- [ ] **Step 3: Run and confirm failure**

Run: `node --test tests/miniprogram_connection.test.js`

Expected: missing `baseUrlForEnvironment` or current public-URL rejection fails the test.

- [ ] **Step 4: Implement environment-aware connection**

Use `wx.getAccountInfoSync().miniProgram.envVersion`. Release always uses the explicitly configured personal HTTPS API domain; develop/trial retain the current connection page and saved LAN mode. Hide scan/manual controls in release and show only authenticated connection status. The build must fail when the personal API URL is absent or is the forbidden company IP/domain target.

- [ ] **Step 5: Run tests and commit**

Run:

```bash
node --test tests/miniprogram_connection.test.js tests/miniprogram_auth.test.js tests/miniprogram_material_api.test.js
.venv/bin/python -m unittest tests.test_mobile_connection -v
```

Expected: PASS.

```bash
git add miniprogram/utils/connection.js miniprogram/app.js miniprogram/pages/connection/index.js miniprogram/pages/connection/index.wxml tests/miniprogram_connection.test.js tests/test_mobile_connection.py
git commit -m "feat: select production mini-program endpoint"
```

### Task 5: ICP-to-Production Cutover

**Files:**
- Modify: `deploy/README.md`

**Interfaces:**
- Produces HTTPS website/API access, approved mini-program configuration, and recorded rollback steps.

- [ ] **Step 1: Verify prerequisites without changing DNS**

Confirm ICP status in the official system, certificate files, domain ownership, small-program filing status, and privacy copy. Confirm Alibaba security group has no 8000 rule.

- [ ] **Step 2: Configure DNS and TLS**

Add A records for the personal website and API domains from `~/.config/tenaishi/deploy-target.env`. Install certificates with owner `root`, mode `0600` for keys, then run:

```bash
nginx -t
systemctl reload nginx
```

Expected: configuration test successful.

- [ ] **Step 3: Open only required ports and run smoke tests**

Allow 80/443 publicly for redirect and HTTPS; retain SSH IP restriction; keep 8000 closed. Run `deploy/smoke-test.sh` against the personal ECS target loaded from the external configuration. Verify anonymous `/admin` redirects to login and employee bearer tokens receive `403` on drawing endpoints.

- [ ] **Step 4: Configure and submit the mini program**

In WeChat Public Platform add the explicitly configured personal HTTPS API domain to request/upload/download legal domains as actually used, confirm mini-program filing and privacy guide, upload the release build, complete experience testing, submit review, and publish only after approval.

- [ ] **Step 5: Perform production acceptance**

Test owner remote PC login, TOTP, employee creation, WeChat binding, every current mini-program feature, disabled-user revocation, idempotent retry, backup success, and temporary restore. Record pass/fail without secrets.

- [ ] **Step 6: Document rollback and commit**

Rollback order:

```text
disable employee writes
→ restore previous Git revision
→ restore database only if a schema/data fault requires it
→ restart FastAPI
→ run smoke tests
```

```bash
git add deploy/README.md
git commit -m "docs: complete production cutover checklist"
```

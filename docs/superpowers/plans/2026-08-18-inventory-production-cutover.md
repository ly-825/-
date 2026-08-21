# Inventory Production Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the existing inventory system at `https://inventory.tnsautoparts.com`, then replace the temporary empty database with the trial computer's verified business data without losing inventory, transactions, or drawings.

**Architecture:** One Nginx virtual host terminates HTTPS and proxies the PC admin pages and mini-program API to one systemd-managed FastAPI process on `127.0.0.1:8000`. Code, data, backups, and secrets remain separated under `/srv/tenaishi` and `/etc/tenaishi`; the trial SQLite database is imported only during a controlled stopped-service cutover.

**Tech Stack:** Ubuntu 22.04, Nginx, Let's Encrypt Certbot, systemd, Python 3.10 virtual environment, FastAPI, SQLite WAL, native WeChat Mini Program.

## Global Constraints

- Public IP is exactly `47.98.121.142`.
- The single public inventory hostname is exactly `inventory.tnsautoparts.com` for both PC admin and mini-program API traffic.
- Public ports are `80` and `443`; FastAPI port `8000` remains bound to `127.0.0.1` and closed publicly.
- Production code comes from the public repository `https://github.com/ly-825/-.git` after the production rollout branch is integrated and verified on `main`.
- Persistent data lives only in `/srv/tenaishi/data`; updates must preserve `/srv/tenaishi/data`, `/srv/tenaishi/backups`, and `/etc/tenaishi`.
- Do not enter passwords, WeChat AppSecret, TOTP secrets, or session secrets in chat, commits, command output, or logs.
- Do not expose a trial or empty database to employees as the formal system.
- The trial computer must stop the inventory program before its `data` directory is archived.
- The original trial archive remains unchanged; all validation and migration use a separate temporary extraction directory.
- Do not publish the mini program until HTTPS, the WeChat request legal domain, mini-program filing, privacy declaration, and review are complete.

---

### Task 1: Make the production proxy explicitly support one inventory hostname

**Files:**
- Modify: `tests/test_linux_deploy_assets.py`
- Modify: `deploy/setup-server.sh`
- Modify: `deploy/nginx-personal-inventory.conf`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: external `TENAISHI_API_DOMAIN` from `$HOME/.config/tenaishi/deploy-target.env`.
- Produces: one HTTP redirect host and one HTTPS reverse-proxy host for the same inventory domain.

- [ ] **Step 1: Replace the two-domain Nginx assertions with a failing single-domain contract**

Update `test_nginx_uses_personal_placeholders_and_secure_proxy_boundary` so it includes these exact assertions:

```python
self.assertNotIn("${TENAISHI_SITE_DOMAIN}", nginx)
self.assertEqual(nginx.count("server_name ${TENAISHI_API_DOMAIN};"), 2)
self.assertEqual(nginx.count("proxy_pass http://127.0.0.1:8000"), 1)
self.assertEqual(nginx.count("location ~ /\\."), 1)
self.assertEqual(nginx.count("Strict-Transport-Security"), 1)
self.assertIn(
    "/etc/letsencrypt/live/${TENAISHI_API_DOMAIN}/fullchain.pem",
    nginx,
)
self.assertIn(
    "/etc/letsencrypt/live/${TENAISHI_API_DOMAIN}/privkey.pem",
    nginx,
)
```

In `test_setup_uses_stable_paths_user_packages_and_no_firewall_changes`, add `gettext-base` and `sqlite3` to the required package tuple so the server always has `envsubst` and the SQLite integrity-check CLI.

- [ ] **Step 2: Run the focused test and confirm the old two-domain template fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_linux_deploy_assets.LinuxDeployAssetTest.test_nginx_uses_personal_placeholders_and_secure_proxy_boundary -v
```

Expected: `FAIL`; the old template still contains `TENAISHI_SITE_DOMAIN` and two HTTPS proxy blocks.

- [ ] **Step 3: Replace the Nginx template with the single-domain configuration**

Set `deploy/nginx-personal-inventory.conf` to:

```nginx
# Render with envsubst after loading the external deployment target.

server {
    listen 80;
    listen [::]:80;
    server_name ${TENAISHI_API_DOMAIN};
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${TENAISHI_API_DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${TENAISHI_API_DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${TENAISHI_API_DOMAIN}/privkey.pem;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    client_max_body_size 100m;
    proxy_read_timeout 180s;

    location ~ /\. {
        deny all;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

In `deploy/README.md`, render only the inventory domain:

```bash
source "$HOME/.config/tenaishi/deploy-target.env"
envsubst '$TENAISHI_API_DOMAIN' \
  < /srv/tenaishi/app/deploy/nginx-personal-inventory.conf \
  > /etc/nginx/sites-available/tenaishi
nginx -t
```

Add `gettext-base` and `sqlite3` to the existing `apt-get install -y` package list in `deploy/setup-server.sh`.

- [ ] **Step 4: Run the focused deployment tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_linux_deploy_assets -v
```

Expected: all `LinuxDeployAssetTest` tests pass.

- [ ] **Step 5: Commit the single-domain deployment contract**

```bash
git add tests/test_linux_deploy_assets.py deploy/setup-server.sh deploy/nginx-personal-inventory.conf deploy/README.md
git commit -m "fix: deploy inventory on one secure domain"
```

---

### Task 2: Integrate, verify, and publish the production source revision

**Files:**
- Modify only files changed by merging `origin/main` into `feature/personal-inventory-rollout`.

**Interfaces:**
- Consumes: verified production rollout commits and the Windows updater fixes currently on `origin/main`.
- Produces: one tested `main` revision that the ECS setup script can clone without credentials.

- [ ] **Step 1: Fetch and inspect both histories**

```bash
git fetch origin
git status --short
git log --oneline feature/personal-inventory-rollout..origin/main
git log --oneline origin/main..feature/personal-inventory-rollout
```

Expected: the worktree is clean; the output shows the updater fixes on `origin/main` and production rollout commits on the current branch.

- [ ] **Step 2: Merge the current remote main into the rollout branch**

```bash
git merge --no-edit origin/main
git diff --check origin/main...HEAD
```

Expected: merge succeeds without unresolved files; `git diff --check` prints nothing.

- [ ] **Step 3: Run the complete Python and mini-program JavaScript suites**

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/*.test.js
```

Expected: both commands exit `0` with no failures or errors.

- [ ] **Step 4: Publish the verified revision to main**

```bash
git push origin HEAD:main
git ls-remote origin refs/heads/main
git rev-parse HEAD
```

Expected: the remote main hash and local `HEAD` hash are identical.

---

### Task 3: Bootstrap the clean ECS without exposing business data

**Files:**
- Create on ECS: `/srv/tenaishi/app`
- Create on ECS: `/srv/tenaishi/data`
- Create on ECS: `/srv/tenaishi/backups`
- Create on ECS: `/etc/tenaishi/tenaishi.env`
- Create on ECS: `/root/.config/tenaishi/deploy-target.env`

**Interfaces:**
- Consumes: verified `origin/main`, root SSH access to `47.98.121.142`, and the existing `deploy/setup-server.sh`.
- Produces: installed but not yet employee-facing FastAPI service, backup timer, Nginx, and protected production configuration.

- [ ] **Step 1: Reconfirm the deployment target and that no previous app paths exist**

```bash
ssh root@47.98.121.142 'test ! -e /srv/tenaishi/app && test ! -e /etc/tenaishi/tenaishi.env && df -h / && ss -lntp'
```

Expected: exit `0`, at least 50 GB free, and no listeners on `80`, `443`, or `8000`.

- [ ] **Step 2: Clone main and run the idempotent setup script**

```bash
ssh root@47.98.121.142 'TENAISHI_REPO_URL=https://github.com/ly-825/-.git bash -s' \
  < deploy/setup-server.sh
```

Expected: packages install, `/srv/tenaishi/app/.git` exists, the `tenaishi` user exists, and the backup timer is enabled; the application service is not started until secrets are configured.

- [ ] **Step 3: Create the external deployment target file with fixed non-secret values**

Run:

```bash
ssh root@47.98.121.142 'set -eu; install -d -o root -g root -m 0700 /root/.config/tenaishi; install -o root -g root -m 0600 /dev/null /root/.config/tenaishi/deploy-target.env; printf "%s\n" "TENAISHI_PUBLIC_IP=47.98.121.142" "TENAISHI_API_DOMAIN=inventory.tnsautoparts.com" > /root/.config/tenaishi/deploy-target.env'
```

The file content is exactly:

```text
TENAISHI_PUBLIC_IP=47.98.121.142
TENAISHI_API_DOMAIN=inventory.tnsautoparts.com
```

Verify:

```bash
ssh root@47.98.121.142 'stat -c "%a %U %G %n" /root/.config/tenaishi/deploy-target.env'
```

Expected: `600 root root /root/.config/tenaishi/deploy-target.env`.

- [ ] **Step 4: Generate production-only authentication secrets on the ECS**

On the ECS, generate `AUTH_PEPPER` and `OWNER_TOTP_SECRET`, validate them before editing, set the known AppID, and keep `WECHAT_APP_SECRET` empty until it is retrieved privately from the WeChat platform:

```bash
ssh root@47.98.121.142 '
set -euo pipefail
AUTH_PEPPER_NEW="$(openssl rand -hex 32)"
OWNER_TOTP_SECRET_NEW="$(runuser -u tenaishi -- /srv/tenaishi/app/.venv/bin/python -c "import pyotp; print(pyotp.random_base32())")"
test ${#AUTH_PEPPER_NEW} -eq 64
test -n "${OWNER_TOTP_SECRET_NEW:?}"
sed -i "s/^AUTH_PEPPER=.*/AUTH_PEPPER=${AUTH_PEPPER_NEW}/" /etc/tenaishi/tenaishi.env
sed -i "s/^OWNER_TOTP_SECRET=.*/OWNER_TOTP_SECRET=${OWNER_TOTP_SECRET_NEW}/" /etc/tenaishi/tenaishi.env
sed -i "s/^WECHAT_APP_ID=.*/WECHAT_APP_ID=wxc9c29ffe2999dff6/" /etc/tenaishi/tenaishi.env
chown root:tenaishi /etc/tenaishi/tenaishi.env
chmod 0640 /etc/tenaishi/tenaishi.env
'
```

Verification command:

```bash
ssh root@47.98.121.142 'set -a; . /etc/tenaishi/tenaishi.env; set +a; test ${#AUTH_PEPPER} -eq 64; test -n "$OWNER_TOTP_SECRET"; test "$WECHAT_APP_ID" = wxc9c29ffe2999dff6; stat -c "%a %U %G" /etc/tenaishi/tenaishi.env'
```

Expected: exit `0` and permissions `640 root tenaishi`; no secret values are printed.

- [ ] **Step 5: Start FastAPI locally and verify the empty pre-cutover service**

```bash
ssh root@47.98.121.142 'systemctl start tenaishi.service; systemctl is-active tenaishi.service; curl -fsS http://127.0.0.1:8000/health; ss -lntp | grep "127.0.0.1:8000"'
```

Expected: service is `active`, health returns status `ok`, and port `8000` listens only on `127.0.0.1`.

---

### Task 4: Issue HTTPS and activate the single public inventory address

**Files:**
- Create on ECS: `/etc/letsencrypt/live/inventory.tnsautoparts.com/`
- Create on ECS: `/etc/nginx/sites-available/tenaishi`
- Create on ECS: `/etc/nginx/sites-enabled/tenaishi`

**Interfaces:**
- Consumes: working DNS, open public ports `80`/`443`, a privately entered certificate contact email, and the single-domain Nginx template.
- Produces: valid HTTPS for the login page and API without exposing port `8000`.

- [ ] **Step 1: Reconfirm DNS and inbound reachability**

```bash
dig +short inventory.tnsautoparts.com A
nc -zv -G 5 47.98.121.142 80
nc -zv -G 5 47.98.121.142 443
```

Expected: DNS returns only `47.98.121.142`; ports are reachable once Nginx is active, or return `Connection refused` rather than time out before Nginx starts.

- [ ] **Step 2: Install Certbot and issue the certificate with a private contact-email prompt**

Run an interactive ECS shell, install Certbot, read the certificate email without placing it in chat or shell history, validate it is non-empty, and execute:

```bash
apt-get update -y
apt-get install -y certbot
read -r -p '证书联系邮箱：' CERT_EMAIL
test -n "${CERT_EMAIL:?}"
certbot certonly --standalone --non-interactive --agree-tos \
  --email "$CERT_EMAIL" \
  --deploy-hook 'systemctl reload nginx' \
  -d inventory.tnsautoparts.com
```

Expected: `/etc/letsencrypt/live/inventory.tnsautoparts.com/fullchain.pem` and `privkey.pem` exist, and `certbot renew --dry-run` succeeds.

- [ ] **Step 3: Render and enable the Nginx configuration**

```bash
ssh root@47.98.121.142 'set -eu; . /root/.config/tenaishi/deploy-target.env; envsubst '\''$TENAISHI_API_DOMAIN'\'' < /srv/tenaishi/app/deploy/nginx-personal-inventory.conf > /etc/nginx/sites-available/tenaishi; ln -sfn /etc/nginx/sites-available/tenaishi /etc/nginx/sites-enabled/tenaishi; rm -f /etc/nginx/sites-enabled/default; nginx -t; systemctl enable --now nginx'
```

Expected: `nginx -t` reports successful syntax and configuration tests.

- [ ] **Step 4: Verify redirect, HTTPS health, login protection, and closed port 8000**

```bash
curl -sSIL http://inventory.tnsautoparts.com
curl -fsS https://inventory.tnsautoparts.com/health
curl -sSIL https://inventory.tnsautoparts.com/admin
nc -zv -G 3 47.98.121.142 8000 && exit 1 || true
```

Expected: HTTP redirects to HTTPS, health is `ok`, `/admin` redirects to `/auth/login`, and public port `8000` is unreachable.

---

### Task 5: Cut over the trial computer's existing data

**Files:**
- Consume from trial ZIP: `data/app.db`
- Consume from trial ZIP: `data/uploads/`
- Consume if present: `data/previews/`
- Replace on ECS: `/srv/tenaishi/data/app.db`
- Replace on ECS: `/srv/tenaishi/data/uploads/`
- Replace on ECS if supplied: `/srv/tenaishi/data/previews/`

**Interfaces:**
- Consumes: one immutable ZIP produced after the Windows trial service is stopped.
- Produces: verified production database and drawing files under `/srv/tenaishi/data`.

- [ ] **Step 1: Freeze the trial source and collect the full data directory**

Have the trial user stop the inventory program, then archive the entire `杭州特耐时-backend\\data` directory. Record the ZIP SHA-256 locally and retain the original ZIP unchanged.

- [ ] **Step 2: Extract into a new temporary directory and validate structure**

Resolve the received attachment to one absolute ZIP path and assign it to `SOURCE_ARCHIVE`, then run in zsh:

```zsh
set -euo pipefail
: "${SOURCE_ARCHIVE:?先把附件的绝对路径赋给 SOURCE_ARCHIVE}"
[[ -f "$SOURCE_ARCHIVE" ]]
SOURCE_SHA256="$(shasum -a 256 -- "$SOURCE_ARCHIVE")"
STAGING_DIR="$(mktemp -d)"
[[ -n "$STAGING_DIR" && "$STAGING_DIR" == /var/folders/* ]]
unzip -q -- "$SOURCE_ARCHIVE" -d "$STAGING_DIR"
db_candidates=("$STAGING_DIR"/**/app.db(N))
(( ${#db_candidates[@]} == 1 ))
EXTRACTED_DATA_DIR="${db_candidates[1]:h}"
[[ -d "$EXTRACTED_DATA_DIR/uploads" ]]
print -r -- "$SOURCE_SHA256"
print -r -- "$EXTRACTED_DATA_DIR"
```

Expected: one SHA-256 line and one validated extraction-directory path. Keep that shell open so `EXTRACTED_DATA_DIR` remains available.

- [ ] **Step 3: Validate the trial SQLite database before upload**

Run against the extracted copy:

```bash
sqlite3 "${EXTRACTED_DATA_DIR:?}/app.db" 'PRAGMA integrity_check;'
sqlite3 "${EXTRACTED_DATA_DIR:?}/app.db" 'SELECT name FROM sqlite_master WHERE type="table" ORDER BY name;'
```

Expected: integrity output is exactly `ok`; the table list includes the existing inventory and transaction tables.

- [ ] **Step 4: Stop writes, preserve the temporary ECS database, and upload to a staging directory**

Upload only the validated extracted data to a new staging directory, reject symbolic links, then stop the service and move the pre-cutover data aside before installing the trial data:

```zsh
rsync -a --protect-args "${EXTRACTED_DATA_DIR:?}/" root@47.98.121.142:/srv/tenaishi/import-staging/
ssh root@47.98.121.142 '
set -euo pipefail
IMPORT_DIR=/srv/tenaishi/import-staging
test -f "${IMPORT_DIR:?}/app.db"
test -d "${IMPORT_DIR:?}/uploads"
test -z "$(find "$IMPORT_DIR" -type l -print -quit)"
test "$(sqlite3 "$IMPORT_DIR/app.db" "PRAGMA integrity_check;")" = ok
systemctl stop tenaishi.service
STAMP="$(date +%Y-%m-%d_%H%M%S)"
test -n "${STAMP:?}"
test -d /srv/tenaishi/data
mv /srv/tenaishi/data "/srv/tenaishi/pre-cutover-${STAMP}"
install -d -o tenaishi -g tenaishi -m 0750 /srv/tenaishi/data
rsync -a "$IMPORT_DIR/" /srv/tenaishi/data/
chown -R tenaishi:tenaishi /srv/tenaishi/data
find /srv/tenaishi/data -type d -exec chmod 0750 {} +
'
```

Expected: the old empty data remains recoverable in one timestamped `/srv/tenaishi/pre-cutover-*` directory; no file is deleted.

- [ ] **Step 5: Start the service to run repeatable migrations and inspect the migrated database**

```bash
ssh root@47.98.121.142 'systemctl start tenaishi.service; systemctl is-active tenaishi.service; curl -fsS http://127.0.0.1:8000/health; sqlite3 /srv/tenaishi/data/app.db "PRAGMA integrity_check;"'
```

Expected: service is active, health is `ok`, and integrity output is exactly `ok`.

- [ ] **Step 6: Compare source and production business counts**

For every inventory, transaction, drawing, raw-plate, paper-material, and scrap table present in the source database, compare `COUNT(*)` between the extracted source and ECS database. Expected: migrations may add tables or columns, but no source business-table row count decreases.

---

### Task 6: Create the owner, validate backups, and accept the PC system

> 2026-08-20 更新：本节原有“创建唯一 owner 并长期使用密码/TOTP”的步骤已被微信管理员登录方案取代。旧 `owner` 只作为阶段 A 的临时应急入口；正式主管理员使用 `scripts/manage_superadmin.py bootstrap` 创建并绑定微信。只有真实主管理员和老板均完成扫码登录后，才进入阶段 B：停用临时 owner、设置 `LEGACY_PASSWORD_LOGIN_ENABLED=false`，并验证 `/auth/legacy-login` 返回 404。准确顺序、备份和恢复命令以 `deploy/README.md` 的“微信管理员登录两阶段切换”为准。

**Files:**
- Modify on ECS database: owner account and authentication records only.
- Create on ECS: one verified backup bundle under `/srv/tenaishi/backups/`.

**Interfaces:**
- Consumes: migrated production database, privately chosen owner password, and private TOTP enrollment.
- Produces: usable owner login and a proven recovery path.

- [ ] **Step 1: Create the unique owner account interactively after data cutover**

```bash
ssh -t root@47.98.121.142 'cd /srv/tenaishi/app && runuser -u tenaishi -- .venv/bin/python scripts/create_owner.py --username owner --display-name 老板'
```

Expected: the owner enters a password of at least 12 characters twice, the command prints one TOTP provisioning URI once, and the owner immediately saves it in an authenticator app without posting it in chat.

- [ ] **Step 2: Test owner login and protected pages in a browser**

Open `https://inventory.tnsautoparts.com/auth/login`, sign in with username, password, and six-digit TOTP. Confirm `/admin`, inventory, transactions, drawings, reports, and employee management are accessible; confirm logout invalidates the session.

- [ ] **Step 3: Run a production backup and non-destructive restore drill**

```bash
ssh root@47.98.121.142 'systemctl start tenaishi-backup.service; systemctl is-active tenaishi-backup.timer; latest=$(find /srv/tenaishi/backups -mindepth 1 -maxdepth 1 -type d -name "20??-??-??_??????" | sort | tail -1); test -n "$latest"; runuser -u tenaishi -- /srv/tenaishi/app/.venv/bin/python /srv/tenaishi/app/scripts/restore_backup.py "$latest"; target=$(mktemp -d)/restored; runuser -u tenaishi -- /srv/tenaishi/app/.venv/bin/python /srv/tenaishi/app/scripts/restore_backup.py "$latest" --target "$target"; sqlite3 "$target/app.db" "PRAGMA integrity_check;"'
```

Expected: backup verification succeeds, temporary restore succeeds, integrity output is `ok`, and production data is not overwritten.

- [ ] **Step 4: Record acceptance without secrets**

Record the deployed Git commit, certificate expiry date, source ZIP SHA-256, source/production row-count comparison, backup bundle path, restore result, and pass/fail for owner login. Do not record passwords, TOTP URI, AppSecret, cookies, or tokens.

---

### Task 7: Configure and publish the employee mini program

**Files:**
- Create locally: `$HOME/.config/tenaishi/deploy-target.env`
- Create locally: `dist/miniprogram-release/`
- Modify on ECS: `/etc/tenaishi/tenaishi.env` with the privately retrieved WeChat AppSecret.

**Interfaces:**
- Consumes: AppID `wxc9c29ffe2999dff6`, private AppSecret, HTTPS inventory domain, owner-created employee accounts, and WeChat platform approval.
- Produces: a release build fixed to `https://inventory.tnsautoparts.com` and approved for employee use.

- [ ] **Step 1: Confirm control of the matching WeChat mini-program account**

Log in to the WeChat public platform for AppID `wxc9c29ffe2999dff6`. Retrieve AppSecret privately, set it only in `/etc/tenaishi/tenaishi.env`, restart `tenaishi.service`, and verify no secret appears in shell history or logs.

- [ ] **Step 2: Configure the request legal domain**

Add exactly `https://inventory.tnsautoparts.com` to the mini program's request legal domains. Do not add an IP address, HTTP URL, path, or port `8000`.

- [ ] **Step 3: Build the fail-closed release directory**

Create the local target file with:

```text
TENAISHI_PUBLIC_IP=47.98.121.142
TENAISHI_API_DOMAIN=inventory.tnsautoparts.com
```

Then run:

```bash
.venv/bin/python scripts/build_miniprogram_release.py \
  --target-file "$HOME/.config/tenaishi/deploy-target.env" \
  --output dist/miniprogram-release
node --test tests/*.test.js
```

Expected: `dist/miniprogram-release/release-config.js` contains `https://inventory.tnsautoparts.com`; JavaScript tests pass.

- [ ] **Step 4: Complete experience testing and WeChat release gates**

Import `dist/miniprogram-release` into WeChat Developer Tools. Test employee activation, repeat login, plan, steel, paper, products, scraps, inbound, outbound, transaction history, reversal, disabled-user rejection, and idempotent retry. Complete mini-program filing and privacy declaration, upload code, submit review, and publish only after approval.

- [ ] **Step 5: Perform final production smoke tests**

Run `deploy/smoke-test.sh`, then confirm owner PC login and one employee mini-program login against the same production database. Expected: local and HTTPS health pass, public `8000` remains closed, and changes made from one client appear in the other after refresh.

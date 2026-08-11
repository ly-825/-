# SQLite Backup and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace live-file copying with consistent SQLite online backups, automatic retention, systemd scheduling, and a verified non-destructive restore workflow.

**Architecture:** A focused Python module uses `sqlite3.Connection.backup` and `PRAGMA integrity_check` to create a consistent snapshot while the application runs. A shell wrapper locks concurrent runs, copies uploads, writes a manifest, and prunes local backups. A systemd timer runs daily, while Alibaba Cloud ECS File Backup stores the generated backup directory off the application disk.

**Tech Stack:** Python 3 sqlite3, Bash, systemd timer, rsync, unittest, Alibaba Cloud ECS File Backup.

## Global Constraints

- Source database is SQLite; do not introduce MySQL or RDS in this phase.
- Never copy the live `data/app.db` with plain `cp` as the production database-backup mechanism.
- Local retention is 7 days; cloud retention is 30 days.
- Restore drills use a temporary target and never overwrite production data.
- Preserve pre-existing uncommitted files and current inventory data.
- Use TDD and commit after every task.

---

## File Map

- `app/backup.py`: online backup, verification, manifest, and restore primitives.
- `scripts/backup.py`: executable backup CLI.
- `scripts/restore_backup.py`: explicit, guarded restore CLI.
- `scripts/backup.sh`: locking wrapper and stable systemd entry point.
- `deploy/tenaishi-backup.service`: oneshot backup job.
- `deploy/tenaishi-backup.timer`: daily schedule.
- `tests/test_backup.py`: database consistency, retention, and restore tests.
- `README.md`, `deploy/README.md`: operator instructions.

### Task 1: Consistent SQLite Backup Primitive

**Files:**
- Create: `app/backup.py`
- Create: `tests/test_backup.py`

**Interfaces:**
- Produces `backup_sqlite(source: Path, destination: Path) -> BackupResult`.
- Produces `verify_sqlite(path: Path) -> None`.

- [ ] **Step 1: Write failing online-backup tests**

Use this complete committed-row pattern:

```python
def test_online_backup_contains_committed_rows(self) -> None:
    source = self.root / "source.db"
    target = self.root / "target.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE stock (id INTEGER PRIMARY KEY, quantity INTEGER)")
        connection.execute("INSERT INTO stock(quantity) VALUES (7)")
    backup_sqlite(source, target)
    with sqlite3.connect(target) as connection:
        self.assertEqual(connection.execute("SELECT quantity FROM stock").fetchone()[0], 7)
```

Additional tests must keep an uncommitted writer open and prove that row is excluded, reject corrupt input during verification, and reject an already-existing destination.

The uncommitted-row test must keep a writer connection open while the backup runs from a second connection.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_backup -v`

Expected: missing `app.backup` module.

- [ ] **Step 3: Implement the primitive**

Use atomic temporary output and SQLite's online API:

```python
def backup_sqlite(source: Path, destination: Path) -> BackupResult:
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(temporary)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    verify_sqlite(temporary)
    temporary.replace(destination)
    return BackupResult(path=destination, size=destination.stat().st_size)
```

`verify_sqlite` requires `PRAGMA integrity_check` to return exactly `ok`.

- [ ] **Step 4: Run tests and commit**

Run: `.venv/bin/python -m unittest tests.test_backup -v`

Expected: PASS.

```bash
git add app/backup.py tests/test_backup.py
git commit -m "feat: create consistent SQLite backups"
```

### Task 2: Backup Bundle, Upload Copy, and Retention

**Files:**
- Create: `scripts/backup.py`
- Modify: `scripts/backup.sh`
- Modify: `tests/test_backup.py`

**Interfaces:**
- Produces a timestamped directory containing `app.db`, `uploads/`, and `manifest.json`.
- Produces CLI arguments `--database`, `--uploads`, `--backup-root`, and `--retention-days`.

- [ ] **Step 1: Add failing bundle and retention tests**

Create one source database, one upload file, three old timestamp directories, and one unrelated directory in the test fixture. Assert that a completed bundle contains a verified database, copied upload, and matching manifest hash; a failed backup leaves no completed bundle; retention removes only timestamped direct children older than seven days; and an unrelated directory inside or outside the root remains untouched.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_backup -v`

Expected: missing bundle functions.

- [ ] **Step 3: Implement the CLI and safe shell wrapper**

The manifest must include:

```json
{
  "format_version": 1,
  "created_at": "2026-08-11T02:00:00+08:00",
  "database": "app.db",
  "database_sha256": "hex-digest",
  "database_size": 12345,
  "uploads": "uploads"
}
```

Use `shutil.copytree` for uploads. Write into a `.partial-*` directory and rename only after database verification and manifest creation. Retention may delete only direct children matching `YYYY-MM-DD_HHMMSS`.

Replace `scripts/backup.sh` with a lock wrapper:

```bash
exec 9>"${BACKUP_ROOT}/.backup.lock"
flock -n 9 || { echo "backup already running" >&2; exit 75; }
exec "$PYTHON_BIN" scripts/backup.py \
  --database "$DATABASE_PATH" \
  --uploads "$UPLOAD_DIR" \
  --backup-root "$BACKUP_ROOT" \
  --retention-days "${BACKUP_RETENTION_DAYS:-7}"
```

- [ ] **Step 4: Run tests and an isolated CLI smoke test**

Run:

```bash
.venv/bin/python -m unittest tests.test_backup -v
tmpdir=$(mktemp -d)
.venv/bin/python scripts/backup.py --database data/app.db --uploads data/uploads --backup-root "$tmpdir" --retention-days 7
find "$tmpdir" -maxdepth 2 -type f -print
```

Expected: one timestamped bundle with `app.db` and `manifest.json`; no `.partial-*` directory.

- [ ] **Step 5: Commit**

```bash
git add scripts/backup.py scripts/backup.sh tests/test_backup.py
git commit -m "feat: automate backup bundles and retention"
```

### Task 3: Guarded Restore and Monthly Drill

**Files:**
- Create: `scripts/restore_backup.py`
- Modify: `app/backup.py`
- Modify: `tests/test_backup.py`

**Interfaces:**
- Produces `validate_bundle(path: Path) -> BackupManifest`.
- Produces a restore CLI that defaults to verification-only.

- [ ] **Step 1: Add failing restore tests**

Test valid bundle verification, hash mismatch rejection, corrupt database rejection, default no-write behavior, refusal to overwrite an existing target without `--replace`, and restoration into a fresh temporary directory.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_backup -v`

Expected: missing restore interfaces.

- [ ] **Step 3: Implement guarded restore**

CLI behavior:

```text
scripts/restore_backup.py BUNDLE                 verify only
scripts/restore_backup.py BUNDLE --target DIR    restore to an empty directory
scripts/restore_backup.py BUNDLE --target DIR --replace --confirm RESTORE
```

`--replace` must require the literal confirmation `RESTORE`. Before replacing, move the existing target to a sibling directory named with the concrete pattern `pre-restore-2026-08-11_020000` rather than deleting it.

- [ ] **Step 4: Run tests and a temporary restore drill**

Run:

```bash
.venv/bin/python -m unittest tests.test_backup -v
bundle=$(find /tmp/tenaishi-backup-smoke -mindepth 1 -maxdepth 1 -type d | sort | tail -1)
target=$(mktemp -d)/restored
.venv/bin/python scripts/restore_backup.py "$bundle" --target "$target"
.venv/bin/python -c "import sqlite3; c=sqlite3.connect('$target/app.db'); print(c.execute('PRAGMA integrity_check').fetchone()[0])"
```

Expected: final command prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add app/backup.py scripts/restore_backup.py tests/test_backup.py
git commit -m "feat: add verified backup restoration"
```

### Task 4: systemd Schedule and Alibaba File Backup Integration

**Files:**
- Create: `deploy/tenaishi-backup.service`
- Create: `deploy/tenaishi-backup.timer`
- Modify: `deploy/setup-server.sh`
- Modify: `deploy/README.md`
- Modify: `README.md`
- Create: `tests/test_linux_backup_assets.py`

**Interfaces:**
- Produces a daily `02:00 Asia/Shanghai` oneshot backup timer.
- Documents Alibaba file-backup paths `/srv/tenaishi/backups` and `/srv/tenaishi/data/uploads` with 30-day retention.

- [ ] **Step 1: Write failing deployment-asset tests**

Assert the service uses `Type=oneshot`, the timer contains `OnCalendar=*-*-* 02:00:00`, `Persistent=true`, and setup enables `tenaishi-backup.timer`. Assert docs do not recommend copying live `app.db`.

- [ ] **Step 2: Run and confirm failure**

Run: `.venv/bin/python -m unittest tests.test_linux_backup_assets -v`

Expected: missing timer files.

- [ ] **Step 3: Add service and timer**

Service essentials:

```ini
[Service]
Type=oneshot
User=tenaishi
Group=tenaishi
WorkingDirectory=/srv/tenaishi/app
EnvironmentFile=/etc/tenaishi/tenaishi.env
ExecStart=/srv/tenaishi/app/scripts/backup.sh
UMask=0077
```

Timer essentials:

```ini
[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true
RandomizedDelaySec=300
```

- [ ] **Step 4: Document cloud policy and operator checks**

The Alibaba ECS File Backup job must include the generated backup directory and uploads, use 30-day retention, and show a successful record with nonzero bytes. Add exact checks:

```bash
systemctl list-timers tenaishi-backup.timer
systemctl start tenaishi-backup.service
journalctl -u tenaishi-backup.service -n 100 --no-pager
find /srv/tenaishi/backups -maxdepth 2 -type f -print
```

- [ ] **Step 5: Run tests and commit**

Run: `.venv/bin/python -m unittest tests.test_backup tests.test_linux_backup_assets -v`

Expected: PASS.

```bash
git add deploy/tenaishi-backup.service deploy/tenaishi-backup.timer deploy/setup-server.sh deploy/README.md README.md tests/test_linux_backup_assets.py
git commit -m "feat: schedule and document cloud backups"
```

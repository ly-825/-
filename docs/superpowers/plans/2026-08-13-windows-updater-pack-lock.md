# Windows Updater Pack-Lock Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Windows one-click updater update only `origin/main` without triggering fetch-time automatic repository maintenance.

**Architecture:** Keep the existing backup and dirty-worktree protections. Add an early `main` branch guard, replace `git pull --ff-only` with an explicit maintenance-free fetch followed by a fast-forward-only merge, and encode the contract in the existing Windows script test module.

**Tech Stack:** Windows Batch, Git 2.29+, Python 3 `unittest`

## Global Constraints

- The updater must only update remote `origin/main`.
- The target computer must use Git 2.29 or newer so `--no-auto-maintenance` is available.
- Do not reset, stash, force-checkout, delete, or overwrite local program changes.
- Do not globally disable Git maintenance and do not pipe `y` into Git prompts.
- Do not modify business code, databases, backup contents, or Git configuration.
- The macOS test environment cannot prove Windows file-lock behavior; final acceptance requires one real update on the affected Windows computer.

---

### Task 1: Encode the updater safety contract

**Files:**
- Modify: `tests/test_windows_update_scripts.py`

**Interfaces:**
- Consumes: UTF-8 text from `一键更新程序.bat` through the existing `read_text(path)` helper.
- Produces: regression assertions for the `main` guard, dirty-worktree ordering, maintenance-free fetch, fast-forward merge, and prohibited legacy commands.

- [ ] **Step 1: Replace the obsolete pull assertion with the full safety contract**

Update `test_update_script_contains_safe_update_flow` so its Git assertions read:

```python
        branch_guard = script.index('if /I not "%CURRENT_BRANCH%"=="main"')
        backup = script.index('set "BACKUP_DIR=backups\\%BACKUP_TIME%"')
        dirty_check = script.index("git status --porcelain")
        fetch = script.index("git fetch --no-auto-maintenance origin main")
        merge = script.index("git merge --ff-only FETCH_HEAD")

        self.assertIn('set "CURRENT_BRANCH="', script)
        self.assertIn("git branch --show-current", script)
        self.assertLess(branch_guard, backup)
        self.assertLess(dirty_check, fetch)
        self.assertLess(fetch, merge)
        self.assertNotIn("git pull --ff-only", script)
        self.assertNotIn("echo y | git fetch", script.lower())
```

Retain the existing project-root, backup, virtual-environment, Uvicorn, and wait-prompt assertions.

- [ ] **Step 2: Run the focused test and confirm it fails**

Run:

```bash
.venv/bin/python -m unittest tests.test_windows_update_scripts.WindowsUpdateScriptTests.test_update_script_contains_safe_update_flow -v
```

Expected: FAIL because the current script has no `main` guard and still contains `git pull --ff-only`.

- [ ] **Step 3: Commit the failing regression test**

```bash
git add tests/test_windows_update_scripts.py
git commit -m "test: define safe Windows updater flow"
```

### Task 2: Implement maintenance-free main updates

**Files:**
- Modify: `一键更新程序.bat:27-91`

**Interfaces:**
- Consumes: current Git branch, clean-worktree status, and remote `origin/main`.
- Produces: a fast-forward-only local `main` update through `FETCH_HEAD`, or exit code `1` with a specific operator message.

- [ ] **Step 1: Add the early formal-branch guard**

Immediately after the Git availability check and before Python detection or backup creation, add:

```bat
set "CURRENT_BRANCH="
for /f "delims=" %%i in ('git branch --show-current') do set "CURRENT_BRANCH=%%i"

if /I not "%CURRENT_BRANCH%"=="main" (
    echo.
    echo 更新失败：当前不是 main 正式分支，请联系管理员处理。
    echo.
    call :wait "按回车关闭窗口..."
    exit /b 1
)
```

An empty branch name, including detached HEAD, follows the same refusal path.

- [ ] **Step 2: Replace pull with fetch and fast-forward merge**

Replace the existing `git pull --ff-only` block with:

```bat
echo 正在拉取最新代码...
git fetch --no-auto-maintenance origin main
if errorlevel 1 (
    echo.
    echo 更新失败：下载代码失败。请检查网络，或联系管理员处理。
    echo.
    call :wait "按回车关闭窗口..."
    exit /b 1
)

git merge --ff-only FETCH_HEAD
if errorlevel 1 (
    echo.
    echo 更新失败：本地代码无法安全快进到正式版本，请联系管理员处理。
    echo.
    call :wait "按回车关闭窗口..."
    exit /b 1
)
```

- [ ] **Step 3: Run the focused Windows script tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_windows_update_scripts -v
```

Expected: 2 tests pass.

- [ ] **Step 4: Verify Git command semantics in an isolated repository**

Create temporary bare, source, and deployed repositories with `mktemp -d`; configure a test-only identity in the source and deployed repositories. Verify these two cases with the exact production commands:

```bash
git fetch --no-auto-maintenance origin main
git merge --ff-only FETCH_HEAD
```

Expected:

- A remote-only commit fast-forwards the deployed `main` and returns exit code `0`.
- Divergent local and remote commits cause `git merge --ff-only FETCH_HEAD` to return nonzero without replacing the local commit.

- [ ] **Step 5: Run regression and repository checks**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
git diff --check
git status --short
```

Expected: all Python tests pass; `git diff --check` prints nothing; status contains only the intended batch-script modification after the test commit.

- [ ] **Step 6: Commit the implementation**

```bash
git add "一键更新程序.bat"
git commit -m "fix: avoid fetch-time maintenance in Windows updater"
```

- [ ] **Step 7: Audit final scope**

Run:

```bash
git diff --stat origin/main...HEAD
git log --oneline origin/main..HEAD
git status --short --branch
```

Expected: the branch is clean and contains only the design, plan, test-contract, and updater implementation commits. Do not push or merge without explicit user authorization.

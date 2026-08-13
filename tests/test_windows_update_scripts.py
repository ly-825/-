from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class WindowsUpdateScriptTests(unittest.TestCase):
    def test_update_script_contains_safe_update_flow(self):
        script = read_text(PROJECT_ROOT / "一键更新程序.bat")

        self.assertIn('cd /d "%~dp0"', script)
        self.assertIn('if not exist ".git"', script)
        self.assertIn("git status --porcelain", script)
        self.assertIn("data\\app.db", script)
        self.assertIn("data\\uploads", script)

        self.assertIn('set "CURRENT_BRANCH="', script)
        self.assertIn("git branch --show-current", script)
        self.assertIn('if /I not "%CURRENT_BRANCH%"=="main"', script)
        self.assertIn("git fetch --no-auto-maintenance origin main", script)
        self.assertIn("git merge --ff-only FETCH_HEAD", script)

        branch_guard = script.index('if /I not "%CURRENT_BRANCH%"=="main"')
        backup = script.index('set "BACKUP_DIR=backups\\%BACKUP_TIME%"')
        dirty_check = script.index("git status --porcelain")
        fetch = script.index("git fetch --no-auto-maintenance origin main")
        merge = script.index("git merge --ff-only FETCH_HEAD")
        dependency_update = script.index('if not exist ".venv\\Scripts\\python.exe"')

        fetch_failure_block = script[fetch:merge]
        merge_failure_block = script[merge:dependency_update]

        self.assertLess(branch_guard, backup)
        self.assertLess(dirty_check, fetch)
        self.assertLess(fetch, merge)
        self.assertIn("if errorlevel 1", fetch_failure_block)
        self.assertIn("exit /b 1", fetch_failure_block)
        self.assertIn("if errorlevel 1", merge_failure_block)
        self.assertIn("exit /b 1", merge_failure_block)
        self.assertNotIn("git pull --ff-only", script)
        self.assertNotIn("echo y | git fetch", script.lower())
        self.assertIn('".venv\\Scripts\\python.exe" -m pip install -r requirements.txt', script)
        self.assertIn("uvicorn app.main:app --host 0.0.0.0 --port 8000", script)
        self.assertNotIn("\npause", script.lower())
        self.assertIn("按回车开始更新", script)
        self.assertIn("按回车关闭窗口", script)

    def test_start_script_runs_backend_from_project_root(self):
        script = read_text(PROJECT_ROOT / "启动后台服务.bat")

        self.assertIn('cd /d "%~dp0"', script)
        self.assertIn('if not exist ".venv\\Scripts\\python.exe"', script)
        self.assertIn(
            '".venv\\Scripts\\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000',
            script,
        )

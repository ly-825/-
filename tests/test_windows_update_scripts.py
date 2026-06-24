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
        self.assertIn("git pull --ff-only", script)
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

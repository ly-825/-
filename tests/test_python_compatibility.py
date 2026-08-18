import io
from pathlib import Path
import tokenize
import unittest


class PythonCompatibilityTests(unittest.TestCase):
    def test_f_strings_do_not_reuse_their_quote_inside_expressions(self):
        app_dir = Path(__file__).resolve().parents[1] / "app"
        for source_path in sorted(app_dir.rglob("*.py")):
            with self.subTest(source=str(source_path.relative_to(app_dir.parent))):
                source = source_path.read_text(encoding="utf-8")
                quote_stack = []
                incompatible_lines = []
                for token in tokenize.generate_tokens(io.StringIO(source).readline):
                    if token.type == tokenize.FSTRING_START:
                        quote_stack.append(token.string.lstrip("rRuUbBfF"))
                    elif token.type == tokenize.FSTRING_END:
                        quote_stack.pop()
                    elif token.type == tokenize.STRING and quote_stack:
                        if token.string.lstrip("rRuUbBfF").startswith(quote_stack[-1]):
                            incompatible_lines.append(token.start[0])
                self.assertEqual(
                    incompatible_lines,
                    [],
                    "same-quote strings inside f-string expressions require Python 3.12",
                )


if __name__ == "__main__":
    unittest.main()

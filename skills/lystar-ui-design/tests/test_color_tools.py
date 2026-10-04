import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "color_tools.py"
spec = importlib.util.spec_from_file_location("color_tools", SCRIPT)
color_tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(color_tools)


class ContrastTest(unittest.TestCase):
    def check(self, foreground, background, **extra):
        return color_tools.check_pair({"foreground": foreground, "background": background, **extra})

    def test_black_white_and_identical(self):
        self.assertEqual(self.check("#000", "#fff")["ratio"], 21)
        self.assertEqual(self.check("#123456", "#123456")["ratio"], 1)

    def test_alpha_is_composited_before_luminance(self):
        self.assertEqual(self.check("rgba(0,0,0,.6)", "#fff"), self.check("#666666", "#fff"))
        self.assertEqual(self.check("#ffffff", "rgba(0,0,0,.6)", canvas="#ffffff"), self.check("#ffffff", "#666666"))
        for notation in ("#0009", "#00000099", "rgb(0 0 0 / 60%)", "rgba(0%,0%,0%,60%)"):
            self.assertEqual(self.check(notation, "#fff"), self.check("#666", "#fff"))

    def test_transparency_needs_real_canvas(self):
        with self.assertRaises(ValueError):
            self.check("#fff", "#0009")
        with self.assertRaises(ValueError):
            self.check("#fff", "#0009", canvas="transparent")

    def test_bright_brand_does_not_imply_readable_white_text(self):
        result = self.check("#fff", "#07c160")
        self.assertAlmostEqual(result["ratio"], 2.3848, places=4)
        self.assertFalse(result["passes"])
        self.assertFalse(self.check("#fff", "#366ef4")["passes"])
        self.assertTrue(self.check("#fff", "#0052d9")["passes"])

    def test_does_not_round_before_deciding(self):
        result = self.check("#fff", "#666", minimum=5.74181)
        self.assertEqual(result["ratio"], 5.7418)
        self.assertTrue(result["passes"])
        self.assertFalse(self.check("#fff", "#666", minimum=5.74184)["passes"])

    def test_invalid_and_unsupported_values(self):
        for value in ("var(--brand)", "oklch(50% .1 240)", "#12", "rgb(300,0,0)", "rgba(0,0,0,nan)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check(value, "#fff")
        with self.assertRaises(ValueError):
            self.check("#000", "#fff", minimum=float("nan"))

    def test_cli_exit_codes_and_stdin(self):
        def run(pairs):
            return subprocess.run([sys.executable, str(SCRIPT), "check", "--pairs", "-"], input=json.dumps(pairs), text=True, capture_output=True, timeout=5)
        self.assertEqual(run([{"foreground": "#000", "background": "#fff"}]).returncode, 0)
        self.assertEqual(run([{"foreground": "#fff", "background": "#07c160"}]).returncode, 1)
        self.assertEqual(run([]).returncode, 2)
        self.assertEqual(run([{"foreground": "#000"}]).returncode, 2)


if __name__ == "__main__":
    unittest.main()

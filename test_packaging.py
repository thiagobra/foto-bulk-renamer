"""Checks on the things that only bite on Windows, or only bite at build time.

None of this can be proven by running the app on Linux, so it is asserted
about the files instead. Run with:  python -m unittest test_packaging -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BATCH_FILES = ["run.bat", "build_exe.bat"]


class TestBatchFiles(unittest.TestCase):
    """cmd.exe is the last interpreter alive that still cares about CRLF.

    A .bat checked out with bare LF can mis-parse the multi-line ( ... )
    blocks these scripts are built from, and the user just sees the window
    close. .gitattributes forces CRLF; this proves it stayed forced.
    """

    def test_batch_files_use_crlf_line_endings(self):
        for name in BATCH_FILES:
            with self.subTest(file=name):
                data = (ROOT / name).read_bytes()
                self.assertNotIn(data.replace(b"\r\n", b""), (b"",), "file is empty")
                bare_lf = data.replace(b"\r\n", b"").count(b"\n")
                self.assertEqual(bare_lf, 0,
                                 f"{name} has {bare_lf} LF-only line endings")

    def test_batch_files_have_balanced_parentheses(self):
        """An unbalanced block is the classic way a .bat dies silently."""
        for name in BATCH_FILES:
            with self.subTest(file=name):
                depth = 0
                for number, line in enumerate((ROOT / name).read_text().splitlines(), 1):
                    stripped = line.strip()
                    if stripped.upper().startswith("REM ") or stripped.startswith("::"):
                        continue
                    # ^ is cmd's line-continuation character, not a paren.
                    depth += stripped.count("(") - stripped.count(")")
                    self.assertGreaterEqual(depth, 0,
                                            f"{name}:{number} closes a block that never opened")
                self.assertEqual(depth, 0, f"{name} leaves a ( block unclosed")

    def test_every_exit_path_pauses_so_the_error_can_be_read(self):
        """Double-clicked from Explorer, a .bat window vanishes on exit. Every
        failure branch must pause first or the user sees nothing at all."""
        for name in BATCH_FILES:
            with self.subTest(file=name):
                lines = [line.strip() for line in (ROOT / name).read_text().splitlines()]
                for index, line in enumerate(lines):
                    if not re.fullmatch(r"exit /b [1-9]\d*", line, re.IGNORECASE):
                        continue
                    window = [text.lower() for text in lines[max(0, index - 4):index]]
                    self.assertIn("pause", window,
                                  f"{name}: '{line}' exits without a pause above it")


class TestBuildScript(unittest.TestCase):

    def setUp(self):
        self.text = (ROOT / "build_exe.bat").read_text()

    def test_the_icon_the_build_points_at_exists(self):
        self.assertIn(r"assets\icon.ico", self.text)
        self.assertTrue((ROOT / "assets" / "icon.ico").is_file())

    def test_assets_are_bundled_so_the_running_app_can_find_its_icon(self):
        """--icon only decorates the .exe in Explorer; the window icon needs
        the file to actually be inside the bundle."""
        self.assertIn('--add-data "assets;assets"', self.text)

    def test_packages_pyinstaller_cannot_discover_are_collected(self):
        """These three hide their real payload in data files or binaries, so
        reading the import statements does not find them."""
        for package in ("sv_ttk", "tkinterdnd2", "pillow_heif"):
            with self.subTest(package=package):
                self.assertIn(f"--collect-all {package}", self.text)


class TestDeclaredDependencies(unittest.TestCase):

    def test_requirements_covers_every_third_party_import(self):
        required = (ROOT / "requirements.txt").read_text().lower()
        # import name -> distribution name on PyPI
        for module, distribution in (("PIL", "pillow"), ("sv_ttk", "sv-ttk"),
                                     ("tkinterdnd2", "tkinterdnd2"),
                                     ("pillow_heif", "pillow-heif")):
            with self.subTest(module=module):
                self.assertIn(distribution, required)

    def test_heic_support_is_a_declared_dependency_not_a_hope(self):
        """The README advertises .heic. Pillow alone cannot open one."""
        self.assertIn("pillow-heif", (ROOT / "requirements.txt").read_text())


class TestLicenceAndCI(unittest.TestCase):

    def test_the_project_has_a_licence(self):
        self.assertTrue((ROOT / "LICENSE").is_file())

    def test_ci_runs_the_tests_on_windows_too(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        self.assertIn("windows-latest", workflow)
        self.assertIn("unittest", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)

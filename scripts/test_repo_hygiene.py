import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import check_repo_hygiene as hygiene


class RepoHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        subprocess.run(["git", "init", "-q", self.root], check=True)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", self.root, "add", "--", relative], check=True)

    def run_main(self, args: list[str]) -> tuple[int, str, str]:
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = hygiene.main(args)
        return result, output.getvalue(), errors.getvalue()

    def run_check(self) -> tuple[int, str]:
        result, output, errors = self.run_main(["--root", str(self.root)])
        self.assertEqual(errors, "")
        return result, output

    def test_accepts_local_markdown_targets_and_ignores_fenced_examples(self) -> None:
        self.write("docs/guide.md", "# guide\n")
        self.write("docs/with space.md", "# guide\n")
        self.write("assets/cover.png", "not an image\n")
        self.write("docs/nested/README.md", "[parent](../guide.md) ![cover](../../assets/cover.png)\n")
        self.write(
            "README.md",
            "[guide](docs/guide.md#section) [space](docs/with space.md) [dir](docs/) [external](https://example.com) [route](/docs/guide) [here](#top)\n"
            "[reference]: <docs/guide.md>\n"
            "```md\n[example](missing.md)\n```\n",
        )

        status, output = self.run_check()

        self.assertEqual(status, 0, output)
        self.assertEqual(output, "")

    def test_reports_missing_markdown_targets_including_url_escaped_reference(self) -> None:
        self.write("README.md", "[broken](missing.md)\n[other]: docs/missing%20file.md\n![image](missing.png)\n")

        status, output = self.run_check()

        self.assertEqual(status, 1)
        self.assertEqual(output.splitlines(), [
            "README.md:1: missing-markdown-target",
            "README.md:2: missing-markdown-target",
            "README.md:3: missing-markdown-target",
        ])

    def test_reports_long_source_without_reading_a_secret_value_to_output(self) -> None:
        fake_token = "gh" + "p_" + "x" * 36
        user_root = "/" + "Users" + "/alice"
        self.write("app.py", "\n".join(["pass"] * 1501) + "\n")
        self.write("config.py", f"token = '{fake_token}'\npath = '{user_root}/Library'\n")

        status, output = self.run_check()

        self.assertEqual(status, 1)
        self.assertIn("app.py:1501: source-over-1500-lines", output)
        self.assertIn("config.py:1: probable-secret", output)
        self.assertIn("config.py:2: personal-absolute-path", output)
        self.assertNotIn(fake_token, output)
        self.assertNotIn(user_root, output)

    def test_distinguishes_jellyfin_routes_from_workstation_paths(self) -> None:
        user_root = "/" + "Users" + "/alice"
        docker_root = "/" + "home" + "/service"
        self.write(
            "routes.py",
            f"item = '{user_root}/Items/123/Password'\n"
            f"workspace = '{user_root}/Documents/project'\n"
            f"cache = '{docker_root}/.cache/vendor'\n",
        )

        status, output = self.run_check()

        self.assertEqual(status, 1)
        self.assertEqual(output, "routes.py:2: personal-absolute-path\n")

    def test_ignores_templates_and_non_selected_suffixes(self) -> None:
        self.write("notes.txt", "/Users/{id}/Library\n")
        self.write("asset.bin", "gh" + "p_" + "x" * 36)

        status, output = self.run_check()

        self.assertEqual(status, 0, output)

    def test_reports_missing_tracked_files_and_can_check_a_sparse_subset(self) -> None:
        self.write("available.py", "pass\n")
        self.write("missing.py", "pass\n")
        (self.root / "missing.py").unlink()

        status, output = self.run_check()

        self.assertEqual(status, 1)
        self.assertEqual(output, "missing.py:0: missing-tracked-file\n")
        self.assertEqual(hygiene.check(self.root, {Path("available.py")}), [])

        status, output, errors = self.run_main([
            "--root", str(self.root), "--path", "missing.py",
        ])
        self.assertEqual(status, 1)
        self.assertEqual(output, "missing.py:0: missing-tracked-file\n")
        self.assertEqual(errors, "")

    def test_cli_rejects_untracked_or_absolute_selection_without_echoing_it(self) -> None:
        self.write("available.py", "pass\n")
        for selected in ("typo.py", str(self.root / "available.py")):
            with self.subTest(selected=selected):
                status, output, errors = self.run_main([
                    "--root", str(self.root), "--path", selected,
                ])
                self.assertEqual(status, 2)
                self.assertEqual(output, "")
                self.assertEqual(errors, "invalid --path selection\n")

        status, output, errors = self.run_main([
            "--root", str(self.root), "--path", "available.py",
        ])
        self.assertEqual(status, 0)
        self.assertEqual(output, "")
        self.assertEqual(errors, "")

    def test_does_not_read_symlinks_resolving_outside_the_repository(self) -> None:
        outside_dir = tempfile.TemporaryDirectory()
        self.addCleanup(outside_dir.cleanup)
        outside = Path(outside_dir.name) / "outside.py"
        fake_token = "gh" + "p_" + "x" * 36
        outside.write_text(f"token = '{fake_token}'\n", encoding="utf-8")
        os.symlink(outside, self.root / "linked.py")
        subprocess.run(["git", "-C", self.root, "add", "--", "linked.py"], check=True)

        status, output = self.run_check()

        self.assertEqual(status, 0, output)
        self.assertEqual(output, "")


if __name__ == "__main__":
    unittest.main()

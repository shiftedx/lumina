from pathlib import Path
import unittest

from bootstrap import locked_dependency_commands, virtualenv_python


class VirtualenvPythonTests(unittest.TestCase):
    def test_uses_posix_virtualenv_layout(self) -> None:
        self.assertEqual(
            virtualenv_python(Path("backend/.venv"), platform="posix"),
            Path("backend/.venv/bin/python"),
        )

    def test_uses_windows_virtualenv_layout(self) -> None:
        self.assertEqual(
            virtualenv_python(Path("backend/.venv"), platform="nt"),
            Path("backend/.venv/Scripts/python.exe"),
        )


class LockedDependencyCommandTests(unittest.TestCase):
    def test_installs_exactly_backend_venv_and_frontend_and_never_touches_desktop(self) -> None:
        commands = locked_dependency_commands(
            backend_python=Path("backend/.venv/bin/python"),
            npm="/tools/npm",
        )

        self.assertEqual(
            commands,
            [
                [
                    "backend/.venv/bin/python",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "-r",
                    "backend/requirements.lock",
                ],
                ["/tools/npm", "--prefix", "frontend", "ci"],
            ],
        )

        rendered = " ".join(" ".join(command) for command in commands)
        self.assertNotIn("cargo", rendered)
        self.assertNotIn("desktop", rendered)


if __name__ == "__main__":
    unittest.main()

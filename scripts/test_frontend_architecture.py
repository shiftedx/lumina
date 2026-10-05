import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
FRONTEND_SOURCE = REPOSITORY / "frontend" / "src"


class FrontendArchitectureTests(unittest.TestCase):
    def test_shipped_frontend_has_one_application_entry_and_no_retired_cluster(self) -> None:
        main_source = (FRONTEND_SOURCE / "main.tsx").read_text(encoding="utf-8")
        self.assertIn("import LuminaApp from './LuminaApp';", main_source)

        retired_paths = (
            "App.tsx",
            "automationEditor.ts",
            "automationEditor.test.ts",
            "styles.css",
            "youtubeIcons.tsx",
            "youtubeShell.css",
        )
        for relative_path in retired_paths:
            with self.subTest(relative_path=relative_path):
                self.assertFalse((FRONTEND_SOURCE / relative_path).exists())


if __name__ == "__main__":
    unittest.main()

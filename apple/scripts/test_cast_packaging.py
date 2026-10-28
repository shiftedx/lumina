"""Release-boundary regression for statically linked Google Cast resources."""
import pathlib
import plistlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent
GOOGLE_CAST_RESOURCE_BUNDLES = (
    "GoogleCastCoreResources.bundle",
    "GoogleCastOptionalUIResources.bundle",
    "GoogleCastUIResources.bundle",
    "GoogleSansBold.bundle",
    "GoogleSansMedium.bundle",
    "GoogleSansRegular.bundle",
    "MaterialDialogs.bundle",
)


class GoogleCastPackagingTests(unittest.TestCase):
    def test_ios_archive_requires_static_cast_resources_and_privacy_manifest(self):
        with tempfile.TemporaryDirectory(prefix="lumina-cast-package-test-") as temporary:
            archive = pathlib.Path(temporary) / "Synthetic.xcarchive"
            app = archive / "Products/Applications/Synthetic.app"
            app.mkdir(parents=True)
            (archive / "dSYMs/Synthetic.app.dSYM").mkdir(parents=True)
            (app / "Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": "ai.cozylabs.lumina",
                "CFBundleExecutable": "Synthetic",
                "CFBundleVersion": "26",
                "CFBundleShortVersionString": "0.1.0",
                "CFBundleSupportedPlatforms": ["iPhoneOS"],
                "ITSAppUsesNonExemptEncryption": False,
                "NSLocalNetworkUsageDescription": "Synthetic package regression",
                "CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconName": "Synthetic"}},
            }))
            for name in ("PrivacyInfo.xcprivacy", "Assets.car"):
                (app / name).touch()
            subprocess.run(["xcrun", "clang", "-x", "c", "-", "-o", str(app / "Synthetic")],
                           input=b"int main(void) { return 0; }", check=True, capture_output=True)
            for bundle in GOOGLE_CAST_RESOURCE_BUNDLES:
                (app / bundle).mkdir()
            privacy = app / "GoogleCastPrivacy.bundle/PrivacyInfo.xcprivacy"
            privacy.parent.mkdir()
            privacy.touch()
            validate = [sys.executable, str(SCRIPTS / "validate_archive.py"), str(archive), "iOS", "0.1.0", "26"]
            embedded = app / "Frameworks/GoogleCast.framework"
            embedded.mkdir(parents=True)
            dynamic = subprocess.run(validate, capture_output=True, text=True)
            self.assertNotEqual(dynamic.returncode, 0)
            self.assertIn("must be linked statically", dynamic.stderr)
            embedded.rmdir()
            accepted = subprocess.run(validate, capture_output=True, text=True)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            (app / GOOGLE_CAST_RESOURCE_BUNDLES[0]).rmdir()
            rejected = subprocess.run(validate, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("Google Cast resource bundle missing", rejected.stderr)


if __name__ == "__main__":
    unittest.main()

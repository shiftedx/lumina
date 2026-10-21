"""macOS distribution-boundary regression: inherited framework requirements must not ship."""
import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parent


@unittest.skipUnless(sys.platform == 'darwin', 'requires macOS codesign and clang')
class ArchiveSigningTests(unittest.TestCase):
    def test_vendor_requirement_is_rejected_and_export_can_replace_it(self):
        with tempfile.TemporaryDirectory(prefix='lumina-signing-test-') as temporary:
            archive = pathlib.Path(temporary) / 'Synthetic.xcarchive'
            app = archive / 'Products/Applications/Synthetic.app'
            framework = app / 'Frameworks/Synthetic.framework'
            framework.mkdir(parents=True)
            (archive / 'dSYMs/Synthetic.app.dSYM').mkdir(parents=True)
            (framework / 'Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'com.example.Synthetic', 'CFBundleExecutable': 'Synthetic',
                'CFBundlePackageType': 'FMWK', 'CFBundleVersion': '1',
            }))
            subprocess.run(['xcrun', 'clang', '-dynamiclib', '-x', 'c', '-', '-o', str(framework / 'Synthetic')],
                input=b'int synthetic(void) { return 0; }', check=True, capture_output=True)
            subprocess.run(['xcrun', 'clang', '-x', 'c', '-', '-o', str(app / 'Synthetic')],
                input=b'int main(void) { return 0; }', check=True, capture_output=True)
            (app / 'Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'ai.cozylabs.lumina', 'CFBundleExecutable': 'Synthetic',
                'CFBundlePackageType': 'APPL', 'CFBundleVersion': '4', 'CFBundleShortVersionString': '0.1.0',
                'CFBundleSupportedPlatforms': ['AppleTVOS'], 'ITSAppUsesNonExemptEncryption': False,
                'NSLocalNetworkUsageDescription': 'Synthetic signing regression',
                'CFBundleIcons': {'CFBundlePrimaryIcon': {'CFBundleIconName': 'Synthetic'}},
                'TVTopShelfImage': {'TVTopShelfPrimaryImage': 'Synthetic'},
            }))
            for name in ('PrivacyInfo.xcprivacy', 'Assets.car'):
                (app / name).touch()
            # Valid resource seal and signer, but a requirement naming the upstream identifier.
            subprocess.run(['codesign', '--force', '--sign', '-', '--requirements',
                '=designated => identifier "upstream.Synthetic"', str(framework)], check=True, capture_output=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)
            validate = [sys.executable, str(SCRIPTS / 'validate_archive.py'), str(archive), 'tvOS', '0.1.0', '4']
            rejected = subprocess.run(validate, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn('does not satisfy its designated Requirement', rejected.stderr)
            # Preparation must refuse a signed app rather than invalidate its seal.
            prepare = [sys.executable, str(SCRIPTS / 'prepare_archive.py'), str(archive)]
            self.assertNotEqual(subprocess.run(prepare, capture_output=True).returncode, 0)
            subprocess.run(['codesign', '--remove-signature', str(app)], check=True, capture_output=True)
            shutil.rmtree(app / '_CodeSignature')
            subprocess.run(prepare, check=True, capture_output=True)
            self.assertNotEqual(subprocess.run(['codesign', '-d', str(framework)], capture_output=True).returncode, 0)
            # Stand in for Xcode export: sign from current bundle metadata, with no inherited requirement.
            subprocess.run(['codesign', '--force', '--sign', '-', str(framework)], check=True, capture_output=True)
            subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True, capture_output=True)
            accepted = subprocess.run(validate, capture_output=True, text=True)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)


if __name__ == '__main__':
    unittest.main()

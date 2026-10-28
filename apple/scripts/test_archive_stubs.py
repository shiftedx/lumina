"""Distribution regression: drop only unused Xcode placeholders, preserving runtime/resources."""
import pathlib
import plistlib
import subprocess
import sys
import tempfile
import unittest

from archive_stubs import remove_placeholders


@unittest.skipUnless(sys.platform == 'darwin', 'requires Mach-O compiler tools')
class ArchivePlaceholderTests(unittest.TestCase):
    def test_unused_placeholder_removed_but_code_resources_and_loaded_libraries_retained(self):
        with tempfile.TemporaryDirectory(prefix='lumina-placeholder-test-') as temporary:
            app = pathlib.Path(temporary) / 'Synthetic.app'
            app.mkdir()
            (app / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'Synthetic'}))
            def compile(source, destination, *flags):
                subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-macos100.0',
                    '-x', 'c', '-', '-o', str(destination), *flags], input=source.encode(),
                    check=True, capture_output=True)
            for name, code in [('Unused', ''), ('Loaded', ''), ('Resources', ''), ('GoogleCast', ''),
                               ('RealCode', 'int real_function(void) { return 42; }')]:
                framework = app / 'Frameworks' / f'{name}.framework'
                framework.mkdir(parents=True)
                (framework / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': name}))
                flags = ['-dynamiclib', f'-Wl,-install_name,@rpath/{name}.framework/{name}']
                if name == 'GoogleCast':
                    flags += ['-target', 'arm64-apple-macos16.0']
                compile(code, framework / name, *flags)
            (app / 'Frameworks/Resources.framework/PrivacyInfo.xcprivacy').write_text('retained resource')
            cast = app / 'Frameworks/GoogleCast.framework'
            (cast / 'GoogleCastUIResources.bundle').mkdir()
            (cast / 'GoogleCastUIResources.bundle/dialog').write_bytes(b'vendor chooser resource')
            (cast / 'PrivacyInfo.xcprivacy').write_bytes(b'vendor privacy manifest')
            compile('int main(void) { return 0; }', app / 'Synthetic',
                    '-F' + str(app / 'Frameworks'), '-Wl,-needed_framework,Loaded')
            removed = remove_placeholders(app)
            self.assertEqual(removed, ['Unused.framework'])
            self.assertEqual(sorted(f.name for f in (app / 'Frameworks').iterdir()),
                             ['GoogleCast.framework', 'Loaded.framework', 'RealCode.framework', 'Resources.framework'])
            self.assertEqual(remove_placeholders(app), [])
            (app / 'GoogleCastUIResources.bundle').mkdir()
            dialog = app / 'GoogleCastUIResources.bundle/dialog'
            dialog.write_bytes(b'different resource')
            (app / 'GoogleCastPrivacy.bundle').mkdir()
            (app / 'GoogleCastPrivacy.bundle/PrivacyInfo.xcprivacy').write_bytes(b'vendor privacy manifest')
            self.assertEqual(remove_placeholders(app), [], 'Unique Cast resources must be retained')
            dialog.write_bytes(b'vendor chooser resource')
            self.assertEqual(remove_placeholders(app), ['GoogleCast.framework'])
            self.assertEqual(remove_placeholders(app), [])


if __name__ == '__main__':
    unittest.main()

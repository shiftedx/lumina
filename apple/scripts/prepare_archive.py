"""Clear inherited framework signing requirements before Xcode distribution export.

Only unsigned archives are accepted. Xcode signs the app and every embedded framework
at export; preserving a vendor requirement can leave its identifier inconsistent with
CFBundleIdentifier even when the exported certificate and resource seal are valid.
"""
import pathlib
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
apps = list((root / 'Products/Applications').glob('*.app'))
if len(apps) != 1 or (apps[0] / '_CodeSignature').exists():
    raise SystemExit('Expected one unsigned archived application')
frameworks = sorted((apps[0] / 'Frameworks').glob('*.framework'))
cleared = 0
for framework in frameworks:
    signed = subprocess.run(['codesign', '-d', str(framework)], capture_output=True).returncode == 0
    if signed:
        subprocess.run(['codesign', '--remove-signature', str(framework)], check=True)
        cleared += 1
print(f'Prepared unsigned archive: cleared {cleared} inherited framework signatures')

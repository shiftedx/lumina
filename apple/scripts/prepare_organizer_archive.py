"""Create an Organizer-ready archive from an unsigned archive and its signed export."""
import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile

source, ipa, destination = map(pathlib.Path, sys.argv[1:])
if destination.exists():
    raise SystemExit(f"Destination already exists: {destination}")
with tempfile.TemporaryDirectory(prefix="lumina-organizer-") as temporary:
    subprocess.run(["ditto", "-x", "-k", str(ipa), temporary], check=True)
    apps = list((pathlib.Path(temporary) / "Payload").glob("*.app"))
    if len(apps) != 1:
        raise SystemExit("Expected one signed exported application")
    app = apps[0]
    archive_info = plistlib.loads((source / "Info.plist").read_bytes())
    original = source / "Products" / archive_info["ApplicationProperties"]["ApplicationPath"]
    info = plistlib.loads((app / "Info.plist").read_bytes())
    original_info = plistlib.loads((original / "Info.plist").read_bytes())
    for key in ("CFBundleIdentifier", "CFBundleVersion", "CFBundleShortVersionString"):
        if info[key] != original_info[key]:
            raise SystemExit(f"Export differs from archive: {key}")
    def uuids(binary):
        output = subprocess.check_output(["dwarfdump", "--uuid", str(binary)], text=True)
        return sorted(line.split()[1:3] for line in output.splitlines())
    if uuids(app / info["CFBundleExecutable"]) != uuids(original / original_info["CFBundleExecutable"]):
        raise SystemExit("Export executable UUIDs differ from archive symbols")
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    signature = subprocess.run(["codesign", "-dvv", str(app)], capture_output=True, text=True, check=True).stderr
    fields = dict(line.split("=", 1) for line in signature.splitlines() if "=" in line)
    team = fields.get("TeamIdentifier")
    identity = next((line.split("=", 1)[1] for line in signature.splitlines() if line.startswith("Authority=")), None)
    if not team or team == "not set" or not identity:
        raise SystemExit("Export has no signing team or authority")
    subprocess.run(["ditto", str(source), str(destination)], check=True)
    target = destination / "Products" / archive_info["ApplicationProperties"]["ApplicationPath"]
    shutil.rmtree(target)
    subprocess.run(["ditto", str(app), str(target)], check=True)
    archive_info["ApplicationProperties"].update(Team=team, SigningIdentity=identity)
    archive_info["Name"] += f" — {info['CFBundleShortVersionString']} ({info['CFBundleVersion']}) — Signed for Organizer"
    (destination / "Info.plist").write_bytes(plistlib.dumps(archive_info))
print(f"Organizer archive prepared with verified signature and matching executable UUIDs: {destination}")

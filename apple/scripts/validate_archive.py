"""Check the release bundle contract before an export or TestFlight upload."""
import pathlib
import plistlib
import subprocess
import sys
import tempfile


def require(condition, message):
    if not condition:
        raise SystemExit(message)

archive, platform, version, build = sys.argv[1:]
root = pathlib.Path(archive)
is_ipa = root.suffix == ".ipa"
temporary = tempfile.TemporaryDirectory(prefix="lumina-release-validation-")
if is_ipa:
    subprocess.run(["ditto", "-x", "-k", str(root), temporary.name], check=True)
    root = pathlib.Path(temporary.name)
    apps = list((root / "Payload").glob("*.app"))
else:
    apps = list((root / "Products/Applications").glob("*.app"))
require(len(apps) == 1, "Expected exactly one application")
app = apps[0]
info = plistlib.loads((app / "Info.plist").read_bytes())
expected_platform = {"iOS": "iPhoneOS", "tvOS": "AppleTVOS"}[platform]
require(info["CFBundleIdentifier"] == "ai.cozylabs.lumina", "Unexpected release bundle ID")
require(info["CFBundleShortVersionString"] == version, "Wrong marketing version")
require(info["CFBundleVersion"] == build, "Wrong build number")
require(info["CFBundleSupportedPlatforms"] == [expected_platform], "Archive must target physical devices")
require(info["ITSAppUsesNonExemptEncryption"] is False, "Export compliance declaration missing")
require(info.get("NSLocalNetworkUsageDescription"), "LAN purpose string missing")
require((app / "PrivacyInfo.xcprivacy").is_file(), "Privacy manifest missing")
require((app / "Assets.car").is_file(), "Compiled release artwork missing")
icons = info.get("CFBundleIcons", {}).get("CFBundlePrimaryIcon", {})
require(icons, "Primary icon metadata missing")
if platform == "tvOS":
    require(info.get("TVTopShelfImage"), "TV top shelf metadata missing")
if not is_ipa:
    require(list((root / "dSYMs").glob("*.app.dSYM")), "Debug symbols missing")
for path in root.rglob("*"):
    require(path.name != "Fixture.json", "Disposable credentials must not ship")
    require(path.suffix != ".xctest", "Test bundles must not ship")
signed = (app / "_CodeSignature").is_dir()
require(signed or not is_ipa, "Exported IPA must be signed")
if signed:
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
if is_ipa:
    output = subprocess.run(["codesign", "-d", "--entitlements", ":-", str(app)], check=True, capture_output=True)
    entitlements = plistlib.loads(output.stdout)
    require(entitlements.get("get-task-allow") is False, "Distribution must disable debugging")
    require(entitlements.get("beta-reports-active") is True, "TestFlight entitlement missing")
    require(entitlements.get("application-identifier", "").endswith(".ai.cozylabs.lumina"), "Wrong signing identity")
status = "distribution signature" if is_ipa else ("archive signature" if signed else "unsigned archive; signing required at export")
print(f"Validated {platform} {version} ({build}): identity, device platform, artwork, privacy, no test fixture; {status}")
temporary.cleanup()

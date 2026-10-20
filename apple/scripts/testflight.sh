#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-archive}"
case "$mode" in archive|export|upload) ;; *) echo "Usage: $0 [archive|export|upload]" >&2; exit 2 ;; esac
: "${LUMINA_DEVELOPMENT_TEAM:?Set the Apple Developer team ID}"
: "${LUMINA_BUILD_NUMBER:?Set an unused build number}"
version="${LUMINA_VERSION:-0.1.0}"
audience="${LUMINA_TESTFLIGHT_AUDIENCE:-internal}"
case "$audience" in internal|external) ;; *) echo "Audience must be internal or external" >&2; exit 2 ;; esac
release_root="${LUMINA_DISTRIBUTION_DIR:-$PWD/Distribution/$version-$LUMINA_BUILD_NUMBER}"
mkdir -p "$release_root"

if [ "$mode" = archive ]; then
    xcodegen generate
fi
options="$release_root/ExportOptions-$mode-$audience.plist"
if [ "$mode" != archive ]; then
    cp Config/TestFlightExport.plist "$options"
    /usr/libexec/PlistBuddy -c "Add :teamID string $LUMINA_DEVELOPMENT_TEAM" "$options"
    if [ "$audience" = external ]; then
        /usr/libexec/PlistBuddy -c 'Set :testFlightInternalTestingOnly false' "$options"
    fi
    if [ "$mode" = upload ]; then
        /usr/libexec/PlistBuddy -c 'Set :destination upload' "$options"
    fi
fi

for platform in ${LUMINA_PLATFORMS:-iOS tvOS}; do
    case "$platform" in iOS|tvOS) ;; *) echo "Unsupported platform: $platform" >&2; exit 2 ;; esac
    archive="$release_root/Lumina-$platform.xcarchive"
    if [ "$mode" = archive ]; then
        if [ -e "$archive" ]; then echo "Archive already exists: $archive" >&2; exit 1; fi
        xcodebuild -jobs 2 -project Lumina.xcodeproj -scheme "Lumina-$platform" \
            -configuration Release -destination "generic/platform=$platform" \
            -archivePath "$archive" -derivedDataPath "${TMPDIR:-/tmp}/lumina-testflight-build/$version-$LUMINA_BUILD_NUMBER/$platform" \
            CODE_SIGNING_ALLOWED=NO DEVELOPMENT_TEAM="$LUMINA_DEVELOPMENT_TEAM" \
            MARKETING_VERSION="$version" CURRENT_PROJECT_VERSION="$LUMINA_BUILD_NUMBER" archive
        python3 scripts/validate_archive.py "$archive" "$platform" "$version" "$LUMINA_BUILD_NUMBER"
    else
        python3 scripts/validate_archive.py "$archive" "$platform" "$version" "$LUMINA_BUILD_NUMBER"
        xcodebuild -exportArchive -archivePath "$archive" \
            -exportOptionsPlist "$options" -exportPath "$release_root/$mode-$platform" \
            -allowProvisioningUpdates
        if [ "$mode" = export ]; then
            for ipa in "$release_root/$mode-$platform/"*.ipa; do
                python3 scripts/validate_archive.py "$ipa" "$platform" "$version" "$LUMINA_BUILD_NUMBER"
            done
        fi
    fi
done
echo "TestFlight $mode completed: $release_root"

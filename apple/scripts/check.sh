#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
build_root="${TMPDIR:-/tmp}/lumina-native-proof-check"
swift test --package-path Core --scratch-path "$build_root/Core"
for platform in iOS tvOS; do
    xcodebuild -project Lumina.xcodeproj -scheme "Lumina-$platform" \
        -destination "generic/platform=$platform Simulator" \
        -derivedDataPath "$build_root/$platform" CODE_SIGNING_ALLOWED=NO build
 done

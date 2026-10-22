#!/bin/sh
# Only the test bundle receives this optional, disposable configuration.
set -eu
source_fixture="$SRCROOT/Tests/UITests/Fixture.json"
bundle_fixture="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/Fixture.json"
rm -f "$bundle_fixture"
if [ -f "$source_fixture" ]; then
    install -m 600 "$source_fixture" "$bundle_fixture"
fi

bundle_broker="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/FixtureBroker.json"
rm -f "$bundle_broker"
if [ -n "${LUMINA_NATIVE_BROKER_FILE:-}" ]; then
    install -m 600 "$LUMINA_NATIVE_BROKER_FILE" "$bundle_broker"
fi

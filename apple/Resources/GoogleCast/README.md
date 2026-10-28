# Google Cast static resources

These are the unmodified iOS-device resource bundles from Google Cast SDK 4.8.6's
`GoogleCastStaticBinary`. They are copied into the app because the static SDK does
not embed framework resources.

Source package: `google-cast-ios-sdk` 4.8.6, revision
`104500df8ce2a827ca3b3392bf16877fae413a33`. The upstream static artifact checksum
is `e1fe7fd6f2bf4b58e830d378fafc435d159bd755f7ee20fd3033aa1ce313cd6e`.
`SHA256SUMS` records deterministic content hashes for the seven vendor bundles and
the vendor privacy manifest. The device slice is intentional: its Core and UI
resource bundles differ from the simulator slice.

`GoogleCastPrivacy.bundle/PrivacyInfo.xcprivacy` is an exact copy of the vendor
privacy manifest, wrapped only so it can be included alongside Lumina's own app
privacy manifest. `GoogleCastPrivacy.bundle/Info.plist` is Lumina-owned bundle
metadata, not vendor content.

Google Cast's package source is Apache-2.0 licensed. The full vendor and bundled
third-party notices remain in [GoogleCastNotices.txt](../../Notices/GoogleCastNotices.txt).

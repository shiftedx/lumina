// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "LuminaCore",
    platforms: [.iOS(.v18), .tvOS(.v18), .macOS(.v15)],
    products: [.library(name: "LuminaCore", targets: ["LuminaCore"])],
    dependencies: [.package(url: "https://github.com/shiftedx/PrismCore.git", revision: "8fdb8f1e444839c47df4c2f697df39e0ec8098fb")],
    targets: [
        .target(name: "LuminaCore", dependencies: [.product(name: "PrismCore", package: "PrismCore")]),
        .testTarget(name: "LuminaCoreTests", dependencies: ["LuminaCore"]),
    ]
)

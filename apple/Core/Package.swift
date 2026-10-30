// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "LuminaCore",
    platforms: [.iOS(.v18), .tvOS(.v18), .macOS(.v15)],
    products: [.library(name: "LuminaCore", targets: ["LuminaCore"])],
    dependencies: [.package(url: "https://github.com/shiftedx/PrismCore.git", revision: "37148992b891d9a5e587a6aaa0aa56b72cd98b8f")],
    targets: [
        .target(name: "LuminaCore", dependencies: [.product(name: "PrismCore", package: "PrismCore")]),
        .testTarget(name: "LuminaCoreTests", dependencies: ["LuminaCore"]),
    ]
)

// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "LuminaCore",
    platforms: [.iOS(.v18), .tvOS(.v18), .macOS(.v15)],
    products: [.library(name: "LuminaCore", targets: ["LuminaCore"])],
    targets: [
        .target(name: "LuminaCore"),
        .testTarget(name: "LuminaCoreTests", dependencies: ["LuminaCore"]),
    ]
)

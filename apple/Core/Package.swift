// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "LuminaCore",
    platforms: [.iOS(.v18), .tvOS(.v18), .macOS(.v15)],
    products: [.library(name: "LuminaCore", targets: ["LuminaCore"])],
    dependencies: [.package(url: "https://github.com/shiftedx/PrismCore.git", revision: "db42eb269ee629fe8ea81614b187fe290d2ec2d0")],
    targets: [
        .target(name: "LuminaCore", dependencies: [.product(name: "PrismCore", package: "PrismCore")]),
        .testTarget(name: "LuminaCoreTests", dependencies: ["LuminaCore"]),
    ]
)

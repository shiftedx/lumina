// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "LuminaCore",
    platforms: [.iOS(.v18), .tvOS(.v18), .macOS(.v15)],
    products: [.library(name: "LuminaCore", targets: ["LuminaCore"])],
    dependencies: [.package(url: "https://github.com/shiftedx/PrismCore.git", revision: "0a26ce14ff0b38b56ff20c2cbd1a23b953d0a62c")],
    targets: [
        .target(name: "LuminaCore", dependencies: [.product(name: "PrismCore", package: "PrismCore")]),
        .testTarget(name: "LuminaCoreTests", dependencies: ["LuminaCore"]),
    ]
)

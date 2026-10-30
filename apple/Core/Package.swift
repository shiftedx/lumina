// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "LuminaCore",
    platforms: [.iOS(.v18), .tvOS(.v18), .macOS(.v15)],
    products: [.library(name: "LuminaCore", targets: ["LuminaCore"])],
    dependencies: [.package(url: "https://github.com/shiftedx/PrismCore.git", revision: "5e6a28a714dc711a811ff678501ff9b5b99378ee")],
    targets: [
        .target(name: "LuminaCore", dependencies: [.product(name: "PrismCore", package: "PrismCore")]),
        .testTarget(name: "LuminaCoreTests", dependencies: ["LuminaCore"]),
    ]
)

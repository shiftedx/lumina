// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "LuminaBenchmarks", platforms: [.macOS(.v15), .iOS(.v18), .tvOS(.v18)],
    dependencies: [.package(path: "../Core")],
    targets: [
        .executableTarget(name: "VideoBenchmark", dependencies: [.product(name: "LuminaCore", package: "Core")]),
        .executableTarget(name: "OfflineLibraryBenchmark", dependencies: [.product(name: "LuminaCore", package: "Core")]),
        .executableTarget(name: "BookPageBenchmark", dependencies: [.product(name: "LuminaCore", package: "Core")]),
    ])

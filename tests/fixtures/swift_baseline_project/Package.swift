// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "swift-baseline-fixture",
    targets: [
        .target(name: "Fixture", path: "Sources"),
        .testTarget(name: "FixtureTests", dependencies: ["Fixture"], path: "Tests"),
    ]
)

import XCTest

@testable import Fixture

final class FixtureTests: XCTestCase {
    func testFixtureLines() {
        let (aaa, _) = fixtureLines()
        XCTAssertFalse(aaa.isEmpty)
    }
}

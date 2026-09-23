// A call NESTED inside a block comment is prose (Swift nests block comments).
import XCTest
/* outer /* inner */ NSScreen.main */
final class NestedCommentTests: XCTestCase {
    func testNothing() {}
}

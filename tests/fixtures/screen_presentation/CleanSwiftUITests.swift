import SwiftUI
import XCTest

/// Window presentation is forbidden here; these tests exercise the view model
/// only. A string naming NSCursor stays prose: "NSCursor.hide() is banned".
@testable import Pkg

final class CleanSwiftUITests: XCTestCase {
    func test_badge_count_updates() {
        let vm = BadgeViewModel()
        vm.items = ["a", "b"]
        XCTAssertEqual(vm.badgeCount, 2)
    }

    func test_offscreen_render_produces_pixels() throws {
        let view = HStack { Text("ok"); Spacer() }
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        let image = try XCTUnwrap(renderer.uiImage)
        XCTAssertEqual(image.size.width, 44)
    }

    // A comment may explain WHY the screen is off limits: NSScreen,
    // CGWindowListCopyWindowInfo and CAMetalLayer all belong to live QA.
    func test_theme_colors_resolve() {
        XCTAssertNotEqual(Theme.foreground, Theme.background)
    }
}

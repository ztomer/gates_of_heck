func test_app_is_active() {
    let app = NSApplication.shared
    XCTAssertTrue(app.isActive)
}

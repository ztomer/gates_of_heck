func test_display_bounds() {
    let b = CGDisplayBounds(CGMainDisplayID())
    XCTAssertFalse(b.isEmpty)
}

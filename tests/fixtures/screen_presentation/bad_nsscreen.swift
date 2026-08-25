func test_layout_against_display() {
    let frame = NSScreen.main!.frame
    XCTAssertGreaterThan(frame.width, 0)
}

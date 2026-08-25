// USE positions must keep flagging even after the type-position exemption:
// these read the LIVE display through member access.

func test_reads_live_screens() {
    let count = NSScreen.screens.count
    XCTAssertGreaterThan(count, 0)
}

func test_reads_main_display() {
    let frame = NSScreen.main!.frame
    render(on: NSScreen.main)
    XCTAssertGreaterThan(frame.width, 0)
}

func test_grab_desktop() throws {
    let content = try SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
    XCTAssertFalse(content.displays.isEmpty)
}

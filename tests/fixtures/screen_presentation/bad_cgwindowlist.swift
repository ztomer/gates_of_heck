func test_window_image() {
    let img = CGWindowListCreateImage(.null, .optionAll, kCGNullWindowID, [.bestResolution])
    XCTAssertNil(img)
}

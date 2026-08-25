func test_window_info() {
    let info = CGWindowListCopyWindowInfo([.optionAll], kCFNull) as? [[String: Any]]
    XCTAssertEqual(info?.count, 0)
}

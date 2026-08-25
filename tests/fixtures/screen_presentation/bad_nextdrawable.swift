func test_acquire_drawable(_ layer: CAMetalLayer) throws {
    let d = try XCTUnwrap(layer.nextDrawable())
    XCTAssertNotNil(d.texture)
}

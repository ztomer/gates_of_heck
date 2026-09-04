// A FAKE's protocol surface TYPES NSScreen without ever reading the live
// display (ZoneTilerWM test support, 2026-08: ~20 such markers). Every
// occurrence below is a Swift TYPE position; none of this may flag.

protocol ScreenProviding {
    var screens: [NSScreen] { get }
    var main: NSScreen? { get }
    func frame(of screen: NSScreen) -> CGRect
    func frames(of screens: [NSScreen]) -> [CGRect]
    func areas(of screens: [NSScreen: CGRect]) -> [CGRect]
    func firstScreen() -> NSScreen?
    func unwrap(_ screen: NSScreen?) -> NSScreen!
}

final class FakeScreenProvider: ScreenProviding {
    private let cached: [NSScreen] = []
    private var cache: [String: NSScreen] = [:]

    func frame(of screen: NSScreen) -> CGRect { .zero }

    func frames(of screens: [NSScreen]) -> [CGRect] { [] }

    func areas(of screens: [NSScreen: CGRect]) -> [CGRect] { [] }

    func matches(_ candidate: Any) -> Bool {
        candidate is NSScreen || candidate as? NSScreen != nil
    }

    func forceCast(_ value: Any) -> NSScreen {
        value as! NSScreen
    }

    func boxed() -> Box<NSScreen> { Box<NSScreen>() }
}

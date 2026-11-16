// The Mac settings window (dm/mac/Launcher.swift), run by test_launcher_storage.py: lay out its real TabView on every tab without creating or showing a window. The window keeps
// the player's size: each tab must lay out within it, in bounded time, and leave the hosting view's size alone (a launcher that fitted the window to a sidebar TabView's reported
// content height looped in layout and never drew its window).
import AppKit
import SwiftUI

func checkLauncherLayout(_ model: LauncherModel, defaults: UserDefaults) -> Int {
    NSApplication.shared.setActivationPolicy(.prohibited)
    var failed = 0
    func expect(_ c: Bool, _ what: String) { print((c ? "PASS " : "FAIL ") + what); if !c { failed += 1 } }
    for width: CGFloat in [720, 780] {
        autoreleasepool {
            let view = NSHostingView(rootView: LauncherView(m: model, done: { _ in }).defaultAppStorage(defaults))
            view.sizingOptions = []
            let frame = NSRect(x: 0, y: 0, width: width, height: 640)
            view.frame = frame
            for tab in model.tabs {
                defaults.set(tab.name, forKey: "window.tab")
                let start = Date()
                view.layoutSubtreeIfNeeded()
                RunLoop.current.run(until: Date().addingTimeInterval(0.1))
                view.layoutSubtreeIfNeeded()
                let took = Date().timeIntervalSince(start)
                expect(took < 2 && view.frame == frame, "tab \(tab.name) lays out at width \(width) in \(String(format: "%.2f", took)) s, size kept")
            }
        }
    }
    defaults.removeObject(forKey: "window.tab")
    return failed
}

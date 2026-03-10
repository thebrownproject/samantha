import AppKit
import SwiftUI

@main
struct SamanthaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        // Settings scene placeholder -- sam-pjm.6 will populate this.
        Settings {
            Text("Samantha Settings")
                .frame(width: 300, height: 200)
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let orbController = OrbWindowController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        orbController.showWindow()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}

import AppKit
import SwiftUI

@main
struct SamanthaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        Settings {
            SettingsView()
        }
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let orbController = OrbWindowController()
    let hotkeyManager = HotkeyManager()

    func applicationDidFinishLaunching(_ notification: Notification) {
        hotkeyManager.delegate = self
        orbController.showWindow()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }
}

extension AppDelegate: HotkeyManagerDelegate {
    func hotkeyManagerDidDetectKeyDown(_ manager: HotkeyManager) {
        let state = orbController.orbState
        // Toggle: idle -> listening, anything else -> idle.
        // WebSocket start/stop will be wired in sam-0up.2.
        state.appState = state.appState == .idle ? .listening : .idle
    }

    func hotkeyManagerDidDetectKeyUp(_ manager: HotkeyManager) {
        // Push-to-talk keyUp handling reserved for future use.
        // V1 uses toggle semantics (keyDown toggles, keyUp is no-op).
    }
}

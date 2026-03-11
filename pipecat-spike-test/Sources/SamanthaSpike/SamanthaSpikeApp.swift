import AppKit
import SwiftUI

@main
struct SamanthaSpikeApp: App {
    @NSApplicationDelegateAdaptor(SpikeAppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup("Samantha Dev Console") {
            DevConsoleView(state: appDelegate.consoleState, actions: appDelegate.consoleActions)
        }
        Settings {
            Text("Settings placeholder")
                .frame(width: 300, height: 200)
        }
    }
}

@MainActor
final class SpikeAppDelegate: NSObject, NSApplicationDelegate {
    let consoleState = DevConsoleState()
    let hotkeyManager = HotkeyManager()
    let audioManager = AudioManager()
    let desktopContextToolExecutor = DesktopContextToolExecutor()
    private(set) var orbController: OrbWindowController?

    lazy var consoleActions: DevConsoleActions = DevConsoleActions(
        onTalkToggle: { [weak self] in self?.toggleListening() },
        onInterrupt: { [weak self] in self?.interruptConversation() },
        onApprove: { _, _ in },
        onReject: { _, _ in },
        onClearLog: { [weak self] in self?.consoleState.clearLog() }
    )

    func applicationDidFinishLaunching(_ notification: Notification) {
        hotkeyManager.delegate = self

        let controller = OrbWindowController()
        controller.showWindow()
        orbController = controller

        Task {
            let granted = await audioManager.requestPermission()
            consoleState.log("Mic permission: \(granted ? "granted" : "denied")",
                            level: granted ? .info : .warning)
        }

        SpikeConfig.bootstrapStorage()

        if let config = try? SpikeConfig.load() {
            consoleState.log("Config loaded (llm: \(config.llmProvider)/\(config.llmModel))")
        }

        for account in KeychainAccount.allCases {
            if KeychainHelper.loadAPIKey(for: account) != nil {
                consoleState.log("\(account.rawValue): configured")
            } else {
                consoleState.log("\(account.rawValue): not set", level: .warning)
            }
        }

        consoleState.log("Spike app launched")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        audioManager.stopCapture()
    }

    private func toggleListening() {
        switch consoleState.appState {
        case .idle:
            consoleState.appState = .listening
            consoleState.log("Listening started (no STT client yet)")
        case .listening:
            consoleState.appState = .idle
            consoleState.log("Listening stopped")
        case .thinking, .speaking:
            interruptConversation()
        case .error:
            consoleState.appState = .idle
            consoleState.log("Reset from error state")
        }
    }

    private func interruptConversation() {
        audioManager.stopPlayback()
        consoleState.isPlaying = false
        consoleState.appState = .idle
        consoleState.log("Interrupted")
    }
}

extension SpikeAppDelegate: HotkeyManagerDelegate {
    func hotkeyManagerDidDetectKeyDown(_ manager: HotkeyManager) {
        toggleListening()
    }

    func hotkeyManagerDidDetectKeyUp(_ manager: HotkeyManager) {}
}

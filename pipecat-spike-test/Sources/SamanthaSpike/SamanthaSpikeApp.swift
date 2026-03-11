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
            SettingsView()
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
    private(set) var conversationLoop: ConversationLoop?

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

        let config: SpikeConfig
        if let loaded = try? SpikeConfig.load() {
            config = loaded
            consoleState.log("Config loaded (llm: \(config.llmProvider)/\(config.llmModel))")
        } else {
            config = .defaults
        }

        for account in KeychainAccount.allCases {
            if KeychainHelper.loadAPIKey(for: account) != nil {
                consoleState.log("\(account.rawValue): configured")
            } else {
                consoleState.log("\(account.rawValue): not set", level: .warning)
            }
        }

        if let deepgramKey = KeychainHelper.loadAPIKey(for: .deepgramAPIKey),
           let openRouterKey = KeychainHelper.loadAPIKey(for: .openRouterAPIKey) {
            let loop = ConversationLoop(
                audioManager: audioManager,
                consoleState: consoleState,
                transcriptStore: controller.transcriptStore,
                deepgramAPIKey: deepgramKey,
                openRouterAPIKey: openRouterKey,
                config: config
            )
            loop.orbState = controller.orbState
            conversationLoop = loop
            consoleState.log("ConversationLoop initialized")
        } else {
            consoleState.log("ConversationLoop not initialized -- missing API keys", level: .warning)
        }

        consoleState.log("Spike app launched")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        conversationLoop?.interrupt()
        audioManager.stopCapture()
    }

    private func toggleListening() {
        guard let loop = conversationLoop else {
            consoleState.log("No ConversationLoop -- check API keys", level: .error)
            return
        }

        switch loop.appState {
        case .idle, .error:
            loop.startListening()
        case .listening:
            loop.stopListening()
        case .thinking, .speaking:
            loop.interrupt()
        }
    }

    private func interruptConversation() {
        conversationLoop?.interrupt()
    }
}

extension SpikeAppDelegate: HotkeyManagerDelegate {
    func hotkeyManagerDidDetectKeyDown(_ manager: HotkeyManager) {
        toggleListening()
    }

    func hotkeyManagerDidDetectKeyUp(_ manager: HotkeyManager) {}
}

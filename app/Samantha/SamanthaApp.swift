import AppKit
import SwiftUI

@main
struct SamanthaApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

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
final class AppDelegate: NSObject, NSApplicationDelegate {
    let consoleState = DevConsoleState()
    let hotkeyManager = HotkeyManager()
    let audioManager = AudioManager()
    let webSocketClient = WebSocketClient()
    let desktopContextToolExecutor = DesktopContextToolExecutor()

    private var audioFrameCount: Int = 0

    lazy var consoleActions: DevConsoleActions = DevConsoleActions(
        onTalkToggle: { [weak self] in self?.toggleListening() },
        onInterrupt: { [weak self] in self?.interruptConversation() },
        onApprove: { [weak self] callId, always in self?.approveToolCall(callId: callId, always: always) },
        onReject: { [weak self] callId, always in self?.rejectToolCall(callId: callId, always: always) },
        onClearLog: { [weak self] in self?.consoleState.clearLog() }
    )

    func applicationDidFinishLaunching(_ notification: Notification) {
        hotkeyManager.delegate = self
        webSocketClient.delegate = self
        webSocketClient.appToolExecutor = desktopContextToolExecutor
        webSocketClient.connect()

        // Watch for voice changes in Settings
        UserDefaults.standard.addObserver(self, forKeyPath: "selectedVoice", options: [.new], context: nil)

        Task {
            let granted = await audioManager.requestPermission()
            consoleState.log("Mic permission: \(granted ? "granted" : "denied")",
                            level: granted ? .info : .warning)
        }

        if KeychainHelper.loadAPIKey() != nil {
            consoleState.log("API key: configured in Keychain")
        } else {
            consoleState.log("API key: not set (set OPENAI_API_KEY env for backend)", level: .warning)
        }

        consoleState.log("App launched")
    }

    override func observeValue(
        forKeyPath keyPath: String?,
        of object: Any?,
        change: [NSKeyValueChangeKey: Any]?,
        context: UnsafeMutableRawPointer?
    ) {
        if keyPath == "selectedVoice", let voice = change?[.newKey] as? String {
            Task { @MainActor in
                guard webSocketClient.connectionState == .connected else { return }
                try? await webSocketClient.setVoice(voice)
                consoleState.log("Voice changed to: \(voice)")
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        UserDefaults.standard.removeObserver(self, forKeyPath: "selectedVoice")
        webSocketClient.disconnect()
        audioManager.stopCapture()
    }

    // MARK: - Listening Toggle

    private func toggleListening() {
        switch consoleState.appState {
        case .idle:
            beginListening()
        case .listening:
            stopListening()
        case .thinking, .speaking, .error:
            interruptConversation()
        }
    }

    private func beginListening() {
        Task { @MainActor in
            guard await audioManager.requestPermission() else {
                consoleState.appState = .error
                consoleState.log("Mic permission denied", level: .error)
                return
            }

            do {
                try await webSocketClient.startListening()
                audioFrameCount = 0
                audioManager.startCapture { [weak self] chunk in
                    Task { @MainActor [weak self] in
                        guard let self else { return }
                        await self.webSocketClient.sendAudio(chunk)
                        self.audioFrameCount += 1
                        if self.audioFrameCount % 50 == 0 {
                            self.consoleState.log("Sent \(self.audioFrameCount) audio frames", level: .debug)
                        }
                    }
                }
                consoleState.appState = .listening
                consoleState.isCapturing = true
                consoleState.log("Listening started")
            } catch {
                consoleState.appState = .error
                consoleState.log("Failed to start listening: \(error.localizedDescription)", level: .error)
            }
        }
    }

    private func stopListening() {
        Task { @MainActor in
            audioManager.stopInputCapture()
            consoleState.isCapturing = false
            do {
                try await webSocketClient.stopListening()
                consoleState.log("Listening stopped")
            } catch {
                consoleState.appState = .error
                consoleState.log("Failed to stop listening: \(error.localizedDescription)", level: .error)
            }
        }
    }

    private func interruptConversation() {
        Task { @MainActor in
            audioManager.stopInputCapture()
            audioManager.stopPlayback()
            consoleState.isCapturing = false
            consoleState.isPlaying = false
            do {
                try await webSocketClient.interrupt()
                consoleState.log("Interrupt sent")
            } catch {
                consoleState.appState = .error
                consoleState.log("Interrupt failed: \(error.localizedDescription)", level: .error)
            }
        }
    }

    // MARK: - Tool Approval (stub -- wired to WebSocketClient public methods in Task 3 / sam-d5g)

    private func approveToolCall(callId: String, always: Bool) {
        consoleState.log("Approve tool call: \(callId) (always: \(always))", level: .info)
        consoleState.removeApproval(callId: callId)
        Task {
            try? await webSocketClient.approveToolCall(callId: callId, always: always)
        }
    }

    private func rejectToolCall(callId: String, always: Bool) {
        consoleState.log("Reject tool call: \(callId) (always: \(always))", level: .info)
        consoleState.removeApproval(callId: callId)
        Task {
            try? await webSocketClient.rejectToolCall(callId: callId, always: always)
        }
    }

    // MARK: - Helpers

    private func formatArgs(_ args: Any?) -> String {
        guard let dict = args as? [String: Any] else {
            if let str = args as? String { return str }
            return ""
        }
        let parts = dict.map { "\($0.key): \($0.value)" }
        return "{\(parts.joined(separator: ", "))}"
    }
}

// MARK: - HotkeyManagerDelegate

extension AppDelegate: HotkeyManagerDelegate {
    func hotkeyManagerDidDetectKeyDown(_ manager: HotkeyManager) {
        toggleListening()
    }

    func hotkeyManagerDidDetectKeyUp(_ manager: HotkeyManager) {
        // Toggle mode - keyUp is a no-op
    }
}

// MARK: - WebSocketClientDelegate

extension AppDelegate: WebSocketClientDelegate {
    func webSocketClient(_ client: WebSocketClient, didChangeConnectionState state: WebSocketConnectionState) {
        consoleState.connectionState = state
        consoleState.log("WebSocket: \(state)")

        switch state {
        case .connected:
            let voice = UserDefaults.standard.string(forKey: "selectedVoice") ?? "ash"
            Task { try? await webSocketClient.setVoice(voice) }
            consoleState.log("Synced voice: \(voice)", level: .debug)
        case .disconnected:
            if consoleState.appState != .idle {
                audioManager.stopCapture()
                consoleState.isCapturing = false
                consoleState.isPlaying = false
                consoleState.appState = .error
            }
        case .connecting:
            break
        }
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveState state: AppState) {
        consoleState.appState = state
        consoleState.log("state_change -> \(state.rawValue)")

        // Sync isPlaying/isCapturing from backend state
        if state == .idle || state == .error {
            consoleState.isCapturing = false
        }
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveTranscriptRole role: String, text: String, isFinal: Bool) {
        consoleState.addTranscript(role: role, text: text, isFinal: isFinal)
        let preview = text.prefix(80)
        consoleState.log("transcript(\(role)): \"\(preview)\"\(isFinal ? " [final]" : "")")
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveAudio data: Data) {
        audioManager.enqueueAudio(data: data)
        consoleState.isPlaying = true
    }

    func webSocketClientDidClearPlayback(_ client: WebSocketClient) {
        audioManager.stopPlayback()
        consoleState.isPlaying = false
        consoleState.log("clear_playback received")
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveError message: String) {
        consoleState.log("ERROR: \(message)", level: .error)
        audioManager.stopCapture()
        consoleState.isCapturing = false
        consoleState.isPlaying = false
        consoleState.appState = .error
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveToolMessage message: [String: Any]) {
        let type = message["type"] as? String ?? ""
        switch type {
        case "tool_start":
            let name = message["name"] as? String ?? "unknown"
            consoleState.log("tool_start: \(name) \(formatArgs(message["args"]))")
        case "tool_end":
            let name = message["name"] as? String ?? "unknown"
            let result = message["result"] as? String ?? ""
            consoleState.log("tool_end: \(name) -> \(result.prefix(200))")
        case "tool_approval_required":
            let name = message["name"] as? String ?? "unknown"
            let callId = message["call_id"] as? String ?? ""
            consoleState.addApproval(callId: callId, toolName: name, argsDescription: formatArgs(message["args"]))
            consoleState.log("APPROVAL REQUIRED: \(name) (call_id: \(callId))", level: .warning)
        default:
            consoleState.log("Unknown tool message: \(type)", level: .debug)
        }
    }
}

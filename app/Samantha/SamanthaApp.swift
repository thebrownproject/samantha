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
    let audioManager = AudioManager()
    let webSocketClient = WebSocketClient()
    let desktopContextToolExecutor = DesktopContextToolExecutor()

    /// Convenience accessor for WebSocket integration (sam-0up).
    var transcriptStore: TranscriptStore { orbController.transcriptStore }

    func applicationDidFinishLaunching(_ notification: Notification) {
        hotkeyManager.delegate = self
        webSocketClient.delegate = self
        webSocketClient.appToolExecutor = desktopContextToolExecutor
        orbController.showWindow()
        webSocketClient.connect()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false
    }

    func applicationWillTerminate(_ notification: Notification) {
        webSocketClient.disconnect()
        audioManager.stopCapture()
    }

    private func beginListening() {
        Task { @MainActor in
            guard await audioManager.requestPermission() else {
                orbController.orbState.appState = .error
                return
            }

            do {
                try await webSocketClient.startListening()
                audioManager.startCapture { [weak self] chunk in
                    Task { @MainActor [weak self] in
                        await self?.webSocketClient.sendAudio(chunk)
                    }
                }
                orbController.orbState.appState = .listening
            } catch {
                orbController.orbState.appState = .error
            }
        }
    }

    private func stopListening() {
        Task { @MainActor in
            audioManager.stopInputCapture()
            do {
                try await webSocketClient.stopListening()
            } catch {
                orbController.orbState.appState = .error
            }
        }
    }

    private func interruptConversation() {
        Task { @MainActor in
            audioManager.stopInputCapture()
            audioManager.stopPlayback()
            do {
                try await webSocketClient.interrupt()
            } catch {
                orbController.orbState.appState = .error
            }
        }
    }
}

extension AppDelegate: HotkeyManagerDelegate {
    func hotkeyManagerDidDetectKeyDown(_ manager: HotkeyManager) {
        let state = orbController.orbState
        switch state.appState {
        case .idle:
            beginListening()
        case .listening:
            stopListening()
        case .thinking, .speaking, .error:
            interruptConversation()
        }
    }

    func hotkeyManagerDidDetectKeyUp(_ manager: HotkeyManager) {
        // Push-to-talk keyUp handling reserved for future use.
        // V1 uses toggle semantics (keyDown toggles, keyUp is no-op).
    }
}

extension AppDelegate: WebSocketClientDelegate {
    func webSocketClient(_ client: WebSocketClient, didChangeConnectionState state: WebSocketConnectionState) {
        if state == .disconnected && orbController.orbState.appState != .idle {
            audioManager.stopCapture()
            orbController.orbState.appState = .error
        }
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveState state: AppState) {
        orbController.orbState.appState = state
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveTranscriptRole role: String, text: String, isFinal: Bool) {
        transcriptStore.handleTranscript(role: role, text: text, isFinal: isFinal)
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveAudio data: Data) {
        audioManager.enqueueAudio(data: data)
    }

    func webSocketClientDidClearPlayback(_ client: WebSocketClient) {
        audioManager.stopPlayback()
    }

    func webSocketClient(_ client: WebSocketClient, didReceiveError message: String) {
        audioManager.stopCapture()
        orbController.orbState.appState = .error
    }
}

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
    let agentClient = DeepgramAgentClient()
    let desktopContextToolExecutor = DesktopContextToolExecutor()
    private(set) var orbController: OrbWindowController?
    private(set) var toolRegistry: ToolRegistry?
    private(set) var functionCallHandler: FunctionCallHandler?
    private var config: SpikeConfig = .defaults
    private var deepgramAPIKey: String?
    private var openRouterAPIKey: String?

    lazy var consoleActions: DevConsoleActions = DevConsoleActions(
        onTalkToggle: { [weak self] in self?.toggleListening() },
        onInterrupt: { [weak self] in self?.interruptSession() },
        onApprove: { _, _ in },
        onReject: { _, _ in },
        onClearLog: { [weak self] in self?.consoleState.clearLog() }
    )

    func applicationDidFinishLaunching(_ notification: Notification) {
        hotkeyManager.delegate = self
        agentClient.delegate = self

        let controller = OrbWindowController()
        controller.showWindow()
        orbController = controller

        Task {
            let granted = await audioManager.requestPermission()
            consoleState.log("Mic permission: \(granted ? "granted" : "denied")",
                            level: granted ? .info : .warning)
        }

        SpikeConfig.bootstrapStorage()

        if let loaded = try? SpikeConfig.load() {
            config = loaded
            consoleState.log("Config loaded (llm: \(config.llmProvider)/\(config.llmModel))")
        }

        for account in KeychainAccount.allCases {
            if KeychainHelper.loadAPIKey(for: account) != nil {
                consoleState.log("\(account.rawValue): configured")
            } else {
                consoleState.log("\(account.rawValue): not set", level: .warning)
            }
        }

        deepgramAPIKey = KeychainHelper.loadAPIKey(for: .deepgramAPIKey)
        openRouterAPIKey = KeychainHelper.loadAPIKey(for: .openRouterAPIKey)

        let registry = ToolRegistry.withDefaultTools(confirmDestructive: config.confirmDestructive)
        DesktopTools.register(on: registry, executor: desktopContextToolExecutor)
        toolRegistry = registry

        functionCallHandler = FunctionCallHandler(
            toolRegistry: registry,
            agentClient: agentClient
        )

        consoleState.log("Spike app launched (Voice Agent mode)")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        agentClient.disconnect()
        audioManager.stopCapture()
    }

    // MARK: - Session control

    private func toggleListening() {
        switch consoleState.appState {
        case .idle, .error:
            startSession()
        case .listening:
            stopSession()
        case .thinking, .speaking:
            interruptSession()
        }
    }

    private func startSession() {
        guard let deepgramKey = deepgramAPIKey else {
            consoleState.log("Cannot start -- Deepgram API key not set", level: .error)
            return
        }

        consoleState.connectionState = .connecting
        consoleState.log("Connecting to Voice Agent...")

        agentClient.connect(apiKey: deepgramKey)
    }

    private func stopSession() {
        agentClient.disconnect()
        audioManager.stopCapture()
        setState(.idle)
        consoleState.connectionState = .disconnected
        consoleState.log("Session stopped")
    }

    private func interruptSession() {
        audioManager.stopPlayback()
        setState(.listening)
        consoleState.log("Interrupted -- resuming listen")
    }

    private func setState(_ newState: AppState) {
        consoleState.appState = newState
        consoleState.isCapturing = audioManager.isCapturing
        consoleState.isPlaying = audioManager.isPlaying
        orbController?.orbState.appState = newState
    }
}

// MARK: - HotkeyManagerDelegate

extension SpikeAppDelegate: HotkeyManagerDelegate {
    func hotkeyManagerDidDetectKeyDown(_ manager: HotkeyManager) {
        toggleListening()
    }

    func hotkeyManagerDidDetectKeyUp(_ manager: HotkeyManager) {}
}

// MARK: - DeepgramAgentDelegate

extension SpikeAppDelegate: DeepgramAgentDelegate {
    nonisolated func agentDidConnect(requestId: String) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.consoleState.connectionState = .connected
            self.consoleState.log("Voice Agent connected (request: \(requestId))")

            guard let orKey = self.openRouterAPIKey, let registry = self.toolRegistry else {
                self.consoleState.log("Missing OpenRouter key or tool registry", level: .error)
                return
            }

            let settings = VoiceAgentSettingsBuilder.build(
                config: self.config,
                toolRegistry: registry,
                openRouterKey: orKey,
                greeting: "Hey there!"
            )
            self.agentClient.sendSettings(settings)
        }
    }

    nonisolated func agentDidDisconnect(error: Error?) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.consoleState.connectionState = .disconnected
            if let error {
                self.consoleState.log("Voice Agent disconnected: \(error.localizedDescription)", level: .warning)
            } else {
                self.consoleState.log("Voice Agent disconnected")
            }
            self.audioManager.stopCapture()
            self.setState(.idle)
        }
    }

    nonisolated func agentSettingsApplied() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.consoleState.log("Voice Agent ready")
            self.setState(.listening)

            self.audioManager.startCapture { [weak self] data in
                self?.agentClient.sendAudio(data)
            }
        }
    }

    nonisolated func agentUserStartedSpeaking() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.audioManager.stopPlayback()
            self.setState(.listening)
        }
    }

    nonisolated func agentDidStartThinking(content: String) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.setState(.thinking)
        }
    }

    nonisolated func agentDidStartSpeaking(totalLatency: Double, ttsLatency: Double, llmLatency: Double) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.setState(.speaking)
            let totalMs = Int(totalLatency * 1000)
            let ttsMs = Int(ttsLatency * 1000)
            let llmMs = Int(llmLatency * 1000)
            self.consoleState.log("Latency -- total: \(totalMs)ms, tts: \(ttsMs)ms, llm: \(llmMs)ms", level: .debug)
        }
    }

    nonisolated func agentDidReceiveAudio(_ data: Data) {
        Task { @MainActor [weak self] in
            self?.audioManager.enqueueAudio(data: data)
        }
    }

    nonisolated func agentAudioDone() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            if self.consoleState.appState == .speaking {
                self.setState(.listening)
            }
        }
    }

    nonisolated func agentDidReceiveTranscript(role: String, content: String) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.orbController?.transcriptStore.handleTranscript(role: role, text: content, isFinal: true)
            self.consoleState.addTranscript(role: role, text: content, isFinal: true)
            self.consoleState.log("\(role): \(content)")
        }
    }

    nonisolated func agentDidReceiveFunctionCall(id: String, name: String, arguments: String) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.consoleState.log("Tool call: \(name)(\(arguments.prefix(100)))")

            guard let handler = self.functionCallHandler else {
                self.consoleState.log("No function call handler -- returning error", level: .warning)
                self.agentClient.sendFunctionCallResponse(id: id, name: name, output: "Error: tool system not initialized")
                return
            }

            handler.handle(id: id, name: name, arguments: arguments)
        }
    }

    nonisolated func agentDidReceiveError(message: String) {
        Task { @MainActor [weak self] in
            self?.consoleState.log("Agent error: \(message)", level: .error)
        }
    }

    nonisolated func agentDidReceiveWarning(message: String) {
        Task { @MainActor [weak self] in
            self?.consoleState.log("Agent warning: \(message)", level: .warning)
        }
    }
}

import Foundation
import os

private let log = Logger(subsystem: "com.samantha.spike", category: "ConversationLoop")

/// Accumulates LLM text deltas, splits into sentences, and feeds them to a
/// continuation for serial TTS consumption. Thread-safe via internal lock.
private final class SentenceAccumulator: @unchecked Sendable {
    private let continuation: AsyncStream<String>.Continuation
    private let lock = NSLock()
    private var buffer = ""
    private(set) var fullResponse = ""

    // Latency tracking -- written once, read once
    private let utteranceEndTime: CFAbsoluteTime
    private var llmFirstTokenTime: CFAbsoluteTime = 0

    init(continuation: AsyncStream<String>.Continuation, utteranceEndTime: CFAbsoluteTime) {
        self.continuation = continuation
        self.utteranceEndTime = utteranceEndTime
    }

    /// Called from LLM stream callback (off main actor).
    /// Returns (fullResponseSnapshot, llmFirstTokenDeltaMs?) for UI updates.
    func ingest(_ delta: String) -> (snapshot: String, firstTokenMs: Int?) {
        lock.lock()
        defer { lock.unlock() }

        var firstTokenMs: Int?
        if llmFirstTokenTime == 0 {
            llmFirstTokenTime = CFAbsoluteTimeGetCurrent()
            firstTokenMs = Int((llmFirstTokenTime - utteranceEndTime) * 1000)
            log.info("llm_first_token: \(firstTokenMs!)ms after utterance_end")
        }

        fullResponse += delta
        buffer += delta
        let snapshot = fullResponse

        // Split on sentence boundaries (.!? followed by whitespace) for low-latency TTS
        while let range = buffer.range(of: #"[.!?]\s"#, options: .regularExpression) {
            let sentence = String(buffer[buffer.startIndex...range.lowerBound])
            buffer = String(buffer[range.upperBound...])
            continuation.yield(sentence)
        }

        return (snapshot, firstTokenMs)
    }

    /// Flush remaining text and close the stream.
    func finish() -> String {
        lock.lock()
        defer { lock.unlock() }
        let remaining = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
        if !remaining.isEmpty {
            continuation.yield(remaining)
        }
        continuation.finish()
        buffer = ""
        return fullResponse
    }

    func cancel() {
        continuation.finish()
    }
}

@MainActor
final class ConversationLoop: NSObject, ObservableObject {
    @Published private(set) var appState: AppState = .idle

    private let audioManager: AudioManager
    private let sttClient: DeepgramSTTClient
    private let ttsClient: DeepgramTTSClient
    private let llmClient: OpenRouterLLMClient
    private let consoleState: DevConsoleState
    private let transcriptStore: TranscriptStore

    private var history: [ChatMessage] = []
    private var currentUserText = ""
    private var turnTask: Task<Void, Never>?

    private var utteranceEndTime: CFAbsoluteTime = 0
    private var ttsFirstAudioLogged = false

    weak var orbState: OrbState?

    init(
        audioManager: AudioManager,
        consoleState: DevConsoleState,
        transcriptStore: TranscriptStore,
        deepgramAPIKey: String,
        openRouterAPIKey: String,
        config: SpikeConfig = .defaults
    ) {
        self.audioManager = audioManager
        self.consoleState = consoleState
        self.transcriptStore = transcriptStore

        self.sttClient = DeepgramSTTClient(apiKey: deepgramAPIKey)
        self.ttsClient = DeepgramTTSClient(apiKey: deepgramAPIKey)
        self.llmClient = OpenRouterLLMClient(apiKey: openRouterAPIKey, model: config.llmModel)
        self.ttsClient.model = config.voice

        super.init()

        self.sttClient.delegate = self
        self.history = [ChatMessage(role: "system", content: Prompts.system)]

        self.ttsClient.onAudioChunk = { [weak self] data in
            Task { @MainActor [weak self] in
                guard let self else { return }
                if !self.ttsFirstAudioLogged {
                    self.ttsFirstAudioLogged = true
                    let delta = (CFAbsoluteTimeGetCurrent() - self.utteranceEndTime) * 1000
                    log.info("tts_first_audio: \(Int(delta))ms after utterance_end")
                    self.consoleState.log("TTS first audio: \(Int(delta))ms", level: .debug)
                }
                self.audioManager.enqueueAudio(data: data)
            }
        }
    }

    func startListening() {
        guard appState == .idle || appState == .error else { return }
        currentUserText = ""
        audioManager.startCapture { [weak self] data in
            self?.sttClient.sendAudio(data)
        }
        sttClient.connect()
        setState(.listening)
        consoleState.log("Listening started")
    }

    func stopListening() {
        sttClient.disconnect()
        audioManager.stopInputCapture()
        if appState == .listening {
            setState(.idle)
        }
        consoleState.log("Listening stopped")
    }

    func interrupt() {
        turnTask?.cancel()
        turnTask = nil
        llmClient.cancel()
        ttsClient.cancel()
        audioManager.stopPlayback()
        sttClient.disconnect()
        audioManager.stopInputCapture()
        setState(.idle)
        consoleState.log("Interrupted")
    }

    private func setState(_ newState: AppState) {
        appState = newState
        consoleState.appState = newState
        orbState?.appState = newState
    }

    private func processUserUtterance() {
        let text = currentUserText.trimmingCharacters(in: .whitespacesAndNewlines)
        currentUserText = ""

        guard !text.isEmpty else {
            consoleState.log("Empty utterance, ignoring", level: .debug)
            return
        }

        sttClient.disconnect()
        audioManager.stopInputCapture()

        history.append(ChatMessage(role: "user", content: text))
        consoleState.addTranscript(role: "user", text: text, isFinal: true)
        transcriptStore.handleTranscript(role: "user", text: text, isFinal: true)

        utteranceEndTime = CFAbsoluteTimeGetCurrent()
        ttsFirstAudioLogged = false

        setState(.thinking)
        consoleState.log("User: \(text)")

        turnTask = Task { [weak self] in
            await self?.runTurn()
        }
    }

    private func runTurn() async {
        let (sentenceStream, sentenceContinuation) = AsyncStream<String>.makeStream()
        let accumulator = SentenceAccumulator(
            continuation: sentenceContinuation,
            utteranceEndTime: utteranceEndTime
        )

        // TTS consumer -- reads sentences serially so audio never interleaves
        let ttsTask = Task { [weak self] in
            guard let self else { return }
            var firstSentence = true
            for await sentence in sentenceStream {
                try Task.checkCancellation()
                log.debug("TTS: \(sentence.prefix(60))")
                if firstSentence {
                    firstSentence = false
                    await MainActor.run { [weak self] in
                        if self?.appState == .thinking {
                            self?.setState(.speaking)
                        }
                    }
                }
                do {
                    try await self.ttsClient.speak(sentence)
                } catch is CancellationError {
                    return
                } catch {
                    log.error("TTS error: \(error.localizedDescription)")
                }
            }
        }

        // LLM producer
        do {
            try await llmClient.stream(
                messages: history,
                tools: nil,
                onTextDelta: { [weak self] delta in
                    let (snapshot, firstTokenMs) = accumulator.ingest(delta)

                    if let ms = firstTokenMs {
                        Task { @MainActor [weak self] in
                            self?.consoleState.log("LLM first token: \(ms)ms", level: .debug)
                        }
                    }

                    Task { @MainActor [weak self] in
                        self?.consoleState.addTranscript(role: "assistant", text: snapshot, isFinal: false)
                        self?.transcriptStore.handleTranscript(role: "assistant", text: snapshot, isFinal: false)
                    }
                },
                onToolCall: { toolCall in
                    log.warning("Tool call ignored (not enabled): \(toolCall.function.name)")
                },
                onComplete: { [weak self] message in
                    Task { @MainActor [weak self] in
                        self?.history.append(message)
                    }
                }
            )

            let finalResponse = accumulator.finish()

            // Wait for all TTS to complete
            _ = await ttsTask.result

            let totalMs = Int((CFAbsoluteTimeGetCurrent() - utteranceEndTime) * 1000)
            log.info("turn_complete: \(totalMs)ms total")
            consoleState.addTranscript(role: "assistant", text: finalResponse, isFinal: true)
            transcriptStore.handleTranscript(role: "assistant", text: finalResponse, isFinal: true)
            consoleState.log("Turn complete: \(totalMs)ms total", level: .debug)

            if appState == .speaking || appState == .thinking {
                setState(.idle)
            }
        } catch is CancellationError {
            accumulator.cancel()
            ttsTask.cancel()
            log.debug("Turn cancelled")
        } catch {
            accumulator.cancel()
            ttsTask.cancel()
            log.error("LLM error: \(error.localizedDescription)")
            consoleState.log("LLM error: \(error.localizedDescription)", level: .error)
            setState(.error)
        }
    }
}

// MARK: - DeepgramSTTDelegate

extension ConversationLoop: DeepgramSTTDelegate {
    nonisolated func sttDidReceiveTranscript(_ text: String, isFinal: Bool) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            if isFinal {
                if !text.isEmpty {
                    if !self.currentUserText.isEmpty { self.currentUserText += " " }
                    self.currentUserText += text
                }
                self.consoleState.addTranscript(role: "user", text: self.currentUserText, isFinal: false)
                self.transcriptStore.handleTranscript(role: "user", text: self.currentUserText, isFinal: false)
            } else {
                let preview = self.currentUserText.isEmpty ? text : self.currentUserText + " " + text
                self.consoleState.addTranscript(role: "user", text: preview, isFinal: false)
                self.transcriptStore.handleTranscript(role: "user", text: preview, isFinal: false)
            }
        }
    }

    nonisolated func sttDidDetectSpeechStart() {
        Task { @MainActor [weak self] in
            self?.consoleState.log("Speech detected", level: .debug)
        }
    }

    nonisolated func sttDidDetectUtteranceEnd() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            log.info("Utterance end detected")
            self.consoleState.log("Utterance end", level: .debug)
            self.processUserUtterance()
        }
    }

    nonisolated func sttDidConnect() {
        Task { @MainActor [weak self] in
            self?.consoleState.log("STT connected")
            self?.consoleState.connectionState = .connected
        }
    }

    nonisolated func sttDidDisconnect(error: Error?) {
        Task { @MainActor [weak self] in
            if let error {
                self?.consoleState.log("STT disconnected: \(error.localizedDescription)", level: .warning)
            } else {
                self?.consoleState.log("STT disconnected")
            }
            self?.consoleState.connectionState = .disconnected
        }
    }
}

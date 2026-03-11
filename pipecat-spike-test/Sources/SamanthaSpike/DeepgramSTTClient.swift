import Foundation
import os

private let log = Logger(subsystem: "com.samantha.spike", category: "DeepgramSTT")

protocol DeepgramSTTDelegate: AnyObject {
    func sttDidReceiveTranscript(_ text: String, isFinal: Bool)
    func sttDidDetectSpeechStart()
    func sttDidDetectUtteranceEnd()
    func sttDidConnect()
    func sttDidDisconnect(error: Error?)
}

struct DeepgramSTTConfig {
    var model: String = "nova-3"
    var language: String = "en"
    var sampleRate: Int = 24000
    var encoding: String = "linear16"
    var channels: Int = 1
    var interimResults: Bool = true
    var utteranceEndMs: Int = 1000
    var vadEvents: Bool = true
    var endpointing: Int = 300
    var punctuate: Bool = true
    var smartFormat: Bool = true
}

final class DeepgramSTTClient: NSObject, @unchecked Sendable {
    weak var delegate: DeepgramSTTDelegate?

    private let apiKey: String
    private let config: DeepgramSTTConfig
    private let queue = DispatchQueue(label: "com.samantha.deepgram-stt")

    private var session: URLSession?
    private var task: URLSessionWebSocketTask?
    private var keepaliveTimer: DispatchSourceTimer?
    private var connected = false
    private var intentionalDisconnect = false
    private var reconnectAttempt = 0
    private let maxReconnectAttempts = 5
    private let baseReconnectDelay: TimeInterval = 1.0

    init(apiKey: String, config: DeepgramSTTConfig = DeepgramSTTConfig()) {
        self.apiKey = apiKey
        self.config = config
        super.init()
    }

    func connect() {
        queue.async { [weak self] in
            self?.doConnect()
        }
    }

    func disconnect() {
        queue.async { [weak self] in
            guard let self else { return }
            self.intentionalDisconnect = true
            self.sendCloseStream()
        }
    }

    func sendAudio(_ data: Data) {
        queue.async { [weak self] in
            guard let self, self.connected, let task = self.task else { return }
            task.send(.data(data)) { error in
                if let error {
                    log.error("Send audio failed: \(error.localizedDescription)")
                }
            }
        }
    }

    // MARK: - Connection

    private func doConnect() {
        teardown()
        intentionalDisconnect = false

        guard let url = buildURL() else {
            log.error("Failed to build Deepgram URL")
            return
        }

        var request = URLRequest(url: url)
        request.addValue("Token \(apiKey)", forHTTPHeaderField: "Authorization")

        let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        let task = session.webSocketTask(with: request)

        self.session = session
        self.task = task
        task.resume()

        log.info("Connecting to Deepgram STT...")
    }

    private func buildURL() -> URL? {
        var components = URLComponents()
        components.scheme = "wss"
        components.host = "api.deepgram.com"
        components.path = "/v1/listen"
        components.queryItems = [
            URLQueryItem(name: "model", value: config.model),
            URLQueryItem(name: "encoding", value: config.encoding),
            URLQueryItem(name: "sample_rate", value: String(config.sampleRate)),
            URLQueryItem(name: "channels", value: String(config.channels)),
            URLQueryItem(name: "language", value: config.language),
            URLQueryItem(name: "interim_results", value: config.interimResults ? "true" : "false"),
            URLQueryItem(name: "utterance_end_ms", value: String(config.utteranceEndMs)),
            URLQueryItem(name: "vad_events", value: config.vadEvents ? "true" : "false"),
            URLQueryItem(name: "endpointing", value: String(config.endpointing)),
            URLQueryItem(name: "punctuate", value: config.punctuate ? "true" : "false"),
            URLQueryItem(name: "smart_format", value: config.smartFormat ? "true" : "false"),
        ]
        return components.url
    }

    // MARK: - Receive loop

    private func startReceiving() {
        task?.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                switch result {
                case .success(let message):
                    self.handleMessage(message)
                    self.startReceiving()
                case .failure(let error):
                    if !self.intentionalDisconnect {
                        log.error("Receive error: \(error.localizedDescription)")
                    }
                    self.handleDisconnect(error: self.intentionalDisconnect ? nil : error)
                }
            }
        }
    }

    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        guard case .string(let text) = message else { return }

        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = json["type"] as? String else {
            log.warning("Unrecognized message from Deepgram")
            return
        }

        switch type {
        case "Results":
            handleTranscriptResult(json)
        case "SpeechStarted":
            log.debug("Speech started")
            delegate?.sttDidDetectSpeechStart()
        case "UtteranceEnd":
            log.debug("Utterance end")
            delegate?.sttDidDetectUtteranceEnd()
        case "Metadata":
            if let requestId = (json["request_id"] as? String) ??
                (json["metadata"] as? [String: Any])?["request_id"] as? String {
                log.info("Session started (request_id: \(requestId))")
            }
        default:
            log.debug("Deepgram event: \(type)")
        }
    }

    private func handleTranscriptResult(_ json: [String: Any]) {
        guard let channel = json["channel"] as? [String: Any],
              let alternatives = channel["alternatives"] as? [[String: Any]],
              let first = alternatives.first,
              let transcript = first["transcript"] as? String else { return }

        guard !transcript.isEmpty else { return }

        let isFinal = json["is_final"] as? Bool ?? false
        log.debug("\(isFinal ? "Final" : "Interim"): \(transcript)")
        delegate?.sttDidReceiveTranscript(transcript, isFinal: isFinal)
    }

    // MARK: - Keepalive

    private func startKeepalive() {
        stopKeepalive()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 10, repeating: 10)
        timer.setEventHandler { [weak self] in
            guard let self, self.connected, let task = self.task else { return }
            let msg = "{\"type\":\"KeepAlive\"}"
            task.send(.string(msg)) { error in
                if let error {
                    log.error("Keepalive send failed: \(error.localizedDescription)")
                }
            }
        }
        timer.resume()
        keepaliveTimer = timer
    }

    private func stopKeepalive() {
        keepaliveTimer?.cancel()
        keepaliveTimer = nil
    }

    // MARK: - Close and reconnect

    private func sendCloseStream() {
        guard let task else {
            teardown()
            return
        }
        let msg = "{\"type\":\"CloseStream\"}"
        task.send(.string(msg)) { [weak self] _ in
            self?.queue.async {
                self?.task?.cancel(with: .normalClosure, reason: nil)
                self?.teardown()
                self?.delegate?.sttDidDisconnect(error: nil)
            }
        }
    }

    private func handleDisconnect(error: Error?) {
        let wasConnected = connected
        teardown()

        if wasConnected {
            delegate?.sttDidDisconnect(error: error)
        }

        guard !intentionalDisconnect, reconnectAttempt < maxReconnectAttempts else {
            if reconnectAttempt >= maxReconnectAttempts {
                log.error("Max reconnect attempts (\(self.maxReconnectAttempts)) reached")
            }
            return
        }

        let delay = baseReconnectDelay * pow(2.0, Double(reconnectAttempt))
        reconnectAttempt += 1
        log.info("Reconnecting in \(delay)s (attempt \(self.reconnectAttempt)/\(self.maxReconnectAttempts))")

        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, !self.intentionalDisconnect else { return }
            self.doConnect()
        }
    }

    private func teardown() {
        stopKeepalive()
        connected = false
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil
    }
}

// MARK: - URLSessionWebSocketDelegate

extension DeepgramSTTClient: URLSessionWebSocketDelegate {
    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didOpenWithProtocol protocol: String?
    ) {
        queue.async { [weak self] in
            guard let self else { return }
            self.connected = true
            self.reconnectAttempt = 0
            self.startKeepalive()
            self.startReceiving()
            log.info("Connected to Deepgram STT")
            self.delegate?.sttDidConnect()
        }
    }

    func urlSession(
        _ session: URLSession,
        webSocketTask: URLSessionWebSocketTask,
        didCloseWith closeCode: URLSessionWebSocketTask.CloseCode,
        reason: Data?
    ) {
        queue.async { [weak self] in
            guard let self else { return }
            let reasonStr = reason.flatMap { String(data: $0, encoding: .utf8) } ?? "none"
            log.info("WebSocket closed (code: \(closeCode.rawValue), reason: \(reasonStr))")
            self.handleDisconnect(error: nil)
        }
    }
}

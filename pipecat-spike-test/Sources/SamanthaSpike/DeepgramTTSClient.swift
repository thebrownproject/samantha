import Foundation
import os

private let log = Logger(subsystem: "com.samantha.spike", category: "DeepgramTTS")

final class DeepgramTTSClient: @unchecked Sendable {
    private let apiKey: String
    private let session: URLSession
    private let lock = NSLock()
    private var currentTask: Task<Void, Error>?

    var model: String = "aura-2-en"
    var onAudioChunk: ((Data) -> Void)?
    var onSpeechStart: (() -> Void)?
    var onSpeechEnd: (() -> Void)?

    init(apiKey: String) {
        self.apiKey = apiKey
        let config = URLSessionConfiguration.default
        config.requestCachePolicy = .reloadIgnoringLocalCacheData
        self.session = URLSession(configuration: config)
    }

    func speak(_ text: String) async throws {
        let task = Task { [weak self] in
            guard let self else { return }
            try await self.streamTTS(text: text)
        }

        lock.lock()
        currentTask = task
        lock.unlock()

        defer {
            lock.lock()
            if currentTask === task { currentTask = nil }
            lock.unlock()
        }

        try await task.value
    }

    func cancel() {
        lock.lock()
        let task = currentTask
        currentTask = nil
        lock.unlock()
        task?.cancel()
        log.debug("TTS cancel requested")
    }

    private func streamTTS(text: String) async throws {
        var components = URLComponents(string: "https://api.deepgram.com/v1/speak")!
        components.queryItems = [
            URLQueryItem(name: "model", value: model),
            URLQueryItem(name: "encoding", value: "linear16"),
            URLQueryItem(name: "sample_rate", value: "24000"),
            URLQueryItem(name: "container", value: "none"),
        ]

        var request = URLRequest(url: components.url!)
        request.httpMethod = "POST"
        request.setValue("Token \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body = try JSONEncoder().encode(["text": text])
        request.httpBody = body

        let (bytes, response) = try await session.bytes(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw DeepgramTTSError.invalidResponse
        }
        guard http.statusCode == 200 else {
            var errorBody = Data()
            for try await byte in bytes { errorBody.append(byte) }
            let message = String(data: errorBody, encoding: .utf8) ?? "Unknown error"
            log.error("TTS API \(http.statusCode): \(message)")
            throw DeepgramTTSError.httpError(statusCode: http.statusCode, message: message)
        }

        var firstChunk = true
        // Read in ~4KB chunks for low latency while keeping overhead reasonable
        let chunkSize = 4096
        var buffer = Data(capacity: chunkSize)

        for try await byte in bytes {
            try Task.checkCancellation()

            buffer.append(byte)

            if buffer.count >= chunkSize {
                if firstChunk {
                    firstChunk = false
                    onSpeechStart?()
                    log.debug("First TTS audio chunk received")
                }
                // Ensure even byte count (PCM16 = 2 bytes per sample)
                let usable = buffer.count - (buffer.count % 2)
                if usable > 0 {
                    onAudioChunk?(buffer.prefix(usable))
                    buffer = Data(buffer.suffix(from: usable))
                }
            }
        }

        // Flush remaining bytes
        if !buffer.isEmpty {
            if firstChunk {
                firstChunk = false
                onSpeechStart?()
            }
            let usable = buffer.count - (buffer.count % 2)
            if usable > 0 {
                onAudioChunk?(buffer.prefix(usable))
            }
        }

        onSpeechEnd?()
        log.debug("TTS stream complete for \(text.prefix(40))...")
    }
}

enum DeepgramTTSError: LocalizedError {
    case invalidResponse
    case httpError(statusCode: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return "Invalid HTTP response from Deepgram TTS"
        case .httpError(let code, let message):
            return "Deepgram TTS HTTP \(code): \(message)"
        }
    }
}

import Foundation
import os

private let log = Logger(subsystem: "com.samantha.spike", category: "OpenRouterLLM")

// MARK: - Message Types

struct ChatMessage: Codable, Sendable {
    let role: String
    var content: String?
    var tool_calls: [ToolCall]?
    var tool_call_id: String?
    var name: String?

    init(role: String, content: String? = nil, tool_calls: [ToolCall]? = nil,
         tool_call_id: String? = nil, name: String? = nil) {
        self.role = role
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id
        self.name = name
    }
}

struct ToolCall: Codable, Sendable {
    let id: String
    let type: String
    let function: FunctionCall
}

struct FunctionCall: Codable, Sendable {
    let name: String
    let arguments: String
}

struct ToolDefinition: Sendable {
    let type: String
    let function: FunctionDefinition
}

struct FunctionDefinition: Sendable {
    let name: String
    let description: String
    let parameters: JSONValue
}

// MARK: - JSON Value (Codable wrapper for arbitrary JSON)

enum JSONValue: Sendable, Codable, Equatable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let b = try? container.decode(Bool.self) {
            self = .bool(b)
        } else if let i = try? container.decode(Int.self) {
            self = .int(i)
        } else if let d = try? container.decode(Double.self) {
            self = .double(d)
        } else if let s = try? container.decode(String.self) {
            self = .string(s)
        } else if let arr = try? container.decode([JSONValue].self) {
            self = .array(arr)
        } else if let obj = try? container.decode([String: JSONValue].self) {
            self = .object(obj)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let b): try container.encode(b)
        case .int(let i): try container.encode(i)
        case .double(let d): try container.encode(d)
        case .string(let s): try container.encode(s)
        case .array(let arr): try container.encode(arr)
        case .object(let obj): try container.encode(obj)
        }
    }
}

// MARK: - Encodable conformances for request serialization

extension ToolDefinition: Encodable {
    enum CodingKeys: String, CodingKey { case type, function }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(function, forKey: .function)
    }
}

extension FunctionDefinition: Encodable {
    enum CodingKeys: String, CodingKey { case name, description, parameters }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name)
        try c.encode(description, forKey: .description)
        try c.encode(parameters, forKey: .parameters)
    }
}

// MARK: - SSE Chunk Types (internal, for parsing stream responses)

private struct StreamChunk: Decodable {
    let choices: [StreamChoice]?
}

private struct StreamChoice: Decodable {
    let delta: StreamDelta?
    let finish_reason: String?
}

private struct StreamDelta: Decodable {
    let role: String?
    let content: String?
    let tool_calls: [StreamToolCallDelta]?
}

private struct StreamToolCallDelta: Decodable {
    let index: Int?
    let id: String?
    let type: String?
    let function: StreamFunctionDelta?
}

private struct StreamFunctionDelta: Decodable {
    let name: String?
    let arguments: String?
}

// MARK: - Errors

enum OpenRouterError: Error, LocalizedError {
    case noAPIKey
    case httpError(statusCode: Int, body: String)
    case rateLimited(retryAfter: TimeInterval?)
    case modelUnavailable(String)
    case decodingError(String)
    case cancelled

    var errorDescription: String? {
        switch self {
        case .noAPIKey: return "OpenRouter API key not configured"
        case .httpError(let code, let body): return "HTTP \(code): \(body)"
        case .rateLimited(let retry):
            if let retry { return "Rate limited, retry after \(retry)s" }
            return "Rate limited"
        case .modelUnavailable(let model): return "Model unavailable: \(model)"
        case .decodingError(let msg): return "Decoding error: \(msg)"
        case .cancelled: return "Request cancelled"
        }
    }
}

// MARK: - Tool Call Accumulator

/// Accumulates incremental tool call deltas into complete ToolCall objects.
private struct ToolCallAccumulator {
    private var entries: [(id: String, type: String, name: String, arguments: String)] = []

    mutating func apply(_ deltas: [StreamToolCallDelta]) {
        for delta in deltas {
            let idx = delta.index ?? 0
            while entries.count <= idx {
                entries.append((id: "", type: "function", name: "", arguments: ""))
            }
            if let id = delta.id { entries[idx].id = id }
            if let type = delta.type { entries[idx].type = type }
            if let name = delta.function?.name { entries[idx].name = name }
            if let args = delta.function?.arguments { entries[idx].arguments += args }
        }
    }

    func build() -> [ToolCall] {
        entries.map { ToolCall(id: $0.id, type: $0.type, function: FunctionCall(name: $0.name, arguments: $0.arguments)) }
    }
}

// MARK: - Client

final class OpenRouterLLMClient: @unchecked Sendable {
    var model: String
    private let apiKey: String
    private let session: URLSession
    private let endpoint = URL(string: "https://openrouter.ai/api/v1/chat/completions")!

    /// Set by `stream()`, read by `cancel()`. Access from any thread.
    private let lock = NSLock()
    private var _activeTask: Task<Void, any Error>?

    init(apiKey: String, model: String = "anthropic/claude-sonnet-4-20250514") {
        self.apiKey = apiKey
        self.model = model
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 120
        self.session = URLSession(configuration: config)
    }

    /// Cancel any in-flight stream request.
    func cancel() {
        lock.withLock {
            _activeTask?.cancel()
            _activeTask = nil
        }
    }

    /// Stream a chat completion. Callbacks fire from the calling task's context.
    /// Cancel via `cancel()` or by cancelling the parent Task.
    func stream(
        messages: [ChatMessage],
        tools: [ToolDefinition]?,
        onTextDelta: @escaping @Sendable (String) -> Void,
        onToolCall: @escaping @Sendable (ToolCall) -> Void,
        onComplete: @escaping @Sendable (ChatMessage) -> Void
    ) async throws {
        let task = Task {
            try await self.performStream(
                messages: messages, tools: tools,
                onTextDelta: onTextDelta, onToolCall: onToolCall, onComplete: onComplete
            )
        }
        lock.withLock { _activeTask = task }
        defer { lock.withLock { _activeTask = nil } }
        try await task.value
    }

    private func performStream(
        messages: [ChatMessage],
        tools: [ToolDefinition]?,
        onTextDelta: @escaping @Sendable (String) -> Void,
        onToolCall: @escaping @Sendable (ToolCall) -> Void,
        onComplete: @escaping @Sendable (ChatMessage) -> Void
    ) async throws {
        let request = try buildRequest(messages: messages, tools: tools)

        let (bytes, response) = try await session.bytes(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw OpenRouterError.httpError(statusCode: 0, body: "Non-HTTP response")
        }

        try await checkHTTPStatus(httpResponse, bytes: bytes)

        var fullContent = ""
        var toolAccumulator = ToolCallAccumulator()
        var hasToolCalls = false

        for try await line in bytes.lines {
            try Task.checkCancellation()

            guard line.hasPrefix("data: ") else { continue }
            let payload = String(line.dropFirst(6))

            if payload == "[DONE]" { break }

            guard let data = payload.data(using: .utf8) else { continue }
            guard let chunk = try? JSONDecoder().decode(StreamChunk.self, from: data) else {
                log.debug("Skipped unparseable chunk")
                continue
            }

            guard let choice = chunk.choices?.first else { continue }

            if let text = choice.delta?.content {
                fullContent += text
                onTextDelta(text)
            }

            if let toolDeltas = choice.delta?.tool_calls, !toolDeltas.isEmpty {
                hasToolCalls = true
                toolAccumulator.apply(toolDeltas)
            }

            if choice.finish_reason == "tool_calls" {
                let calls = toolAccumulator.build()
                for call in calls { onToolCall(call) }
                let msg = ChatMessage(role: "assistant", content: fullContent.isEmpty ? nil : fullContent, tool_calls: calls)
                onComplete(msg)
                return
            }
        }

        let finalToolCalls = hasToolCalls ? toolAccumulator.build() : nil
        if let calls = finalToolCalls {
            for call in calls { onToolCall(call) }
        }
        let msg = ChatMessage(role: "assistant", content: fullContent.isEmpty ? nil : fullContent, tool_calls: finalToolCalls)
        onComplete(msg)
    }

    // MARK: - Private

    private func buildRequest(messages: [ChatMessage], tools: [ToolDefinition]?) throws -> URLRequest {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("https://github.com/thebrownproject/samantha", forHTTPHeaderField: "HTTP-Referer")
        request.setValue("Samantha", forHTTPHeaderField: "X-Title")

        var body: [String: Any] = [
            "model": model,
            "stream": true,
        ]

        let encoder = JSONEncoder()
        let messagesData = try encoder.encode(messages)
        body["messages"] = try JSONSerialization.jsonObject(with: messagesData)

        if let tools, !tools.isEmpty {
            let toolsData = try encoder.encode(tools)
            body["tools"] = try JSONSerialization.jsonObject(with: toolsData)
            body["tool_choice"] = "auto"
        }

        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        return request
    }

    private func checkHTTPStatus(_ response: HTTPURLResponse, bytes: URLSession.AsyncBytes) async throws {
        guard response.statusCode != 200 else { return }

        // Collect error body
        var errorBody = ""
        for try await line in bytes.lines {
            errorBody += line
            if errorBody.count > 2048 { break }
        }

        if response.statusCode == 429 {
            let retryAfter = response.value(forHTTPHeaderField: "Retry-After").flatMap(TimeInterval.init)
            log.warning("Rate limited. Retry-After: \(retryAfter ?? -1)")
            throw OpenRouterError.rateLimited(retryAfter: retryAfter)
        }

        if response.statusCode == 404 || errorBody.contains("model_not_available") || errorBody.contains("not found") {
            log.error("Model unavailable: \(self.model)")
            throw OpenRouterError.modelUnavailable(model)
        }

        log.error("HTTP \(response.statusCode): \(errorBody)")
        throw OpenRouterError.httpError(statusCode: response.statusCode, body: errorBody)
    }
}

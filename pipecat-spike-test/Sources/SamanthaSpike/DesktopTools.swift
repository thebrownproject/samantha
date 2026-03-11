import Foundation
import os

private let log = Logger(subsystem: "com.samantha.spike", category: "DesktopTools")

private let defaultDisplayPrompt =
    "Describe the current screen concisely for Samantha. Focus on the active app, " +
    "the visible page or document, notable dialogs, and any UI the user is likely referring to."

enum DesktopTools {

    /// Replace placeholder handlers for frontmost_app_context and capture_display
    /// with real implementations backed by DesktopContextToolExecutor and OpenAI vision.
    @MainActor
    static func register(on registry: ToolRegistry, executor: DesktopContextToolExecutor) {
        registry.register(
            name: "frontmost_app_context",
            description: "Return structured context about the frontmost app and window.",
            parameters: [:],
            required: [],
            handler: { _ in
                let result = try await executor.execute(tool: "frontmost_app_context", args: [:])
                let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
                return String(data: data, encoding: .utf8) ?? "{}"
            }
        )

        registry.register(
            name: "capture_display",
            description: "Capture the current display and return a concise vision summary plus metadata.",
            parameters: [
                "question": .object([
                    "type": .string("string"),
                    "description": .string("Optional question to focus the vision analysis"),
                ]),
            ],
            required: [],
            handler: { arguments in
                try await handleCaptureDisplay(arguments, executor: executor)
            }
        )
    }
}

// MARK: - capture_display

private func handleCaptureDisplay(_ arguments: String, executor: DesktopContextToolExecutor) async throws -> String {
    let question = parseQuestion(from: arguments)
    let prompt = question.isEmpty ? defaultDisplayPrompt : question

    let capture = try await executor.execute(tool: "capture_display", args: [:])

    guard let imageBase64 = capture["image_base64"] as? String, !imageBase64.isEmpty else {
        return errorJSON("capture_display", "missing image_base64 in capture payload")
    }

    let mimeType = capture["mime_type"] as? String ?? "image/png"

    guard let apiKey = KeychainHelper.loadAPIKey(for: .openAIAPIKey) else {
        return errorJSON("capture_display", "OpenAI API key not configured")
    }

    let summary = try await callVisionAPI(
        apiKey: apiKey,
        imageBase64: imageBase64,
        mimeType: mimeType,
        prompt: prompt
    )

    var payload: [String: Any] = [
        "summary": condenseForVoice(summary, maxChars: 500),
        "mime_type": mimeType,
    ]
    if let w = capture["width"] { payload["width"] = w }
    if let h = capture["height"] { payload["height"] = h }
    if let d = capture["display_id"] { payload["display_id"] = d }

    let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    return String(data: data, encoding: .utf8) ?? "{}"
}

// MARK: - OpenAI Vision API

private func callVisionAPI(
    apiKey: String,
    imageBase64: String,
    mimeType: String,
    prompt: String
) async throws -> String {
    let url = URL(string: "https://api.openai.com/v1/chat/completions")!
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
    request.timeoutInterval = 30

    let body: [String: Any] = [
        "model": "gpt-4o-mini",
        "messages": [
            [
                "role": "user",
                "content": [
                    ["type": "text", "text": prompt],
                    [
                        "type": "image_url",
                        "image_url": ["url": "data:\(mimeType);base64,\(imageBase64)"],
                    ],
                ],
            ] as [String: Any],
        ],
        "max_tokens": 300,
    ]

    request.httpBody = try JSONSerialization.data(withJSONObject: body)

    let (data, response) = try await URLSession.shared.data(for: request)

    if let http = response as? HTTPURLResponse, http.statusCode != 200 {
        let body = String(data: data, encoding: .utf8) ?? "(no body)"
        throw VisionAPIError.httpError(statusCode: http.statusCode, body: body)
    }

    guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let choices = json["choices"] as? [[String: Any]],
          let first = choices.first,
          let message = first["message"] as? [String: Any],
          let content = message["content"] as? String else {
        throw VisionAPIError.unexpectedResponse
    }

    return content
}

private enum VisionAPIError: LocalizedError {
    case httpError(statusCode: Int, body: String)
    case unexpectedResponse

    var errorDescription: String? {
        switch self {
        case .httpError(let code, let body):
            "OpenAI vision API returned \(code): \(body.prefix(200))"
        case .unexpectedResponse:
            "Unexpected response format from OpenAI vision API"
        }
    }
}

// MARK: - Helpers

private func parseQuestion(from arguments: String) -> String {
    guard let data = arguments.data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let q = obj["question"] as? String else {
        return ""
    }
    return q.trimmingCharacters(in: .whitespacesAndNewlines)
}

private func condenseForVoice(_ text: String, maxChars: Int) -> String {
    let out = text
        .replacingOccurrences(of: "```[\\s\\S]*?```", with: "", options: .regularExpression)
        .replacingOccurrences(of: "`([^`]+)`", with: "$1", options: .regularExpression)
        .replacingOccurrences(of: "^#{1,6}\\s+", with: "", options: .regularExpression)
        .replacingOccurrences(of: "^[\\s]*[-*]\\s+", with: "", options: .regularExpression)
        .replacingOccurrences(of: "\\*{1,2}([^*]+)\\*{1,2}", with: "$1", options: .regularExpression)
        .replacingOccurrences(of: "\\[([^\\]]+)\\]\\([^)]+\\)", with: "$1", options: .regularExpression)
        .replacingOccurrences(of: "\\n{2,}", with: " ", options: .regularExpression)
        .trimmingCharacters(in: .whitespacesAndNewlines)

    guard out.count > maxChars else { return out }

    let truncated = String(out.prefix(maxChars))
    if let range = truncated.range(of: ". ", options: .backwards) {
        return String(truncated[truncated.startIndex...range.lowerBound]) + "."
    }
    return truncated + "..."
}

private func errorJSON(_ tool: String, _ message: String) -> String {
    log.error("Error in \(tool): \(message)")
    let escaped = message
        .replacingOccurrences(of: "\\", with: "\\\\")
        .replacingOccurrences(of: "\"", with: "\\\"")
    return "{\"error\":\"Error in \(tool): \(escaped)\"}"
}

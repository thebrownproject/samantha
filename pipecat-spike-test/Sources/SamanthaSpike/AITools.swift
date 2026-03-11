import Foundation
import os

private let log = Logger(subsystem: "com.samantha.spike", category: "AITools")

private let maxDelegationOutput = 2048
private let delegationFallback = "I wasn't able to think that through deeply right now. Let me try to help directly."
private let delegationModel = "gpt-5-mini-2025-08-07"
private let webSearchModel = "gpt-4o-mini"
private let requestTimeout: TimeInterval = 30
private let maxRetries = 2
private let baseRetryDelay: TimeInterval = 1

// MARK: - reason_deeply

func reasonDeeplyHandler(_ arguments: String) async throws -> String {
    guard let data = arguments.data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let task = obj["task"] as? String, !task.isEmpty else {
        return delegationFallback
    }

    guard let apiKey = KeychainHelper.loadAPIKey(for: .openAIAPIKey) else {
        log.error("OpenAI API key not configured for reason_deeply")
        return delegationFallback
    }

    var lastError: Error?

    for attempt in 0..<maxRetries {
        if attempt > 0 {
            let delay = baseRetryDelay * pow(2.0, Double(attempt - 1))
            try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
        }

        do {
            let result = try await callChatCompletions(
                apiKey: apiKey,
                model: delegationModel,
                systemPrompt: Prompts.delegation,
                userMessage: task
            )
            let condensed = condenseForVoice(result, maxChars: maxDelegationOutput)
            log.info("reason_deeply succeeded on attempt \(attempt + 1)")
            return condensed
        } catch {
            lastError = error
            log.warning("reason_deeply attempt \(attempt + 1)/\(maxRetries) failed: \(error.localizedDescription)")
        }
    }

    log.error("reason_deeply exhausted retries: \(lastError?.localizedDescription ?? "unknown")")
    return delegationFallback
}

private func callChatCompletions(
    apiKey: String,
    model: String,
    systemPrompt: String,
    userMessage: String
) async throws -> String {
    let url = URL(string: "https://api.openai.com/v1/chat/completions")!
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
    request.timeoutInterval = requestTimeout

    let body: [String: Any] = [
        "model": model,
        "messages": [
            ["role": "system", "content": systemPrompt],
            ["role": "user", "content": userMessage],
        ],
    ]
    request.httpBody = try JSONSerialization.data(withJSONObject: body)

    let (data, response) = try await URLSession.shared.data(for: request)

    if let http = response as? HTTPURLResponse, http.statusCode != 200 {
        let body = String(data: data, encoding: .utf8) ?? "(no body)"
        throw AIToolError.httpError(statusCode: http.statusCode, body: body)
    }

    guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let choices = json["choices"] as? [[String: Any]],
          let first = choices.first,
          let message = first["message"] as? [String: Any],
          let content = message["content"] as? String else {
        throw AIToolError.unexpectedResponse("chat completions")
    }

    return content
}

// MARK: - web_search

func webSearchHandler(_ arguments: String) async throws -> String {
    guard let data = arguments.data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let query = obj["query"] as? String, !query.isEmpty else {
        return serializeWebSearchResponse(query: "", error: "Missing required argument: query")
    }

    guard let apiKey = KeychainHelper.loadAPIKey(for: .openAIAPIKey) else {
        log.error("OpenAI API key not configured for web_search")
        return serializeWebSearchResponse(query: query, error: "OpenAI API key not configured")
    }

    do {
        return try await callResponsesWebSearch(apiKey: apiKey, query: query)
    } catch {
        log.error("web_search failed: \(error.localizedDescription)")
        return serializeWebSearchResponse(query: query, error: error.localizedDescription)
    }
}

private func callResponsesWebSearch(apiKey: String, query: String) async throws -> String {
    let url = URL(string: "https://api.openai.com/v1/responses")!
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
    request.timeoutInterval = requestTimeout

    let body: [String: Any] = [
        "model": webSearchModel,
        "tools": [["type": "web_search_preview"]],
        "input": query,
    ]
    request.httpBody = try JSONSerialization.data(withJSONObject: body)

    let (data, response) = try await URLSession.shared.data(for: request)

    if let http = response as? HTTPURLResponse, http.statusCode != 200 {
        let respBody = String(data: data, encoding: .utf8) ?? "(no body)"
        throw AIToolError.httpError(statusCode: http.statusCode, body: respBody)
    }

    guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          let output = json["output"] as? [[String: Any]] else {
        throw AIToolError.unexpectedResponse("responses web search")
    }

    var summaryParts: [String] = []
    var results: [[String: String]] = []
    var seen = Set<String>()

    for item in output {
        guard (item["type"] as? String) == "message",
              let content = item["content"] as? [[String: Any]] else { continue }

        for block in content {
            if let text = block["text"] as? String {
                let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                if !trimmed.isEmpty { summaryParts.append(trimmed) }
            }
            if let annotations = block["annotations"] as? [[String: Any]] {
                for ann in annotations {
                    guard let title = ann["title"] as? String,
                          let annURL = ann["url"] as? String else { continue }
                    let t = title.trimmingCharacters(in: .whitespacesAndNewlines)
                    let u = annURL.trimmingCharacters(in: .whitespacesAndNewlines)
                    let key = "\(t)|\(u)"
                    guard !t.isEmpty, !u.isEmpty, !seen.contains(key) else { continue }
                    seen.insert(key)
                    results.append(["title": t, "url": u])
                }
            }
        }
    }

    return serializeWebSearchResponse(
        query: query,
        summary: summaryParts.joined(separator: " "),
        results: results
    )
}

private func serializeWebSearchResponse(
    query: String,
    summary: String = "",
    results: [[String: String]] = [],
    error: String? = nil
) -> String {
    var payload: [String: Any] = [
        "query": query,
        "summary": summary.trimmingCharacters(in: .whitespacesAndNewlines),
        "results": results,
    ]
    if let error { payload["error"] = error }

    guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
          let str = String(data: data, encoding: .utf8) else {
        return "{\"query\":\"\",\"summary\":\"\",\"results\":[]}"
    }
    return str
}

// MARK: - Shared

private enum AIToolError: LocalizedError {
    case httpError(statusCode: Int, body: String)
    case unexpectedResponse(String)

    var errorDescription: String? {
        switch self {
        case .httpError(let code, let body):
            "OpenAI API returned \(code): \(String(body.prefix(200)))"
        case .unexpectedResponse(let api):
            "Unexpected response format from OpenAI \(api) API"
        }
    }
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

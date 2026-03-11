import Foundation
import os

private let wsLog = Logger(subsystem: "com.samantha.app", category: "WebSocketClient")
private let wsProtocolVersion = 1

enum WebSocketConnectionState {
    case disconnected
    case connecting
    case connected
}

protocol AppToolExecutor: AnyObject {
    func execute(tool: String, args: [String: Any]) async throws -> [String: Any]
}

protocol WebSocketClientDelegate: AnyObject {
    func webSocketClient(_ client: WebSocketClient, didChangeConnectionState state: WebSocketConnectionState)
    func webSocketClient(_ client: WebSocketClient, didReceiveState state: AppState)
    func webSocketClient(_ client: WebSocketClient, didReceiveTranscriptRole role: String, text: String, isFinal: Bool)
    func webSocketClient(_ client: WebSocketClient, didReceiveAudio data: Data)
    func webSocketClient(_ client: WebSocketClient, didReceiveToolMessage message: [String: Any])
    func webSocketClientDidClearPlayback(_ client: WebSocketClient)
    func webSocketClient(_ client: WebSocketClient, didReceiveError message: String)
}

extension WebSocketClientDelegate {
    func webSocketClient(_ client: WebSocketClient, didChangeConnectionState state: WebSocketConnectionState) {}
    func webSocketClient(_ client: WebSocketClient, didReceiveState state: AppState) {}
    func webSocketClient(_ client: WebSocketClient, didReceiveTranscriptRole role: String, text: String, isFinal: Bool) {}
    func webSocketClient(_ client: WebSocketClient, didReceiveAudio data: Data) {}
    func webSocketClient(_ client: WebSocketClient, didReceiveToolMessage message: [String: Any]) {}
    func webSocketClientDidClearPlayback(_ client: WebSocketClient) {}
    func webSocketClient(_ client: WebSocketClient, didReceiveError message: String) {}
}

@MainActor
final class WebSocketClient: ObservableObject {
    @Published private(set) var connectionState: WebSocketConnectionState = .disconnected

    weak var delegate: WebSocketClientDelegate?
    weak var appToolExecutor: AppToolExecutor?

    private let url: URL
    private let session: URLSession
    private var socketTask: URLSessionWebSocketTask?
    private var receiveTask: Task<Void, Never>?
    private var reconnectTask: Task<Void, Never>?
    private var shouldReconnect = false
    private var reconnectAttempt = 0

    init(url: URL = URL(string: "ws://localhost:9090")!) {
        self.url = url
        self.session = URLSession(configuration: .default)
    }

    func connect() {
        shouldReconnect = true
        guard socketTask == nil, receiveTask == nil else { return }
        Task { await openConnection() }
    }

    func disconnect() {
        shouldReconnect = false
        reconnectTask?.cancel()
        reconnectTask = nil
        receiveTask?.cancel()
        receiveTask = nil
        socketTask?.cancel(with: .normalClosure, reason: nil)
        socketTask = nil
        connectionState = .disconnected
        delegate?.webSocketClient(self, didChangeConnectionState: .disconnected)
    }

    func startListening() async throws {
        try await sendJSON(["type": "start_listening"])
    }

    func stopListening() async throws {
        try await sendJSON(["type": "stop_listening"])
    }

    func interrupt() async throws {
        try await sendJSON(["type": "interrupt"])
    }

    func sendAudio(_ data: Data) async {
        guard let socketTask else { return }
        do {
            try await socketTask.send(.data(data))
        } catch {
            wsLog.error("Audio send failed: \(error.localizedDescription)")
        }
    }

    private func openConnection() async {
        guard shouldReconnect else { return }

        let task = session.webSocketTask(with: url)
        socketTask = task
        connectionState = .connecting
        delegate?.webSocketClient(self, didChangeConnectionState: .connecting)
        task.resume()

        connectionState = .connected
        reconnectAttempt = 0
        delegate?.webSocketClient(self, didChangeConnectionState: .connected)

        do {
            try await sendJSON(["type": "get_state"])
        } catch {
            wsLog.error("Initial get_state send failed: \(error.localizedDescription)")
        }

        receiveTask = Task { [weak self] in
            await self?.receiveLoop(for: task)
        }
    }

    private func receiveLoop(for task: URLSessionWebSocketTask) async {
        while !Task.isCancelled {
            do {
                let message = try await task.receive()
                switch message {
                case .string(let text):
                    await handleText(text)
                case .data(let data):
                    delegate?.webSocketClient(self, didReceiveAudio: data)
                @unknown default:
                    continue
                }
            } catch {
                wsLog.error("Receive loop ended: \(error.localizedDescription)")
                await handleDisconnect()
                return
            }
        }
    }

    private func handleDisconnect() async {
        receiveTask?.cancel()
        receiveTask = nil
        socketTask = nil
        connectionState = .disconnected
        delegate?.webSocketClient(self, didChangeConnectionState: .disconnected)

        guard shouldReconnect else { return }
        reconnectAttempt += 1
        let delay = min(pow(2.0, Double(reconnectAttempt - 1)), 5.0)
        reconnectTask?.cancel()
        reconnectTask = Task { [weak self] in
            guard let self else { return }
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            await self.openConnection()
        }
    }

    private func handleText(_ text: String) async {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            delegate?.webSocketClient(self, didReceiveError: "Invalid websocket JSON payload")
            return
        }

        guard (json["protocol_version"] as? Int) == wsProtocolVersion else {
            delegate?.webSocketClient(self, didReceiveError: "Unsupported websocket protocol version")
            return
        }

        guard let type = json["type"] as? String else {
            delegate?.webSocketClient(self, didReceiveError: "Missing websocket message type")
            return
        }

        switch type {
        case "state_change":
            if let raw = json["state"] as? String, let state = AppState(rawValue: raw) {
                delegate?.webSocketClient(self, didReceiveState: state)
            }
        case "transcript":
            if let role = json["role"] as? String, let text = json["text"] as? String {
                let isFinal = json["final"] as? Bool ?? false
                delegate?.webSocketClient(self, didReceiveTranscriptRole: role, text: text, isFinal: isFinal)
            }
        case "clear_playback":
            delegate?.webSocketClientDidClearPlayback(self)
        case "error":
            delegate?.webSocketClient(self, didReceiveError: json["message"] as? String ?? "Unknown websocket error")
        case "tool_start", "tool_end", "tool_approval_required":
            delegate?.webSocketClient(self, didReceiveToolMessage: json)
        case "app_tool_call":
            await handleAppToolCall(json)
        default:
            break
        }
    }

    private func handleAppToolCall(_ message: [String: Any]) async {
        guard let requestID = message["request_id"] as? String, !requestID.isEmpty else {
            try? await sendAppToolResult(requestID: "missing", ok: false, result: nil, error: "Missing request_id")
            return
        }

        guard let tool = message["tool"] as? String, !tool.isEmpty else {
            try? await sendAppToolResult(requestID: requestID, ok: false, result: nil, error: "Missing tool")
            return
        }

        let args = message["args"] as? [String: Any] ?? [:]
        guard let appToolExecutor else {
            try? await sendAppToolResult(
                requestID: requestID,
                ok: false,
                result: nil,
                error: "No app tool executor configured"
            )
            return
        }

        do {
            let result = try await appToolExecutor.execute(tool: tool, args: args)
            try await sendAppToolResult(requestID: requestID, ok: true, result: result, error: nil)
        } catch {
            try? await sendAppToolResult(
                requestID: requestID,
                ok: false,
                result: nil,
                error: error.localizedDescription
            )
        }
    }

    private func sendAppToolResult(
        requestID: String,
        ok: Bool,
        result: [String: Any]?,
        error: String?
    ) async throws {
        var payload: [String: Any] = [
            "type": "app_tool_result",
            "request_id": requestID,
            "ok": ok,
        ]
        if let result {
            payload["result"] = result
        }
        if let error {
            payload["error"] = error
        }
        try await sendJSON(payload)
    }

    private func sendJSON(_ payload: [String: Any]) async throws {
        guard let socketTask else {
            throw URLError(.notConnectedToInternet)
        }
        var message = payload
        message["protocol_version"] = wsProtocolVersion
        let data = try JSONSerialization.data(withJSONObject: message)
        guard let text = String(data: data, encoding: .utf8) else {
            throw URLError(.cannotEncodeContentData)
        }
        try await socketTask.send(.string(text))
    }
}

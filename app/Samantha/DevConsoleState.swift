import Foundation
import Observation

@Observable @MainActor
final class DevConsoleState {
    var appState: AppState = .idle
    var connectionState: WebSocketConnectionState = .disconnected
    var isCapturing = false
    var isPlaying = false

    var logEntries: [ConsoleLogEntry] = []
    var transcriptEntries: [TranscriptEntry] = []
    var pendingApprovals: [PendingApproval] = []

    private static let maxLogEntries = 500

    func log(_ message: String, level: LogLevel = .info) {
        logEntries.append(ConsoleLogEntry(timestamp: Date(), level: level, message: message))
        if logEntries.count > Self.maxLogEntries {
            logEntries.removeFirst(logEntries.count - Self.maxLogEntries)
        }
    }

    func addTranscript(role: String, text: String, isFinal: Bool) {
        if let last = transcriptEntries.last, last.role == role, !last.isFinal {
            transcriptEntries[transcriptEntries.count - 1].text = text
            transcriptEntries[transcriptEntries.count - 1].isFinal = isFinal
        } else {
            transcriptEntries.append(TranscriptEntry(role: role, text: text, isFinal: isFinal))
        }
        if transcriptEntries.count > 50 {
            transcriptEntries.removeFirst(transcriptEntries.count - 50)
        }
    }

    func addApproval(callId: String, toolName: String, argsDescription: String) {
        let approval = PendingApproval(
            id: callId,
            toolName: toolName,
            argsDescription: argsDescription,
            timestamp: Date()
        )
        pendingApprovals.append(approval)
    }

    func removeApproval(callId: String) {
        pendingApprovals.removeAll { $0.id == callId }
    }

    func clearLog() {
        logEntries.removeAll()
    }
}

struct ConsoleLogEntry: Identifiable {
    let id = UUID()
    let timestamp: Date
    let level: LogLevel
    let message: String
}

enum LogLevel: String {
    case info, warning, error, debug
}

struct PendingApproval: Identifiable {
    let id: String
    let toolName: String
    let argsDescription: String
    let timestamp: Date
}

import AppKit
import SwiftUI

enum AppState: String, CaseIterable {
    case idle
    case listening
    case thinking
    case speaking
    case error
}

@MainActor
final class OrbState: ObservableObject {
    @Published var appState: AppState = .idle

    @Published var windowOrigin: CGPoint? {
        didSet {
            guard let origin = windowOrigin else { return }
            UserDefaults.standard.set(origin.x, forKey: Self.orbXKey)
            UserDefaults.standard.set(origin.y, forKey: Self.orbYKey)
        }
    }

    private static let orbXKey = "orbX"
    private static let orbYKey = "orbY"

    func loadSavedPosition() -> CGPoint? {
        guard UserDefaults.standard.object(forKey: Self.orbXKey) != nil else { return nil }
        let x = UserDefaults.standard.double(forKey: Self.orbXKey)
        let y = UserDefaults.standard.double(forKey: Self.orbYKey)
        return CGPoint(x: x, y: y)
    }
}

@MainActor
class FloatingPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class OrbWindowController {
    static let orbSize: CGFloat = 80

    private var panel: FloatingPanel?
    let orbState = OrbState()
    let transcriptStore = TranscriptStore()
    private(set) var transcriptController: TranscriptPanelController?

    func showWindow() {
        if panel != nil {
            panel?.orderFrontRegardless()
            return
        }

        let contentView = OrbView(state: orbState)
        let hosting = NSHostingView(rootView: contentView)
        let size = NSSize(width: Self.orbSize, height: Self.orbSize)
        hosting.frame = NSRect(origin: .zero, size: size)

        let panel = FloatingPanel(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isMovableByWindowBackground = true
        panel.contentView = hosting

        let origin = orbState.loadSavedPosition() ?? Self.defaultOrigin()
        panel.setFrameOrigin(origin)

        panel.orderFrontRegardless()
        self.panel = panel

        startTrackingPosition()
        setupTranscriptPanel()
    }

    func hideWindow() {
        panel?.orderOut(nil)
    }

    private static func defaultOrigin() -> CGPoint {
        guard let screen = NSScreen.main else { return .zero }
        let frame = screen.visibleFrame
        return CGPoint(
            x: frame.maxX - orbSize - 40,
            y: frame.minY + 40
        )
    }

    private func setupTranscriptPanel() {
        guard let panel else { return }
        let controller = TranscriptPanelController(store: transcriptStore)
        controller.showPanel(relativeTo: panel.frame)
        transcriptController = controller
    }

    private func startTrackingPosition() {
        NotificationCenter.default.addObserver(
            forName: NSWindow.didMoveNotification,
            object: panel,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                guard let self, let frame = self.panel?.frame else { return }
                self.orbState.windowOrigin = frame.origin
                self.transcriptController?.repositionPanel(orbFrame: frame)
            }
        }
    }
}

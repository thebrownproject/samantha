import AppKit
import Combine
import SwiftUI

struct TranscriptEntry: Identifiable {
    let id = UUID()
    let role: String
    var text: String
    var isFinal: Bool
    let timestamp: Date

    var isUser: Bool { role == "user" }
}

@MainActor
final class TranscriptStore: ObservableObject {
    @Published private(set) var entries: [TranscriptEntry] = []
    @Published var isVisible: Bool {
        didSet { UserDefaults.standard.set(isVisible, forKey: Self.visibilityKey) }
    }

    static let visibilityKey = "transcriptVisible"
    private static let maxEntries = 20
    private var defaultsObserver: AnyCancellable?

    init() {
        isVisible = UserDefaults.standard.bool(forKey: Self.visibilityKey)
        // Sync with @AppStorage changes from SettingsView.
        defaultsObserver = NotificationCenter.default
            .publisher(for: UserDefaults.didChangeNotification)
            .receive(on: RunLoop.main)
            .sink { [weak self] _ in
                guard let self else { return }
                let stored = UserDefaults.standard.bool(forKey: Self.visibilityKey)
                if self.isVisible != stored { self.isVisible = stored }
            }
    }

    /// Accepts a decoded IPC transcript message from WebSocket.
    /// Merges partial updates for the same role into one entry, replaces on final.
    func handleTranscript(role: String, text: String, isFinal: Bool) {
        if let last = entries.last, last.role == role, !last.isFinal {
            entries[entries.count - 1].text = text
            entries[entries.count - 1].isFinal = isFinal
        } else {
            entries.append(TranscriptEntry(role: role, text: text, isFinal: isFinal, timestamp: .now))
        }
        if entries.count > Self.maxEntries {
            entries.removeFirst(entries.count - Self.maxEntries)
        }
    }

    func clear() {
        entries.removeAll()
    }
}

// MARK: - Overlay View

struct TranscriptOverlay: View {
    @ObservedObject var store: TranscriptStore

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 6) {
                    ForEach(store.entries) { entry in
                        TranscriptBubble(entry: entry)
                            .id(entry.id)
                    }
                }
                .padding(10)
            }
            .onChange(of: store.entries.count) { _, _ in
                if let last = store.entries.last {
                    withAnimation(.easeOut(duration: 0.15)) {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }
        }
        .frame(width: 260, height: 200)
        .background(.ultraThinMaterial.opacity(0.85))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

private struct TranscriptBubble: View {
    let entry: TranscriptEntry

    var body: some View {
        HStack {
            if !entry.isUser { Spacer(minLength: 20) }
            Text(entry.text)
                .font(.system(size: 12))
                .foregroundStyle(entry.isFinal ? .primary : .secondary)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(bubbleBackground)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            if entry.isUser { Spacer(minLength: 20) }
        }
    }

    private var bubbleBackground: some ShapeStyle {
        entry.isUser
            ? AnyShapeStyle(Color.accentColor.opacity(0.15))
            : AnyShapeStyle(Color(white: 0.5, opacity: 0.12))
    }
}

// MARK: - Transcript Panel

@MainActor
final class TranscriptPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class TranscriptPanelController {
    private var panel: TranscriptPanel?
    private let store: TranscriptStore
    private var visibilityObserver: AnyCancellable?

    init(store: TranscriptStore) {
        self.store = store
    }

    func showPanel(relativeTo orbFrame: NSRect) {
        if panel != nil {
            repositionPanel(orbFrame: orbFrame)
            updateVisibility()
            return
        }

        let overlay = TranscriptOverlay(store: store)
        let hosting = NSHostingView(rootView: overlay)
        let size = NSSize(width: 260, height: 200)
        hosting.frame = NSRect(origin: .zero, size: size)

        let panel = TranscriptPanel(
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
        panel.contentView = hosting
        panel.ignoresMouseEvents = false

        self.panel = panel
        repositionPanel(orbFrame: orbFrame)
        updateVisibility()
        observeVisibility()
    }

    func repositionPanel(orbFrame: NSRect) {
        guard let panel else { return }
        let panelSize = panel.frame.size
        let x = orbFrame.midX - panelSize.width / 2
        let y = orbFrame.maxY + 8
        panel.setFrameOrigin(CGPoint(x: x, y: y))
    }

    func updateVisibility() {
        if store.isVisible {
            panel?.orderFrontRegardless()
        } else {
            panel?.orderOut(nil)
        }
    }

    func hidePanel() {
        panel?.orderOut(nil)
    }

    private func observeVisibility() {
        visibilityObserver = store.$isVisible.sink { [weak self] _ in
            self?.updateVisibility()
        }
    }
}

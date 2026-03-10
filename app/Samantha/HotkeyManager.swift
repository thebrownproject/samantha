import AppKit
import KeyboardShortcuts

extension KeyboardShortcuts.Name {
    static let toggleListening = Self("toggleListening", default: .init(.s, modifiers: [.option]))
    static let openSettings = Self("openSettings", default: .init(.s, modifiers: [.option, .shift]))
}

protocol HotkeyManagerDelegate: AnyObject {
    func hotkeyManagerDidDetectKeyDown(_ manager: HotkeyManager)
    func hotkeyManagerDidDetectKeyUp(_ manager: HotkeyManager)
}

@MainActor
final class HotkeyManager: ObservableObject {
    static let listenDefaultShortcut = KeyboardShortcuts.Shortcut(.s, modifiers: [.option])
    static let settingsDefaultShortcut = KeyboardShortcuts.Shortcut(.s, modifiers: [.option, .shift])

    @Published var shortcutDisplay: String = ""
    @Published var settingsShortcutDisplay: String = ""
    weak var delegate: HotkeyManagerDelegate?
    private var isListeningKeyHeld = false
    private var shortcutChangeObserver: NSObjectProtocol?

    init() {
        updateShortcutDisplay()
        updateSettingsShortcutDisplay()
        observeShortcutChanges()
        repairShortcutAssignmentsIfNeeded()

        KeyboardShortcuts.onKeyDown(for: .toggleListening) { [weak self] in
            guard let self else { return }
            // OS key-repeat fires repeated keyDown events; ignore while held.
            guard !self.isListeningKeyHeld else { return }
            self.isListeningKeyHeld = true
            self.delegate?.hotkeyManagerDidDetectKeyDown(self)
        }

        KeyboardShortcuts.onKeyUp(for: .toggleListening) { [weak self] in
            guard let self else { return }
            self.isListeningKeyHeld = false
            self.delegate?.hotkeyManagerDidDetectKeyUp(self)
        }

        KeyboardShortcuts.onKeyUp(for: .openSettings) {
            guard !Self.shortcutsConflict(.openSettings, .toggleListening) else { return }
            NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    deinit {
        if let shortcutChangeObserver {
            NotificationCenter.default.removeObserver(shortcutChangeObserver)
        }
    }

    // MARK: - Display

    func updateShortcutDisplay() {
        shortcutDisplay = Self.displayString(for: .toggleListening)
    }

    func updateSettingsShortcutDisplay() {
        settingsShortcutDisplay = Self.displayString(for: .openSettings)
    }

    private static func displayString(for name: KeyboardShortcuts.Name) -> String {
        guard let shortcut = KeyboardShortcuts.getShortcut(for: name) else { return "Not set" }
        return shortcut.description
    }

    // MARK: - Conflict detection and repair

    private static func shortcutsConflict(
        _ lhs: KeyboardShortcuts.Name,
        _ rhs: KeyboardShortcuts.Name
    ) -> Bool {
        guard
            let lhsShortcut = KeyboardShortcuts.getShortcut(for: lhs),
            let rhsShortcut = KeyboardShortcuts.getShortcut(for: rhs)
        else { return false }
        return lhsShortcut == rhsShortcut
    }

    nonisolated static func repairedShortcuts(
        toggleListening: KeyboardShortcuts.Shortcut?,
        openSettings: KeyboardShortcuts.Shortcut?
    ) -> (toggleListening: KeyboardShortcuts.Shortcut, openSettings: KeyboardShortcuts.Shortcut, didRepair: Bool) {
        var repairedListen: KeyboardShortcuts.Shortcut
        var repairedSettings: KeyboardShortcuts.Shortcut

        if toggleListening == nil
            || openSettings == listenDefaultShortcut
            || (toggleListening != nil && openSettings != nil && toggleListening == openSettings) {
            repairedListen = listenDefaultShortcut
        } else {
            repairedListen = toggleListening!
        }

        if openSettings == nil
            || openSettings == listenDefaultShortcut
            || openSettings == repairedListen {
            repairedSettings = settingsDefaultShortcut
        } else {
            repairedSettings = openSettings!
        }

        // Final invariant: shortcuts must never match.
        if repairedListen == repairedSettings {
            repairedListen = listenDefaultShortcut
            if repairedSettings == repairedListen {
                repairedSettings = settingsDefaultShortcut
            }
        }

        let didRepair = repairedListen != toggleListening || repairedSettings != openSettings
        return (repairedListen, repairedSettings, didRepair)
    }

    private func repairShortcutAssignmentsIfNeeded() {
        let currentListen = KeyboardShortcuts.getShortcut(for: .toggleListening)
        let currentSettings = KeyboardShortcuts.getShortcut(for: .openSettings)

        let repaired = Self.repairedShortcuts(
            toggleListening: currentListen,
            openSettings: currentSettings
        )
        guard repaired.didRepair else { return }

        KeyboardShortcuts.setShortcut(repaired.toggleListening, for: .toggleListening)
        KeyboardShortcuts.setShortcut(repaired.openSettings, for: .openSettings)
        updateShortcutDisplay()
        updateSettingsShortcutDisplay()
    }

    // MARK: - Shortcut change observation

    private func observeShortcutChanges() {
        shortcutChangeObserver = NotificationCenter.default.addObserver(
            forName: Notification.Name("KeyboardShortcuts_shortcutByNameDidChange"),
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard
                let name = notification.userInfo?["name"] as? KeyboardShortcuts.Name,
                name == .toggleListening || name == .openSettings
            else { return }

            Task { @MainActor [weak self] in
                if name == .toggleListening {
                    self?.updateShortcutDisplay()
                } else {
                    self?.updateSettingsShortcutDisplay()
                }
                self?.repairShortcutAssignmentsIfNeeded()
            }
        }
    }
}

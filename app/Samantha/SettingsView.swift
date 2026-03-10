import SwiftUI
import KeyboardShortcuts

enum SamanthaVoice: String, CaseIterable, Identifiable {
    case alloy, ash, ballad, coral, echo, sage, shimmer, verse
    var id: String { rawValue }
    var displayName: String { rawValue.capitalized }
}

struct SettingsView: View {
    @State private var apiKey: String = ""
    @State private var apiKeyError: String?
    @FocusState private var isAPIKeyFieldFocused: Bool

    @AppStorage("selectedVoice") private var selectedVoice: SamanthaVoice = .ash
    @AppStorage("safeModeEnabled") private var safeModeEnabled = true
    @AppStorage("confirmDestructive") private var confirmDestructive = true
    @AppStorage("memoryEnabled") private var memoryEnabled = true
    @AppStorage(TranscriptStore.visibilityKey) private var transcriptVisible = false

    var body: some View {
        Form {
            apiKeySection
            voiceSection
            hotkeySection
            generalSection
        }
        .formStyle(.grouped)
        .frame(width: 420, height: 460)
        .onAppear {
            apiKey = KeychainHelper.loadAPIKey() ?? ""
            apiKeyError = nil
        }
        .onDisappear {
            persistAPIKey()
        }
    }

    // MARK: - Sections

    private var apiKeySection: some View {
        Section {
            SecureField("", text: $apiKey, prompt: Text("sk-..."))
                .textFieldStyle(.roundedBorder)
                .font(.system(.body, design: .monospaced))
                .labelsHidden()
                .focused($isAPIKeyFieldFocused)
                .onSubmit { persistAPIKey() }
                .onChange(of: apiKey) { _, _ in apiKeyError = nil }
                .onChange(of: isAPIKeyFieldFocused) { wasFocused, isFocused in
                    if wasFocused && !isFocused { persistAPIKey() }
                }

            HStack {
                Text("Stored securely in macOS Keychain")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                Link("Get API key", destination: URL(string: "https://platform.openai.com/api-keys")!)
                    .font(.caption)
            }

            if let apiKeyError {
                Text(apiKeyError)
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        } header: {
            Text("OpenAI API Key")
        }
    }

    private var voiceSection: some View {
        Section("Voice") {
            Picker("Voice", selection: $selectedVoice) {
                ForEach(SamanthaVoice.allCases) { voice in
                    Text(voice.displayName).tag(voice)
                }
            }
        }
    }

    private var hotkeySection: some View {
        Section("Hotkeys") {
            HStack {
                Text("Activate")
                Spacer()
                KeyboardShortcuts.Recorder("", name: .toggleListening)
            }

            HStack {
                Text("Open Settings")
                Spacer()
                KeyboardShortcuts.Recorder("", name: .openSettings)
            }
        }
    }

    private var generalSection: some View {
        Section("General") {
            Toggle("Safe mode", isOn: $safeModeEnabled)
            Text("Restrict shell commands to a safe allowlist and disable write tools.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Toggle("Confirm destructive actions", isOn: $confirmDestructive)

            Toggle("Memory", isOn: $memoryEnabled)
            Text("Store and recall conversation context across sessions.")
                .font(.caption)
                .foregroundStyle(.secondary)

            Toggle("Show transcript", isOn: $transcriptVisible)
            Text("Display live transcript near the orb.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Persistence

    private func persistAPIKey() {
        let trimmed = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        let existing = KeychainHelper.loadAPIKey() ?? ""
        guard trimmed != existing else { return }

        if !trimmed.isEmpty && !trimmed.hasPrefix("sk-") {
            apiKeyError = "API key should start with \"sk-\"."
            return
        }

        if trimmed.isEmpty {
            KeychainHelper.deleteAPIKey()
            apiKey = ""
            apiKeyError = nil
            return
        }

        if KeychainHelper.saveAPIKey(trimmed) {
            apiKey = trimmed
            apiKeyError = nil
        } else {
            apiKeyError = "Failed to save API key to Keychain."
        }
    }
}

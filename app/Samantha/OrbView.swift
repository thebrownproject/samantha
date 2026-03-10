import SwiftUI

/// Placeholder orb visual. Displays a circle colored by AppState.
/// sam-pjm.2 will replace this with full animations and effects.
struct OrbView: View {
    @ObservedObject var state: OrbState

    var body: some View {
        Circle()
            .fill(fillColor.gradient)
            .frame(width: OrbWindowController.orbSize, height: OrbWindowController.orbSize)
            .shadow(color: fillColor.opacity(0.5), radius: 8, x: 0, y: 2)
    }

    private var fillColor: Color {
        switch state.appState {
        case .idle:      .gray
        case .listening: .blue
        case .thinking:  .orange
        case .speaking:  .green
        case .error:     .red
        }
    }
}

import SwiftUI

struct OrbView: View {
    @ObservedObject var state: OrbState

    private let size = OrbWindowController.orbSize

    var body: some View {
        TimelineView(.animation) { timeline in
            let t = timeline.date.timeIntervalSinceReferenceDate
            orbBody(time: t)
        }
        .frame(width: size, height: size)
    }

    @ViewBuilder
    private func orbBody(time: Double) -> some View {
        let s = state.appState
        let baseColor = Self.baseColor(for: s)
        let glowColor = Self.glowColor(for: s)

        ZStack {
            Circle()
                .fill(glowColor.opacity(glowOpacity(state: s, time: time)))
                .scaleEffect(glowScale(state: s, time: time))
                .blur(radius: 8)

            Circle()
                .fill(
                    RadialGradient(
                        colors: gradientColors(state: s, time: time),
                        center: gradientCenter(state: s, time: time),
                        startRadius: 0,
                        endRadius: size * 0.5
                    )
                )
                .scaleEffect(coreScale(state: s, time: time))
                .opacity(coreOpacity(state: s, time: time))

            Circle()
                .fill(
                    RadialGradient(
                        colors: [.white.opacity(0.3), .clear],
                        center: .init(x: 0.35, y: 0.3),
                        startRadius: 0,
                        endRadius: size * 0.35
                    )
                )
                .scaleEffect(coreScale(state: s, time: time) * 0.85)
        }
        .shadow(color: baseColor.opacity(shadowOpacity(state: s, time: time)), radius: 6, x: 0, y: 1)
        .animation(.easeInOut(duration: 0.4), value: s)
    }

    private static func baseColor(for state: AppState) -> Color {
        switch state {
        case .idle:      Color(white: 0.65)
        case .listening: Color(red: 0.25, green: 0.55, blue: 1.0)
        case .thinking:  Color(red: 1.0, green: 0.65, blue: 0.15)
        case .speaking:  Color(red: 0.2, green: 0.8, blue: 0.5)
        case .error:     Color(red: 1.0, green: 0.25, blue: 0.25)
        }
    }

    private static func glowColor(for state: AppState) -> Color {
        switch state {
        case .idle:      Color(white: 0.5)
        case .listening: Color(red: 0.3, green: 0.5, blue: 1.0)
        case .thinking:  Color(red: 1.0, green: 0.6, blue: 0.1)
        case .speaking:  Color(red: 0.15, green: 0.7, blue: 0.45)
        case .error:     Color(red: 1.0, green: 0.15, blue: 0.15)
        }
    }

    private func gradientColors(state: AppState, time: Double) -> [Color] {
        let base = Self.baseColor(for: state)
        switch state {
        case .thinking:
            let shift = sin(time * 2.5) * 0.1
            let warm = Color(red: 1.0, green: 0.55 + shift, blue: 0.1)
            return [warm, base.opacity(0.7)]
        default:
            return [base, base.opacity(0.6)]
        }
    }

    private func coreScale(state: AppState, time: Double) -> CGFloat {
        switch state {
        case .idle:
            return 0.72 + sin(time * 1.2) * 0.02
        case .listening:
            return 0.74 + sin(time * 2.0) * 0.04
        case .thinking:
            return 0.73 + sin(time * 3.0) * 0.015
        case .speaking:
            let a = sin(time * 3.5) * 0.03
            let b = sin(time * 5.7) * 0.015
            return 0.74 + a + b
        case .error:
            return 0.73 + sin(time * 6.0) * 0.04
        }
    }

    private func coreOpacity(state: AppState, time: Double) -> Double {
        switch state {
        case .idle:
            return 0.75 + sin(time * 1.2) * 0.08
        case .error:
            return 0.8 + sin(time * 6.0) * 0.15
        default:
            return 0.92
        }
    }

    private func glowScale(state: AppState, time: Double) -> CGFloat {
        switch state {
        case .idle:
            return 0.85 + sin(time * 1.2) * 0.03
        case .listening:
            return 0.92 + sin(time * 2.0) * 0.06
        case .thinking:
            return 0.88 + sin(time * 3.0) * 0.04
        case .speaking:
            return 0.9 + sin(time * 3.5) * 0.05 + sin(time * 5.7) * 0.02
        case .error:
            return 0.9 + sin(time * 6.0) * 0.06
        }
    }

    private func glowOpacity(state: AppState, time: Double) -> Double {
        switch state {
        case .idle:
            return 0.15 + sin(time * 1.2) * 0.05
        case .listening:
            return 0.35 + sin(time * 2.0) * 0.1
        case .thinking:
            return 0.25 + sin(time * 3.0) * 0.08
        case .speaking:
            return 0.3 + sin(time * 3.5) * 0.1
        case .error:
            return 0.4 + sin(time * 6.0) * 0.15
        }
    }

    private func gradientCenter(state: AppState, time: Double) -> UnitPoint {
        switch state {
        case .thinking:
            let x = 0.5 + cos(time * 2.0) * 0.15
            let y = 0.5 + sin(time * 2.0) * 0.15
            return UnitPoint(x: x, y: y)
        default:
            return UnitPoint(x: 0.45, y: 0.4)
        }
    }

    private func shadowOpacity(state: AppState, time: Double) -> Double {
        switch state {
        case .idle:      0.2
        case .listening: 0.4 + sin(time * 2.0) * 0.1
        case .thinking:  0.3
        case .speaking:  0.35 + sin(time * 3.5) * 0.08
        case .error:     0.5 + sin(time * 6.0) * 0.15
        }
    }
}

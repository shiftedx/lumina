import SwiftUI
import UIKit
import AVFoundation
import VisionKit
import LuminaCore

struct LibraryStableNavigationBar: ViewModifier {
    func body(content: Content) -> some View {
        if #available(iOS 27.0, *) {
            content.toolbarMinimizationBehavior(.never, for: .navigationBar)
        } else { content }
    }
}

struct HomeHeroScrollEdge: ViewModifier {
    var isEnabled: Bool

    func body(content: Content) -> some View {
        if #available(iOS 26.0, *) {
            content.scrollEdgeEffectHidden(isEnabled, for: .top)
        } else { content }
    }
}

struct VideoDetailChrome: ViewModifier {
    var isEnabled = true

    func body(content: Content) -> some View {
        if isEnabled {
            content
                .ignoresSafeArea(.container)
                .modifier(HomeHeroScrollEdge(isEnabled: true))
                .navigationTitle("")
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(.hidden, for: .navigationBar)
                .toolbarColorScheme(.dark, for: .navigationBar)
        } else {
            content
        }
    }
}

struct PortraitNavigationHeader<Categories: View>: View {
    @ViewBuilder let categories: Categories
    @Environment(\.layoutDirection) private var layoutDirection

    var body: some View {
        HStack(spacing: 8) {
            HomeHeroChrome()
            categories.frame(maxWidth: .infinity).clipped()
        }
        .padding(.horizontal, 8)
        .frame(height: 44)
        .padding(.leading, layoutDirection == .leftToRight ? 20 : 96)
        .padding(.trailing, layoutDirection == .leftToRight ? 96 : 20)
        .padding(.top, 16)
        .padding(.bottom, 8)
    }
}

struct NavigationUtilityButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .frame(width: 44, height: 44)
            .contentShape(.rect)
            .opacity(configuration.isPressed ? 0.65 : 1)
    }
}

private struct PortraitNavigationRail<Actions: View>: ViewModifier {
    let isEnabled: Bool
    let store: LuminaStore
    @ViewBuilder let actions: Actions
    @Environment(\.layoutDirection) private var layoutDirection

    func body(content: Content) -> some View {
        if isEnabled {
            content
                .toolbarVisibility(.hidden, for: .navigationBar)
                .overlay {
                    GeometryReader { geometry in
                        VStack(spacing: 8) {
                            actions
                            NativeNowPlayingControls(store: store)
                        }
                        .labelStyle(.iconOnly)
                        .buttonStyle(NavigationUtilityButtonStyle())
                        .padding(4)
                        .background(.regularMaterial, in: .capsule)
                        .environment(\.layoutDirection, layoutDirection)
                        .frame(width: 52)
                        .padding(.top, topInset(in: geometry))
                        .padding(.trailing, 20)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                        .environment(\.layoutDirection, .leftToRight)
                    }
                }
        } else {
            content
        }
    }

    private func topInset(in geometry: GeometryProxy) -> CGFloat {
        // Keep all browse roots below the physical status corner, including
        // Library roots whose category header has already consumed safe area.
        max(0, 88 - geometry.frame(in: .global).minY)
    }
}

extension View {
    func portraitNavigationRail<Actions: View>(isEnabled: Bool, store: LuminaStore,
                                               @ViewBuilder actions: () -> Actions) -> some View {
        modifier(PortraitNavigationRail(isEnabled: isEnabled, store: store, actions: actions))
    }
}

@MainActor
struct HomeHeroChrome: View {
    @Environment(\.returnToLuminaHome) private var returnHome

    var body: some View {
        Button(action: returnHome) {
            LuminaBrandMark()
                .frame(width: 28, height: 28 * 34 / 56)
            .foregroundStyle(LuminaTheme.ink)
        }
        .buttonStyle(.plain)
        .frame(minWidth: 44, minHeight: 44, alignment: .leading)
        .contentShape(.rect)
        .accessibilityLabel("Lumina")
        .accessibilityHint("Return to Home")
        .accessibilityIdentifier("homeMasthead")
    }

}

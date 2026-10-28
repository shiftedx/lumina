import Foundation

/// Trust boundary between native playback and a Cast receiver acknowledgement.
///
/// Native playback must remain active until the receiver reports this exact ticketed item at the
/// same approximate position and with the requested play/pause intent. This is intentionally a
/// policy, rather than a convenience comparison, so a stale or foreign receiver status can never
/// stop the local player.
public enum ChromecastHandoff {
    public static func acknowledges(expectedMediaURL: URL, receiverMediaURL: URL?,
                                    localPosition: Double, receiverPosition: Double,
                                    localPlaying: Bool, receiverPlaying: Bool) -> Bool {
        guard receiverMediaURL == expectedMediaURL,
              localPosition.isFinite, receiverPosition.isFinite,
              localPosition >= 0, receiverPosition >= 0,
              abs(localPosition - receiverPosition) <= 1.5,
              localPlaying == receiverPlaying else {
            return false
        }
        return true
    }
}

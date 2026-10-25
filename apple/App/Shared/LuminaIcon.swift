// Generated from source/icons.json. Edit the geometry there and run source/build.py.
import SwiftUI

enum LuminaIconVariant {
    case outline, solid
}

enum LuminaGlyph: String, CaseIterable {
    case home = "LuminaHome"
    case library = "LuminaLibrary"
    case requests = "LuminaRequests"
    case userSettings = "LuminaUserSettings"
    case search = "LuminaSearch"
    case discover = "LuminaDiscover"
    case back = "LuminaBack"
    case forward = "LuminaForward"
    case chevronUp = "LuminaChevronUp"
    case chevronDown = "LuminaChevronDown"
    case menu = "LuminaMenu"
    case close = "LuminaClose"
    case play = "LuminaPlay"
    case pause = "LuminaPause"
    case stop = "LuminaStop"
    case previous = "LuminaPrevious"
    case next = "LuminaNext"
    case rewind = "LuminaRewind"
    case fastForward = "LuminaFastForward"
    case seekBack10 = "LuminaSeekBack10"
    case seekForward10 = "LuminaSeekForward10"
    case shuffle = "LuminaShuffle"
    case `repeat` = "LuminaRepeat"
    case repeatOne = "LuminaRepeatOne"
    case volumeHigh = "LuminaVolumeHigh"
    case volumeLow = "LuminaVolumeLow"
    case volumeOff = "LuminaVolumeOff"
    case subtitles = "LuminaSubtitles"
    case audioTracks = "LuminaAudioTracks"
    case playbackSpeed = "LuminaPlaybackSpeed"
    case queue = "LuminaQueue"
    case sleepTimer = "LuminaSleepTimer"
    case fullscreen = "LuminaFullscreen"
    case fullscreenExit = "LuminaFullscreenExit"
    case pictureInPicture = "LuminaPictureInPicture"
    case cast = "LuminaCast"
    case airplay = "LuminaAirplay"
    case television = "LuminaTelevision"
    case phone = "LuminaPhone"
    case headphones = "LuminaHeadphones"
    case screenFit = "LuminaScreenFit"
    case screenFill = "LuminaScreenFill"
    case movies = "LuminaMovies"
    case series = "LuminaSeries"
    case music = "LuminaMusic"
    case album = "LuminaAlbum"
    case artist = "LuminaArtist"
    case playlist = "LuminaPlaylist"
    case photos = "LuminaPhotos"
    case livePhoto = "LuminaLivePhoto"
    case video = "LuminaVideo"
    case podcasts = "LuminaPodcasts"
    case favorite = "LuminaFavorite"
    case bookmark = "LuminaBookmark"
    case ratingStar = "LuminaRatingStar"
    case add = "LuminaAdd"
    case remove = "LuminaRemove"
    case check = "LuminaCheck"
    case moreHorizontal = "LuminaMoreHorizontal"
    case moreVertical = "LuminaMoreVertical"
    case edit = "LuminaEdit"
    case share = "LuminaShare"
    case link = "LuminaLink"
    case folder = "LuminaFolder"
    case folderAdd = "LuminaFolderAdd"
    case filter = "LuminaFilter"
    case sort = "LuminaSort"
    case history = "LuminaHistory"
    case download = "LuminaDownload"
    case downloadDone = "LuminaDownloadDone"
    case upload = "LuminaUpload"
    case sync = "LuminaSync"
    case cloud = "LuminaCloud"
    case cloudOff = "LuminaCloudOff"
    case server = "LuminaServer"
    case wifi = "LuminaWifi"
    case wifiOff = "LuminaWifiOff"
    case trash = "LuminaTrash"
    case restore = "LuminaRestore"
    case backup = "LuminaBackup"
    case profile = "LuminaProfile"
    case users = "LuminaUsers"
    case settings = "LuminaSettings"
    case notifications = "LuminaNotifications"
    case notificationsOff = "LuminaNotificationsOff"
    case lock = "LuminaLock"
    case unlock = "LuminaUnlock"
    case info = "LuminaInfo"
    case help = "LuminaHelp"
    case warning = "LuminaWarning"
    case calendar = "LuminaCalendar"
    case logout = "LuminaLogout"
    case scanQr = "LuminaScanQr"
    case grid = "LuminaGrid"
    case list = "LuminaList"
    case externalLink = "LuminaExternalLink"
    case camera = "LuminaCamera"
    case crop = "LuminaCrop"
    case rotateLeft = "LuminaRotateLeft"
    case rotateRight = "LuminaRotateRight"
    case zoomIn = "LuminaZoomIn"
    case zoomOut = "LuminaZoomOut"
    case slideshow = "LuminaSlideshow"
    case photoStack = "LuminaPhotoStack"

    var solidAssetName: String? {
        switch self {
        case .home: return "LuminaHomeSelected"
        case .library: return "LuminaLibrarySelected"
        case .requests: return "LuminaRequestsSelected"
        case .userSettings: return "LuminaUserSettingsSelected"
        case .play: return "LuminaPlaySolid"
        case .pause: return "LuminaPauseSolid"
        case .stop: return "LuminaStopSolid"
        case .previous: return "LuminaPreviousSolid"
        case .next: return "LuminaNextSolid"
        case .rewind: return "LuminaRewindSolid"
        case .fastForward: return "LuminaFastForwardSolid"
        case .volumeHigh: return "LuminaVolumeHighSolid"
        case .volumeLow: return "LuminaVolumeLowSolid"
        case .volumeOff: return "LuminaVolumeOffSolid"
        case .subtitles: return "LuminaSubtitlesSolid"
        case .sleepTimer: return "LuminaSleepTimerSolid"
        case .pictureInPicture: return "LuminaPictureInPictureSolid"
        case .television: return "LuminaTelevisionSolid"
        case .phone: return "LuminaPhoneSolid"
        case .headphones: return "LuminaHeadphonesSolid"
        case .movies: return "LuminaMoviesSolid"
        case .series: return "LuminaSeriesSolid"
        case .music: return "LuminaMusicSolid"
        case .album: return "LuminaAlbumSolid"
        case .artist: return "LuminaArtistSolid"
        case .playlist: return "LuminaPlaylistSolid"
        case .photos: return "LuminaPhotosSolid"
        case .livePhoto: return "LuminaLivePhotoSolid"
        case .video: return "LuminaVideoSolid"
        case .podcasts: return "LuminaPodcastsSolid"
        case .favorite: return "LuminaFavoriteSolid"
        case .bookmark: return "LuminaBookmarkSolid"
        case .ratingStar: return "LuminaRatingStarSolid"
        case .folder: return "LuminaFolderSolid"
        case .folderAdd: return "LuminaFolderAddSolid"
        case .profile: return "LuminaProfileSolid"
        case .notifications: return "LuminaNotificationsSolid"
        case .notificationsOff: return "LuminaNotificationsOffSolid"
        case .camera: return "LuminaCameraSolid"
        case .photoStack: return "LuminaPhotoStackSolid"
        default: return nil
        }
    }

    func assetName(for variant: LuminaIconVariant) -> String {
        switch variant {
        case .outline: return rawValue
        case .solid: return solidAssetName ?? rawValue
        }
    }
}

/// Decorative artwork: supply the action or destination label on its containing control.
struct LuminaIcon: View {
    let glyph: LuminaGlyph
    var variant: LuminaIconVariant = .outline
    var size: CGFloat = 24

    var body: some View {
        Image(glyph.assetName(for: variant))
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

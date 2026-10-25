import { forwardRef } from 'react';
import type { SVGProps } from 'react';
import type { LucideIcon as LucideIconType, LucideProps } from 'lucide-react';

import spriteURL from './ui/lumina-media-sprite.svg?url';
import type { LuminaIconName, LuminaIconVariant } from './ui/LuminaIcon';
import './luminaIcons.css';

export type LucideIcon = LucideIconType;
export type { LuminaIconName } from './ui/LuminaIcon';

export type LuminaSpriteIconProps = Omit<SVGProps<SVGSVGElement>,
  'children' | 'name' | 'width' | 'height' | 'aria-label' | 'aria-hidden' | 'role'
> & {
  name: LuminaIconName;
  variant?: LuminaIconVariant;
  size?: number;
  /** Omit for decorative artwork inside an already labelled control. */
  label?: string;
};

const solidIcons = new Set<LuminaIconName>([
  'home', 'library', 'requests', 'user-settings', 'play', 'pause', 'stop', 'previous', 'next',
  'rewind', 'fast-forward', 'volume-high', 'volume-low', 'volume-off', 'subtitles', 'sleep-timer',
  'picture-in-picture', 'television', 'phone', 'headphones', 'movies', 'series', 'music', 'album',
  'artist', 'playlist', 'photos', 'live-photo', 'video', 'podcasts', 'favorite', 'bookmark',
  'rating-star', 'folder', 'folder-add', 'profile', 'notifications', 'notifications-off', 'camera',
  'photo-stack',
]);

export const LuminaSpriteIcon = forwardRef<SVGSVGElement, LuminaSpriteIconProps>(function LuminaSpriteIcon({
  className, label, name, size = 24, variant = 'outline', ...props
}, ref) {
  const suffix = variant === 'solid' && solidIcons.has(name) ? '-solid' : '';
  return (
    <svg {...props} aria-hidden={label ? undefined : true} aria-label={label}
      className={`lumina-icon-sprite${className ? ` ${className}` : ''}`} fill="none" focusable="false"
      height={size} ref={ref} role={label ? 'img' : undefined} viewBox="0 0 24 24" width={size}>
      <use href={`${spriteURL}#lumina-${name}${suffix}`} />
    </svg>
  );
});
LuminaSpriteIcon.displayName = 'LuminaSpriteIcon';

function luminaIcon(name: LuminaIconName, legacyName: string): LucideIcon {
  const legacyClass = legacyName.replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase();
  const Component = forwardRef<SVGSVGElement, LucideProps>(function LuminaLucideAdapter({
    absoluteStrokeWidth: _absoluteStrokeWidth, 'aria-hidden': _ariaHidden, 'aria-label': ariaLabel,
    className, fill, role: _role, size = 24, strokeWidth: _strokeWidth, style, ...props
  }, ref) {
    const numericSize = typeof size === 'number' ? size : 24;
    const sizedStyle = typeof size === 'string' ? { width: size, height: size, ...style } : style;
    const solid = fill !== undefined && fill !== 'none' && fill !== 'transparent';
    return (
      <LuminaSpriteIcon {...props}
        className={`lucide lucide-${legacyClass}${className ? ` ${className}` : ''}`}
        label={typeof ariaLabel === 'string' ? ariaLabel : undefined} name={name} ref={ref}
        size={numericSize} style={sizedStyle} variant={solid ? 'solid' : 'outline'} />
    );
  });
  Component.displayName = legacyName;
  return Component as LucideIcon;
}

export const ArchiveRestore = luminaIcon('restore', 'ArchiveRestore');
export const ArrowDown = luminaIcon('chevron-down', 'ArrowDown');
export const ArrowDownToLine = luminaIcon('download', 'ArrowDownToLine');
export const ArrowLeft = luminaIcon('back', 'ArrowLeft');
export const ArrowRight = luminaIcon('forward', 'ArrowRight');
export const ArrowUp = luminaIcon('chevron-up', 'ArrowUp');
export const AudioLines = luminaIcon('audio-tracks', 'AudioLines');
export const Bookmark = luminaIcon('bookmark', 'Bookmark');
export const CalendarClock = luminaIcon('calendar', 'CalendarClock');
export const CalendarDays = luminaIcon('calendar', 'CalendarDays');
export const Camera = luminaIcon('camera', 'Camera');
export const Captions = luminaIcon('subtitles', 'Captions');
export const Check = luminaIcon('check', 'Check');
export const ChevronDown = luminaIcon('chevron-down', 'ChevronDown');
export const ChevronLeft = luminaIcon('back', 'ChevronLeft');
export const ChevronRight = luminaIcon('forward', 'ChevronRight');
export const ChevronUp = luminaIcon('chevron-up', 'ChevronUp');
export const CircleAlert = luminaIcon('warning', 'CircleAlert');
export const CircleStop = luminaIcon('stop', 'CircleStop');
export const CircleUserRound = luminaIcon('profile', 'CircleUserRound');
export const Clapperboard = luminaIcon('movies', 'Clapperboard');
export const Compass = luminaIcon('discover', 'Compass');
export const DatabaseBackup = luminaIcon('backup', 'DatabaseBackup');
export const Disc3 = luminaIcon('album', 'Disc3');
export const Download = luminaIcon('download', 'Download');
export const Ellipsis = luminaIcon('more-horizontal', 'Ellipsis');
export const ExternalLink = luminaIcon('external-link', 'ExternalLink');
export const FileCheck2 = luminaIcon('check', 'FileCheck2');
export const Film = luminaIcon('movies', 'Film');
export const FolderHeart = luminaIcon('folder', 'FolderHeart');
export const FolderOpen = luminaIcon('folder', 'FolderOpen');
export const FolderPlus = luminaIcon('folder-add', 'FolderPlus');
export const HardDriveDownload = luminaIcon('download', 'HardDriveDownload');
export const Headphones = luminaIcon('headphones', 'Headphones');
export const Heart = luminaIcon('favorite', 'Heart');
export const History = luminaIcon('history', 'History');
export const Home = luminaIcon('home', 'Home');
export const Image = luminaIcon('photos', 'Image');
export const ImagePlus = luminaIcon('upload', 'ImagePlus');
export const Images = luminaIcon('photo-stack', 'Images');
export const Info = luminaIcon('info', 'Info');
export const Library = luminaIcon('library', 'Library');
export const Link2 = luminaIcon('link', 'Link2');
export const List = luminaIcon('list', 'List');
export const ListMusic = luminaIcon('playlist', 'ListMusic');
export const ListPlus = luminaIcon('bookmark', 'ListPlus');
export const ListVideo = luminaIcon('playlist', 'ListVideo');
export const LoaderCircle = luminaIcon('sync', 'LoaderCircle');
export const Lock = luminaIcon('lock', 'Lock');
export const LockOpen = luminaIcon('unlock', 'LockOpen');
export const LogOut = luminaIcon('logout', 'LogOut');
export const Maximize = luminaIcon('fullscreen', 'Maximize');
export const Maximize2 = luminaIcon('fullscreen', 'Maximize2');
export const Menu = luminaIcon('menu', 'Menu');
export const Mic = luminaIcon('artist', 'Mic');
export const Mic2 = luminaIcon('artist', 'Mic2');
export const Minimize = luminaIcon('fullscreen-exit', 'Minimize');
export const Minimize2 = luminaIcon('fullscreen-exit', 'Minimize2');
export const Minus = luminaIcon('remove', 'Minus');
export const Monitor = luminaIcon('television', 'Monitor');
export const MonitorPlay = luminaIcon('video', 'MonitorPlay');
export const MoreHorizontal = luminaIcon('more-horizontal', 'MoreHorizontal');
export const Music = luminaIcon('music', 'Music');
export const Music2 = luminaIcon('music', 'Music2');
export const OctagonX = luminaIcon('warning', 'OctagonX');
export const Pause = luminaIcon('pause', 'Pause');
export const Pencil = luminaIcon('edit', 'Pencil');
export const PictureInPicture2 = luminaIcon('picture-in-picture', 'PictureInPicture2');
export const Play = luminaIcon('play', 'Play');
export const Plus = luminaIcon('add', 'Plus');
export const RefreshCw = luminaIcon('sync', 'RefreshCw');
export const Repeat = luminaIcon('repeat', 'Repeat');
export const Search = luminaIcon('search', 'Search');
export const Server = luminaIcon('server', 'Server');
export const Settings = luminaIcon('settings', 'Settings');
export const Settings2 = luminaIcon('settings', 'Settings2');
export const Share2 = luminaIcon('share', 'Share2');
export const Shuffle = luminaIcon('shuffle', 'Shuffle');
export const SkipBack = luminaIcon('previous', 'SkipBack');
export const SkipForward = luminaIcon('next', 'SkipForward');
export const SlidersHorizontal = luminaIcon('filter', 'SlidersHorizontal');
export const Speaker = luminaIcon('volume-high', 'Speaker');
export const Trash2 = luminaIcon('trash', 'Trash2');
export const Tv = luminaIcon('television', 'Tv');
export const Undo2 = luminaIcon('restore', 'Undo2');
export const Upload = luminaIcon('upload', 'Upload');
export const UserRound = luminaIcon('profile', 'UserRound');
export const Users = luminaIcon('users', 'Users');
export const Video = luminaIcon('video', 'Video');
export const Volume2 = luminaIcon('volume-high', 'Volume2');
export const VolumeX = luminaIcon('volume-off', 'VolumeX');
export const WifiOff = luminaIcon('wifi-off', 'WifiOff');
export const X = luminaIcon('close', 'X');

// Deliberate exceptions: the approved set has no semantically equivalent artwork.
// Keep these centralized so future icon-pack additions have one migration point.
export {
  BookOpen,
  Car,
  ClipboardCopy,
  Clock,
  Copy,
  Dumbbell,
  Eye,
  EyeOff,
  FileText,
  Gamepad2,
  Gauge,
  Globe,
  GripVertical,
  HardDrive,
  Layers,
  Leaf,
  LocateFixed,
  Mail,
  MessagesSquare,
  Moon,
  Newspaper,
  Palette,
  Plug,
  PlugZap,
  Radio,
  RotateCcw,
  Rss,
  Save,
  Scissors,
  ShieldCheck,
  Sparkles,
  Sprout,
  Square,
  Stethoscope,
  Sun,
  Telescope,
  Trophy,
  UserPen,
  Utensils,
  Wrench,
} from 'lucide-react';

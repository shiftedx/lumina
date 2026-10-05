import { type ReactNode, useState } from 'react';

import { AdminImports } from '../../admin/AdminImports';
import { AnimeFolders } from '../../admin/AnimeFolders';
import { LibraryAutomation } from '../../admin/LibraryAutomation';
import { ArtworkProgressRow } from '../../admin/ArtworkProgressRow';
import { AdminStorage, LibraryRefreshContext } from '../../admin/AdminStorage';
import { RecordingRetentionForm } from '../../admin/RecordingRetention';
import type { SettingsSectionDef } from '../settingsTypes';
import { EditingSwitch } from './EditingSwitch';

/** Shares one refresh counter between the storage and imports rows, so Import now's run shows under Imports. */
function LibraryRefresh({ children }: { children: ReactNode }) {
  const [version, setVersion] = useState(0);
  return <LibraryRefreshContext.Provider value={{ version, bump: () => setVersion((current) => current + 1) }}>{children}</LibraryRefreshContext.Provider>;
}

/** Library & storage: roots, imports and progress, mass-missing confirmation, retention. */
export const LIBRARY_SECTION: SettingsSectionDef = {
  id: 'library', group: 'server', label: 'Library & storage', aliases: ['libraries'], summary: 'Where media lives, where new downloads go, and bringing in media already on a drive.',
  Provider: LibraryRefresh,
  entries: [
    { id: 'library.storage', label: 'Storage roots and download rules', layout: 'block', keywords: ['root', 'drive', 'folder', 'mount', 'managed', 'external', 'import now', 'container path', 'keep free', 'reserve', 'rules', 'default destination', 'where downloads go', 'test a download', 'check now', 'disable', 'remove', 'offline', 'low space'], info: 'The folders Lumina can use and which one each new download goes to. Managed roots receive downloads, external roots are read-only libraries you import from, and existing files are never moved. Each root shows whether it is available, offline, short of space or a different disk; this affects everyone on this server.', Control: AdminStorage },
    { id: 'library.retention', label: 'Live recording cleanup', layout: 'block', advanced: true, keywords: ['recordings', 'retention', 'keep recordings for', 'days', 'total size', 'trash'], info: "Moves finished live recordings to the recoverable trash, oldest first, once they pass an age or total-size limit. Recordings a member marked Keep are never removed, and 0 turns a limit off. Affects everyone's recordings.", Control: RecordingRetentionForm },
    { id: 'library.automation', label: 'Scans and folder watching', layout: 'block', advanced: true,
      keywords: ['scan', 'rescan', 'schedule', 'nightly', 'hourly', 'every 15 minutes', 'watch', 'watch folder', 'new media', 'automatic', 'real-time', 'monitor', 'scan now'],
      info: "Keeps imported folders up to date by themselves: a scheduled scan rereads the whole folder, and watching notices new, moved and deleted files within minutes. Neither changes your files, and if many look missing Lumina still asks first. Affects everyone who can see the imported media.",
      Control: LibraryAutomation },
    { id: 'library.imports', label: 'Imports', layout: 'block', keywords: ['import', 'import a folder', 'scan', 'external folder', 'visibility', 'shared', 'private', 'household', 'rescan', 'resume', 'missing files', 'confirm'], info: 'Adds media already on an external folder to the Library without moving or changing the files. You choose who can see the imported items, and if many files suddenly look missing, Lumina asks before marking them. Affects everyone who can see the imported media.', Control: AdminImports },
    { id: 'library.anime', label: 'Anime folders', layout: 'block', advanced: true, keywords: ['anime', 'category', 'folder', 'tab', 'sort', 'jellyfin', 'infuse'], info: "Titles with a file inside a folder with one of these names appear under Anime instead of Movies or Shows, in Lumina and in Jellyfin apps. Every folder in a file's path counts, including a storage root's own folders, in any upper or lower case, so avoid a name every file passes through, such as a root's parent folder. Saving re-sorts the library in the background for everyone on this server.", Control: AnimeFolders },
    { id: 'library.editing', label: 'Let household members edit details', layout: 'switch', keywords: ['editing details', 'edit', 'metadata', 'members', 'permission', 'posters', 'artwork', 'lock', 'genres'], info: 'Vault owners can always edit titles, posters and locks. Turn this on to let household members edit them too. Matching titles to TMDB stays with vault owners, and edits are shared by everyone on this server and appear in Jellyfin apps.', Control: EditingSwitch },
    { id: 'library.artwork', label: 'Artwork preparation', advanced: true, keywords: ['artwork', 'posters', 'backdrops', 'stills', 'renditions', 'thumbnails', 'cache', 'prepared'], info: 'Lumina prepares small, fast copies of posters, backdrops and episode stills in the background, pausing while anyone watches something that needs converting. Failed images keep using the original file.', Control: ArtworkProgressRow },
  ],
};

import { forgetTitle } from '../../gallery/titleCache';
import { FieldRow } from './FieldRow';
import { ItemLock } from './ItemLock';
import { DETAIL_ORDER, type TabProps } from './editorModel';

type MusicGroup = { label: string; helper: string; fields: string[] };

const MUSIC_GROUPS: Record<'album' | 'artist', MusicGroup[]> = {
  album: [
    { label: 'Release identity', helper: 'Keep the title, dates, and release type together so the album stays easy to find.', fields: ['name', 'sort_name', 'year', 'original_year', 'premiered', 'release_type'] },
    { label: 'Release details', helper: 'Edition, label, catalogue number, and country describe this particular release.', fields: ['edition', 'label', 'catalogue_number', 'country', 'studios'] },
    { label: 'Listening context', helper: 'Use these fields for the way you browse and remember the music.', fields: ['genres', 'styles', 'mood', 'tags', 'overview'] },
    { label: 'Library', helper: 'System information retained for this title.', fields: ['added_at'] },
  ],
  artist: [
    { label: 'Artist identity', helper: 'Use the name and origin shown in your library.', fields: ['name', 'sort_name', 'country', 'origin'] },
    { label: 'Active period', helper: 'Partial dates are okay: enter a year, month, or full date.', fields: ['active_since', 'active_until'] },
    { label: 'Listening context', helper: 'These tags travel with the artist profile in your music library.', fields: ['genres', 'styles', 'mood', 'tags', 'overview'] },
    { label: 'Library', helper: 'System information retained for this title.', fields: ['added_at'] },
  ],
};

export default function DetailsTab({ doc, draft, user, setField, togglePin, setItemLock, discardField, reload }: TabProps) {
  const entry = draft[doc.title_id];
  const itemLocked = entry?.locked ?? doc.locked;
  const musicType = doc.type === 'album' ? 'album' : doc.type === 'artist' ? 'artist' : null;
  const renderField = (key: string) => (
    <FieldRow
      discardField={discardField}
      draft={entry}
      fieldKey={key}
      itemLocked={itemLocked}
      key={key}
      onReverted={() => { forgetTitle(doc.title_id); reload(); }}
      seasons={doc.seasons}
      setField={setField}
      state={doc.fields[key]}
      titleId={doc.title_id}
      togglePin={togglePin}
    />
  );
  const musicGroups = musicType ? MUSIC_GROUPS[musicType]
    .concat([{ label: 'Credits', helper: 'The people behind the music.', fields: ['credits'] }])
    .map((group) => ({ ...group, fields: group.fields.filter((key) => key in doc.fields) }))
    .filter((group) => group.fields.length > 0) : [];
  return (
    <div className="ed-details">
      <ItemLock doc={doc} draft={draft} setItemLock={setItemLock} user={user} />
      {musicType
        ? musicGroups.map((group) => (
          <section aria-labelledby={`ed-music-${group.label.toLowerCase().replace(/ /g, '-')}`} className="ed-music-group g-panel" key={group.label}>
            <h2 className="g-panel-title" id={`ed-music-${group.label.toLowerCase().replace(/ /g, '-')}`}>{group.label}</h2>
            <p className="g-setting-note">{group.helper}</p>
            {group.fields.map(renderField)}
          </section>
        ))
        : DETAIL_ORDER.filter((key) => key in doc.fields).map(renderField)}
    </div>
  );
}

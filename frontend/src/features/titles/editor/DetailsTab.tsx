import { forgetTitle } from '../../gallery/titleCache';
import { FieldRow } from './FieldRow';
import { ItemLock } from './ItemLock';
import { DETAIL_ORDER, type TabProps } from './editorModel';

export default function DetailsTab({ doc, draft, user, setField, togglePin, setItemLock, discardField, reload }: TabProps) {
  const entry = draft[doc.title_id];
  const itemLocked = entry?.locked ?? doc.locked;
  return (
    <div className="ed-details">
      <ItemLock doc={doc} draft={draft} setItemLock={setItemLock} user={user} />
      {DETAIL_ORDER.filter((key) => key in doc.fields).map((key) => (
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
      ))}
    </div>
  );
}

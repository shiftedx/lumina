import { useEffect, useRef, useState } from 'react';

import { getPerson, removePersonPhoto, renamePerson, uploadPersonPhoto } from '../../../api';
import type { PersonDoc, PersonEditResult } from '../../../types';
import { Avatar, Button, Dialog, Field, fieldProps, Input, useToast } from '../../../ui';
import { undoBatch } from './editorActions';

export interface PersonDialogProps {
  personId: string | null;
  onClose: () => void;
  /** A change landed (or was undone): the caller refetches what shows the person. */
  onChanged: () => void;
}

/** The household people editor (#164): one person's name and photo on every title that credits them. */
export function PersonDialog({ personId, onClose, onChanged }: PersonDialogProps) {
  const toast = useToast();
  const [person, setPerson] = useState<PersonDoc | null>(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setPerson(null);
    if (!personId) return;
    let live = true;
    getPerson(personId).then((doc) => { if (live) { setPerson(doc); setName(doc.name); } }, () => { if (live) toast({ tone: 'error', message: "Lumina couldn't load that person." }); });
    return () => { live = false; };
  }, [personId, toast]);

  async function run(change: () => Promise<PersonEditResult>, done: string) {
    setBusy(true);
    try {
      const result = await change();
      setPerson(result.person);
      setName(result.person.name);
      onChanged();
      const batch = result.batch_id;
      if (batch) {
        toast({ tone: 'success', message: done, action: { label: 'Undo', onAction: () => void undoBatch(batch, toast, () => {
          onChanged();
          if (personId) getPerson(personId).then((doc) => { setPerson(doc); setName(doc.name); }, () => undefined);
        }) } });
      }
    } catch {
      toast({ tone: 'error', message: "Lumina couldn't save that change." });
    } finally {
      setBusy(false);
    }
  }

  const trimmed = name.trim();
  const id = personId ?? '';
  return (
    <Dialog
      busy={busy}
      description={person ? `Changes show on all ${person.title_count === 1 ? '1 title' : `${person.title_count} titles`} that credit them, here and in connected apps.` : undefined}
      footer={<Button onClick={onClose} variant="quiet">Done</Button>}
      onClose={onClose}
      open={personId !== null}
      title="Edit person"
    >
      {person ? (
        <div className="ed-person">
          <Avatar imageUrl={person.image_url ?? undefined} name={person.name} size={96} />
          <div className="ed-person-photo">
            <input
              accept="image/jpeg,image/png,image/webp"
              aria-hidden="true"
              hidden
              onChange={(event) => { const picked = event.target.files?.[0]; event.target.value = ''; if (picked) void run(() => uploadPersonPhoto(id, picked), 'Photo updated.'); }}
              ref={file}
              tabIndex={-1}
              type="file"
            />
            <Button disabled={busy} onClick={() => file.current?.click()} variant="secondary">Upload photo</Button>
            {person.photo_edited ? <Button disabled={busy} onClick={() => void run(() => removePersonPhoto(id), 'Photo removed.')} variant="quiet">Use the original photo</Button> : null}
          </div>
          <form className="ed-person-name" onSubmit={(event) => { event.preventDefault(); if (trimmed && trimmed !== person.name) void run(() => renamePerson(id, trimmed), 'Name saved.'); }}>
            <Field hint={person.name_edited ? `Originally ${person.source_name}` : undefined} label="Name">
              {(ids) => <Input {...fieldProps(ids)} maxLength={200} onChange={(event) => setName(event.target.value)} value={name} />}
            </Field>
            <Button disabled={busy || !trimmed || trimmed === person.name} type="submit">Save name</Button>
            {person.name_edited ? <Button disabled={busy} onClick={() => void run(() => renamePerson(id, null), 'Name reset.')} variant="quiet">Use {person.source_name}</Button> : null}
          </form>
        </div>
      ) : null}
    </Dialog>
  );
}

import { Button } from '../../../ui';

export function saveLabel(count: number): string {
  return count ? `Save ${count} change${count === 1 ? '' : 's'}` : 'Save changes';
}

export function EditorFooter({ count, saving, onSave, onDiscard }: { count: number; saving: boolean; onSave: () => void; onDiscard: () => void }) {
  return (
    <footer className="ed-footer">
      <Button busy={saving} disabled={count === 0} onClick={onSave} variant="primary">{saveLabel(count)}</Button>
      <Button disabled={count === 0 || saving} onClick={onDiscard} variant="quiet">Discard</Button>
    </footer>
  );
}

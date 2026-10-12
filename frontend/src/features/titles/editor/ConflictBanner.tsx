import { Button } from '../../../ui';
import { FIELD_META } from './editorModel';

const label = (field: string) => FIELD_META[field]?.label ?? field;
export function joinLabels(fields: string[]): string {
  const names = fields.map(label);
  if (names.length < 3) return names.join(' and ');
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

export function ConflictBanner({ fields, onTheirs, onMine }: { fields: string[]; onTheirs: () => void; onMine: () => void }) {
  return (
    <div className="ed-banner" role="alert">
      <p>Someone changed {joinLabels(fields)} while you were editing.</p>
      <div className="ed-banner-actions">
        <Button onClick={onTheirs}>Load their version</Button>
        <Button onClick={onMine} variant="primary">Keep mine and save</Button>
      </div>
    </div>
  );
}

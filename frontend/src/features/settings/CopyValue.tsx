import { Copy } from 'lucide-react';
import { useState } from 'react';

import { Button, Field, fieldProps, Input } from '../../ui';

/** A read-only value in big monospace with a Copy button; selecting it works when the clipboard is blocked. */
export function CopyValue({ label, value, copyLabel }: { label: string; value: string; copyLabel?: string }) {
  const [status, setStatus] = useState('');
  async function copy() {
    try { await navigator.clipboard.writeText(value); setStatus('Copied.'); } catch { setStatus('Copy is blocked here. Select the text and copy it.'); }
  }
  return (
    <div className="admin-issued-link">
      <Field label={label}>{(ids) => <Input {...fieldProps(ids)} className="g-mono" onFocus={(event) => event.currentTarget.select()} readOnly spellCheck={false} value={value} />}</Field>
      <Button aria-label={copyLabel ?? `Copy ${label.toLowerCase()}`} icon={<Copy />} onClick={() => { void copy(); }} variant="secondary">Copy</Button>
      <span aria-live="polite" className="g-setting-note" role="status">{status}</span>
    </div>
  );
}

import { Check, CircleAlert, Minus, OctagonX } from 'lucide-react';
import { type ReactNode } from 'react';

export type StatusTone = 'ok' | 'attention' | 'danger' | 'muted';
const ICONS = { ok: Check, attention: CircleAlert, danger: OctagonX, muted: Minus } as const;

/** An icon plus words; replaces every coloured status pill. */
export function StatusText({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  const Icon = ICONS[tone];
  return <span className={`g-status is-${tone}`}><Icon aria-hidden="true" /><span>{children}</span></span>;
}

import { type ReactNode } from 'react';

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="g-kbd">{children}</kbd>;
}

export function VisuallyHidden({ children, as: As = 'span' }: { children: ReactNode; as?: 'span' | 'div' }) {
  return <As className="sr-only">{children}</As>;
}

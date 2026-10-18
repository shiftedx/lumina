import { useEffect, useState } from 'react';

import { type ArrImportState, getTitleIntegrations } from './integrationsApi';
import { StatusText } from '../../ui';
import './integrations.css';

const labels: Record<ArrImportState, string> = {
  registered: 'Lumina has registered this title from an integration.',
  pending_import: 'An integration found this title; Lumina is waiting to import it.',
  awaiting_first_import: 'An integration found this title; Lumina is waiting for its first import.',
  root_unavailable: 'Lumina cannot currently reach the mapped library root for this title.',
  unmapped: 'An integration found this title, but Lumina has not matched it to this library title.',
};

/** Member-safe availability explanation. A hidden or unavailable response intentionally leaves no trace on the title page. */
export function TitleIntegrationStatus({ titleId }: { titleId: string }) {
  const [integrations, setIntegrations] = useState<Array<{ state: ArrImportState; stale: boolean }> | null>(null);
  useEffect(() => {
    let active = true;
    setIntegrations(null);
    getTitleIntegrations(titleId).then((next) => { if (active) setIntegrations(next.integrations); }).catch(() => { if (active) setIntegrations([]); });
    return () => { active = false; };
  }, [titleId]);
  if (!integrations?.length) return null;
  const primary = integrations.find((entry) => entry.state !== 'registered') ?? integrations[0];
  return <section aria-label="Integration availability" className="t-integration-status"><StatusText tone={primary.stale ? 'attention' : primary.state === 'registered' ? 'ok' : 'muted'}>{labels[primary.state]}{primary.stale ? ' This integration status may be out of date.' : ''}</StatusText></section>;
}

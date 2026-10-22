import { useState } from 'react';

import { HouseholdTransferStep } from './HouseholdTransferStep';
import { VaultSetup } from './VaultSetup';

/** The durable first-owner marker remains pending until media and the optional transfer step are both left. */
export function FirstOwnerSetup({ onContinue, error = null }: { onContinue: () => Promise<void>; error?: string | null }) {
  const [phase, setPhase] = useState<'media' | 'transfer'>('media');
  return (
    <>
      <div hidden={phase !== 'media'} inert={phase !== 'media' || undefined}>
        <VaultSetup active={phase === 'media'} error={phase === 'media' ? error : null} onContinue={async () => { setPhase('transfer'); window.scrollTo({ top: 0, behavior: 'instant' }); }} />
      </div>
      {phase === 'transfer' ? <HouseholdTransferStep error={error} onBack={() => { setPhase('media'); window.scrollTo({ top: 0, behavior: 'instant' }); }} onFinish={onContinue} /> : null}
    </>
  );
}

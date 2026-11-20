import { useEffect, useState } from 'react';
import { getMusicHomeCapability } from '../../api';

export function useMusicRework(owner?: string | null): boolean {
  const [enabled, setEnabled] = useState(false);
  useEffect(() => {
    let current = true;
    setEnabled(false);
    void getMusicHomeCapability().then((result) => { if (current) setEnabled(result.enabled === true); }, () => { if (current) setEnabled(false); });
    return () => { current = false; };
  }, [owner]);
  return enabled;
}

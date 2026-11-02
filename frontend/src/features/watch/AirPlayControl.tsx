import { useEffect, useRef, useState } from 'react';
import { LuminaSpriteIcon } from '../../luminaIcons';
import { Button } from '../../ui';

type AirPlayMedia = HTMLMediaElement & {
  webkitShowPlaybackTargetPicker?: () => void;
  webkitCurrentPlaybackTargetIsWireless?: boolean;
};

export function AirPlayControl({ getMedia }: { getMedia: () => HTMLMediaElement | null }) {
  const [available, setAvailable] = useState(false);
  const [wireless, setWireless] = useState(false);
  const mediaRef = useRef<AirPlayMedia | null>(null);
  useEffect(() => {
    const media: AirPlayMedia | null = getMedia();
    if (!media?.webkitShowPlaybackTargetPicker) return;
    mediaRef.current = media;
    const availability = (event: Event) => setAvailable((event as Event & { availability: string }).availability === 'available');
    const route = () => setWireless(Boolean(media.webkitCurrentPlaybackTargetIsWireless));
    route();
    media.addEventListener('webkitplaybacktargetavailabilitychanged', availability);
    media.addEventListener('webkitcurrentplaybacktargetiswirelesschanged', route);
    return () => {
      mediaRef.current = null;
      media.removeEventListener('webkitplaybacktargetavailabilitychanged', availability);
      media.removeEventListener('webkitcurrentplaybacktargetiswirelesschanged', route);
    };
  }, []); // Remounted by the player when its media element changes.
  if (!available && !wireless) return null;
  return <Button icon={<LuminaSpriteIcon name="airplay" />} pressed={wireless} onClick={() => mediaRef.current?.webkitShowPlaybackTargetPicker?.()} variant="secondary">AirPlay</Button>;
}

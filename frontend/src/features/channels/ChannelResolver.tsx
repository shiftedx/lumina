/** /channel?url=: resolve an @handle, /c/ or /user/ address, then replace the entry with the channel page. */
import { useEffect, useState } from 'react';

import { resolveChannel } from '../../api';
import { channelPagePath, openAppPath } from './channelMention';

export function ChannelResolver({ url }: { url: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    setFailed(false);
    resolveChannel(url).then(({ channel_id: id }) => { if (active) openAppPath(channelPagePath(id), true); }, () => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [url]);
  if (!failed) return <div aria-busy="true" aria-label="Finding the channel" className="surface gallery g-channel-resolve" role="status" />;
  return (
    <div className="surface gallery g-channel-resolve">
      <h1 className="g-channel-state" tabIndex={-1}>Lumina couldn't find that channel.</h1>
      <button className="g-button" onClick={() => window.history.back()} type="button">Back</button>
    </div>
  );
}

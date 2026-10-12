import type { ComponentProps } from 'react';

import { WatchSurface } from '../features/watch/WatchSurface';

type WatchSurfaceProps = ComponentProps<typeof WatchSurface>;

const noop = () => undefined;

/** A WatchSurface with inert defaults; pass it to `render` or `renderToStaticMarkup`. */
export function watchSurface(overrides: Partial<WatchSurfaceProps> & Pick<WatchSurfaceProps, 'selection'>) {
  return (
    <WatchSurface
      busy={false} channels={[]} downloadMenuOpen={false} downloadQuality="best" jobs={[]} library={[]}
      onBack={noop} onClearPlayback={noop} onCloseDownloadMenu={noop} onDownload={noop} onDownloadQuality={noop}
      onFollow={noop} onOpenDownloads={noop} onOpenRelated={noop} onPlaybackCheckpoint={noop}
      onRestart={noop} onToggleDownloadMenu={noop} playback={null} previewLoading={false} related={[]}
      {...overrides}
    />
  );
}

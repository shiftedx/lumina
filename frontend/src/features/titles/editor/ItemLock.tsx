import { useState } from 'react';

import { Button, Switch } from '../../../ui';
import { PreviewRefreshDialog } from './PreviewRefreshDialog';
import type { TabProps } from './editorModel';

/** Lock this item rides in the draft (one change); Preview refresh is owner-only and needs a TMDB match. */
export function ItemLock({ doc, draft, user, setItemLock }: Pick<TabProps, 'doc' | 'draft' | 'user' | 'setItemLock'>) {
  const [previewing, setPreviewing] = useState(false);
  return (
    <div className="ed-itemlock">
      <Switch
        checked={draft[doc.title_id]?.locked ?? doc.locked}
        hint="Scans and refreshes won't change anything on this title. Your edits still save."
        label="Lock this item"
        onChange={(next) => setItemLock(doc.title_id, next, doc.locked)}
      />
      {user.role === 'admin' && doc.can_identify ? <Button onClick={() => setPreviewing(true)}>Preview refresh</Button> : null}
      {previewing ? <PreviewRefreshDialog onClose={() => setPreviewing(false)} titleId={doc.title_id} /> : null}
    </div>
  );
}

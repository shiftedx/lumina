import { ApiRequestError, undoMetadataBatch } from '../../../api';
import type { ToastInput } from '../../../ui';

const plural = (count: number, one: string, many: string) => (count === 1 ? one : many);

/** Undoes one batch and says what it did and what it left alone. */
export async function undoBatch(batchId: string, toast: (toast: ToastInput) => unknown, reload: () => void): Promise<void> {
  try {
    const result = await undoMetadataBatch(batchId);
    const changed = result.skipped.filter((skip) => skip.reason === 'changed_since').length;
    const hidden = new Set(result.skipped.filter((skip) => skip.reason === 'not_visible').map((skip) => skip.title_id)).size;
    const ownerOnly = result.skipped.some((skip) => skip.reason === 'owner_only');
    const sentences = [
      changed ? plural(changed, '1 field changed since, so it was left as it is.', `${changed} fields changed since, so they were left as they are.`) : '',
      hidden ? `${hidden} ${plural(hidden, "title isn't", "titles aren't")} in your library, so ${plural(hidden, 'it was', 'they were')} left as ${plural(hidden, 'it is', 'they are')}.` : '',
      ownerOnly ? 'Only a vault owner can undo this.' : '',
    ].filter(Boolean);
    toast(sentences.length ? { tone: 'info', message: sentences.join(' ') } : { tone: 'success', message: 'Undone.' });
    reload();
  } catch (failure) {
    toast({ tone: 'error', message: failure instanceof ApiRequestError && failure.message === 'already_undone' ? 'That change was already undone.' : 'Lumina could not undo that. Try again.' });
  }
}

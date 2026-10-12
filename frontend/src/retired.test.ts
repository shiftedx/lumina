/// <reference types="node" />
/** Success criterion 11: the retired parts stay retired. */
import { execSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';

const grep = (pattern: string) => execSync(`grep -rnE "${pattern}" src --include='*.tsx' --include='*.ts' --include='*.css' || true`).toString().split('\n').filter((line) => line && !line.includes('retired.test.ts'));

describe('retired parts', () => {
  it('nothing imports TitleCard, SearchBar or GlobalSearch', () => {
    expect(grep('\\b(TitleCard|SearchBar|GlobalSearch)\\b')).toEqual([]);
  });
  it('the old card, its menu and the dead library surface are gone', () => {
    expect(grep('\\b(MediaCard|LibraryCard|RecommendationControls|LibrarySurface|BrowseSurfaces|Shelf)\\b')).toEqual([]);
    expect(grep('\\.(media-card|library-card|recommendation-controls|title-card|reco-menu-list|reco-undo)\\b')).toEqual([]);
  });
  it('the retired class families are gone', () => {
    expect(grep('className=[^>]*[^-[:alnum:]_](app-toast|button-primary|button-secondary|button-quiet|button-following|icon-button|text-button|t-button|t-text-button|t-tab|t-link|h-button|h-text-button|h-icon-button|queue-toast|watch-avatar|summary-evidence|notes-seek|setting-dialog|settings-toggle|segmented-control|theme-choice|batch-state|lifecycle-badge)\\b')).toEqual([]);
  });
  it('window.confirm guards only unsaved-changes navigation', () => {
    expect(grep('window\\.confirm').map((line) => line.split(':')[0]).filter((file) => !file.endsWith('.test.tsx'))).toEqual(['src/features/settings/unsavedChanges.ts']);
  });
});

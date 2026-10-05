import { describe, expect, it } from 'vitest';

import { LIST_ID, recoAnnotation, recoEntry, recoTitle, remoteKey } from '../../test/recoFixtures';
import { CHANNEL_ID } from '../../test/remoteFixtures';
import { followHadSuppression, orderRails, reasonIdFor, recoMenuItems, relatedDropPredicate, remoteTarget, suppressionInput, targetKey, titleTarget, undoMessage } from './recoModel';

const harbor = remoteTarget(recoEntry('v1', 0, { uploader: 'Harbor Films' }));
const bare = remoteTarget(recoEntry('v2', 1, { uploader: null, uploader_id: null, uploader_url: null }));

describe('recoMenuItems', () => {
  it('offers Add to queue and three feedback actions on a remote card, and names the channel', () => {
    expect(recoMenuItems(harbor, false)).toEqual([
      { id: 'queue', label: 'Add to queue' },
      { id: 'not_interested', label: 'Not interested' },
      { id: 'fewer', label: 'Show fewer from Harbor Films' },
      { id: 'hide_channel', label: "Don't recommend Harbor Films" },
    ]);
  });

  it('puts "Why this?" first only where the caption is hidden', () => {
    expect(recoMenuItems(harbor, true)[0]).toEqual({ id: 'why', label: 'Why this?' });
    expect(recoMenuItems(harbor, true)[1]).toEqual({ id: 'queue', label: 'Add to queue' });
    expect(recoMenuItems(harbor, false).some((item) => item.id === 'why')).toBe(false);
  });

  it('cuts a long channel name at 32 characters', () => {
    const long = remoteTarget(recoEntry('v3', 2, { uploader: 'A'.repeat(40) }));
    expect(recoMenuItems(long, false)[2].label).toBe(`Show fewer from ${'A'.repeat(31)}…`);
  });

  it('offers no channel actions when the entry names no channel and carries no channel id', () => {
    expect(recoMenuItems(bare, false).map((item) => item.id)).toEqual(['queue', 'not_interested']);
  });

  it('omits Show fewer when recommendations are not personal (the switch is off), and keeps the rest', () => {
    expect(recoMenuItems(harbor, false, false).map((item) => item.id)).toEqual(['queue', 'not_interested', 'hide_channel']);
    expect(recoMenuItems(harbor, false, true).map((item) => item.id)).toContain('fewer');
  });

  it('offers no queue without an address to queue', () => {
    const noAddress = remoteTarget(recoEntry('v5', 4, { uploader: 'Harbor Films', webpage_url: null }));
    expect(recoMenuItems(noAddress, false).map((item) => item.id)).toEqual(['not_interested', 'fewer', 'hide_channel']);
  });

  it('still offers channel actions from a channel id alone, calling it "this channel"', () => {
    const idOnly = remoteTarget(recoEntry('v4', 3, { uploader: null, uploader_id: CHANNEL_ID }));
    expect(recoMenuItems(idOnly, false).map((item) => item.label)).toEqual(['Add to queue', 'Not interested', 'Show fewer from this channel', "Don't recommend this channel"]);
  });

  it('offers a title only Not interested (and Why this? when the caption is hidden), never a queue', () => {
    const film = titleTarget(recoTitle(1));
    expect(recoMenuItems(film, false).map((item) => item.id)).toEqual(['not_interested']);
    expect(recoMenuItems(film, true).map((item) => item.id)).toEqual(['why', 'not_interested']);
  });
});

describe('undoMessage', () => {
  it('says Hidden. for a veto and names the channel for show-fewer', () => {
    expect(undoMessage('not_interested', harbor)).toBe('Hidden.');
    expect(undoMessage('hide_channel', harbor)).toBe('Hidden.');
    expect(undoMessage('fewer', harbor)).toBe('Showing fewer from Harbor Films.');
    expect(undoMessage('fewer', bare)).toBe('Showing fewer from this channel.');
    expect(undoMessage('not_interested', titleTarget(recoTitle(1)))).toBe('Hidden.');
  });
});

describe('suppressionInput', () => {
  const reco = recoAnnotation({ key: remoteKey('v1') });

  it('sends a remote item with its identity and the list it was used on', () => {
    const entry = recoEntry('v1', 0, { uploader: 'Harbor Films', title: 'Harbor walk', webpage_url: 'https://www.youtube.com/watch?v=v1', source: 'youtube' });
    expect(suppressionInput('not_interested', remoteTarget(entry), reco)).toMatchObject({
      scope: 'item', source: 'youtube', source_id: 'v1', source_url: 'https://www.youtube.com/watch?v=v1', title: 'Harbor walk',
      uploader: 'Harbor Films', list_id: LIST_ID, key: remoteKey('v1'),
    });
  });

  it('sends show-fewer and hide-channel with the stable channel id when the entry carries one', () => {
    const entry = recoEntry('v1', 0, { uploader: 'Harbor Films', uploader_id: CHANNEL_ID });
    expect(suppressionInput('fewer', remoteTarget(entry), reco)).toMatchObject({ scope: 'fewer', uploader: 'Harbor Films', channel_id: CHANNEL_ID, list_id: LIST_ID });
    expect(suppressionInput('hide_channel', remoteTarget(entry), reco)).toMatchObject({ scope: 'channel', channel_id: CHANNEL_ID });
    expect(suppressionInput('hide_channel', remoteTarget(entry), reco)).not.toHaveProperty('source_id');
  });

  it('cuts or omits what the endpoint would refuse', () => {
    const entry = recoEntry('v1', 0, { title: 'T'.repeat(600), uploader: 'U'.repeat(600), webpage_url: `https://example.test/${'p'.repeat(3000)}`, uploader_url: `https://example.test/${'c'.repeat(3000)}` });
    const input = suppressionInput('not_interested', remoteTarget(entry), reco);
    expect(input.title).toHaveLength(512);
    expect(input.uploader).toHaveLength(512);
    expect(input.source_url).toBeNull();
    expect(input.channel_url).toBeNull();
  });

  it('sends a title by id, never as a remote item, and tolerates a missing annotation', () => {
    const input = suppressionInput('not_interested', titleTarget(recoTitle(7)), null);
    expect(input).toMatchObject({ scope: 'title', title_id: recoTitle(7).id, title: 'Recommended Film 7', list_id: null, key: null });
    expect(input).not.toHaveProperty('source_id');
  });
});

describe('relatedDropPredicate', () => {
  it('drops the item for a veto or show-fewer, and the whole channel for hide-channel', () => {
    const a = recoEntry('a', 0, { uploader: 'Harbor Films', webpage_url: 'https://www.youtube.com/watch?v=aaaaaaaaaaa' });
    const same = recoEntry('b', 1, { uploader: ' harbor films ', webpage_url: 'https://www.youtube.com/watch?v=bbbbbbbbbbb' });
    expect(relatedDropPredicate('not_interested', remoteTarget(a))(a)).toBe(true);
    expect(relatedDropPredicate('fewer', remoteTarget(a))(same)).toBe(false);
    expect(relatedDropPredicate('hide_channel', remoteTarget(a))(same)).toBe(true);
    expect(relatedDropPredicate('hide_channel', remoteTarget(a))(recoEntry('c', 2, { uploader: 'Other' }))).toBe(false);
  });

  it('never matches anything for a title target or a nameless channel', () => {
    expect(relatedDropPredicate('not_interested', titleTarget(recoTitle(1)))(recoEntry('a'))).toBe(false);
    expect(relatedDropPredicate('hide_channel', bare)(recoEntry('a', 0, { uploader: null }))).toBe(false);
  });
});

describe('followHadSuppression', () => {
  const entry = (scope: 'item' | 'channel' | 'fewer' | 'title', channel_name: string | null) => ({ id: scope, scope, target_key: 'k', title: null, channel_name, source: 'youtube', created_at: '2026-09-20T10:00:00Z' });

  it('is true when the list held a fewer or channel suppression for that channel, ignoring case and spacing', () => {
    expect(followHadSuppression({ items: [], channels: [entry('channel', 'Harbor Films')], fewer: [] }, ' harbor films ')).toBe(true);
    expect(followHadSuppression({ items: [], channels: [], fewer: [entry('fewer', 'Harbor Films')] }, 'Harbor Films')).toBe(true);
  });

  it('is false for another channel, an item or title suppression, a nameless channel, or an older list without fewer', () => {
    expect(followHadSuppression({ items: [entry('item', 'Harbor Films')], channels: [entry('channel', 'Other')], fewer: [] }, 'Harbor Films')).toBe(false);
    expect(followHadSuppression({ items: [], channels: [entry('channel', 'Harbor Films')] }, '')).toBe(false);
    expect(followHadSuppression({ items: [], channels: [] }, 'Harbor Films')).toBe(false);
  });
});

describe('keys and ids', () => {
  it('keys a remote card by address (else id) and a title by id', () => {
    expect(targetKey(harbor)).toBe(recoEntry('v1').webpage_url || 'v1');
    expect(targetKey(titleTarget(recoTitle(2)))).toBe(recoTitle(2).id);
  });

  it('gives each annotation of a list its own element id', () => {
    expect(reasonIdFor(recoAnnotation({ position: 0 }))).not.toBe(reasonIdFor(recoAnnotation({ position: 1 })));
    expect(reasonIdFor(recoAnnotation({ position: 3 }))).toBe(`reco-${LIST_ID}-3`);
  });
});

describe('orderRails', () => {
  const rails = [{ key: 'popular-gaming' }, { key: 'popular-music' }, { key: 'popular-news' }];

  it('follows the member\'s order and keeps unnamed categories after, in their own order', () => {
    expect(orderRails(rails, ['music', 'gaming']).map((rail) => rail.key)).toEqual(['popular-music', 'popular-gaming', 'popular-news']);
    expect(orderRails(rails, ['news']).map((rail) => rail.key)).toEqual(['popular-news', 'popular-gaming', 'popular-music']);
  });

  it('ignores unknown keys and leaves the rails alone with no order', () => {
    expect(orderRails(rails, ['cooking', 'music']).map((rail) => rail.key)).toEqual(['popular-music', 'popular-gaming', 'popular-news']);
    expect(orderRails(rails, undefined)).toEqual(rails);
    expect(orderRails(rails, [])).toEqual(rails);
    expect(orderRails(rails, null)).toEqual(rails);
  });
});

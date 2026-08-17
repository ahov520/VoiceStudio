import { describe, expect, it } from 'vitest';

import { revokeRemovedStoryPreviewUrls, storyPreviewUrls } from './StoriesEditor';

describe('storyPreviewUrls', () => {
  it('tracks only revocable preview blobs', () => {
    expect(
      storyPreviewUrls([
        { audioUrl: 'blob:first-preview' },
        { audioUrl: '/api/audio/render.wav' },
        { audioUrl: null },
        { audioUrl: 'blob:second-preview' },
      ]),
    ).toEqual(new Set(['blob:first-preview', 'blob:second-preview']));
  });

  it('revokes previews that were replaced while retaining active ones', () => {
    const revoked = [];

    revokeRemovedStoryPreviewUrls(
      new Set(['blob:old-preview', 'blob:shared-preview']),
      new Set(['blob:shared-preview', 'blob:new-preview']),
      (url) => revoked.push(url),
    );

    expect(revoked).toEqual(['blob:old-preview']);
  });
});

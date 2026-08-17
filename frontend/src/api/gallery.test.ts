import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiJson = vi.fn();

vi.mock('./client', () => ({
  apiJson: (...args: unknown[]) => apiJson(...args),
  apiPost: vi.fn(),
}));

import { deleteGalleryVoice, listGalleryVoices, previewVoiceUrl, saveVoiceAsProfile } from './gallery';

describe('deleteGalleryVoice', () => {
  beforeEach(() => apiJson.mockReset());

  it('uses the checked JSON client so HTTP errors reject the deletion', async () => {
    const failure = new Error('voice is still in use');
    apiJson.mockRejectedValue(failure);

    await expect(deleteGalleryVoice('voice/a b')).rejects.toBe(failure);
    expect(apiJson).toHaveBeenCalledWith('/gallery/voices/voice%2Fa%20b', { method: 'DELETE' });
  });
});

describe('gallery voice path parameters', () => {
  beforeEach(() => apiJson.mockReset());

  it('encodes voice IDs when saving a profile', async () => {
    apiJson.mockResolvedValue({ profile_id: 'p1', name: 'Profile' });

    await saveVoiceAsProfile('voice/a b', 'My profile');

    expect(apiJson).toHaveBeenCalledWith(
      '/gallery/voices/voice%2Fa%20b/save-as-profile?profile_name=My%20profile',
      { method: 'POST' },
    );
  });

  it('encodes voice IDs in preview URLs', () => {
    expect(previewVoiceUrl('voice/a b')).toBe('/gallery/voices/voice%2Fa%20b/preview');
  });
});

describe('gallery voice filters', () => {
  beforeEach(() => apiJson.mockReset());

  it('omits unset filters instead of sending the literal undefined', async () => {
    apiJson.mockResolvedValue([]);

    await listGalleryVoices({ category: undefined, search: 'dramatic', limit: undefined });

    expect(apiJson).toHaveBeenCalledWith('/gallery/voices?search=dramatic');
  });

  it('does not add a question mark when all filters are unset', async () => {
    apiJson.mockResolvedValue([]);

    await listGalleryVoices({ category: undefined, search: undefined });

    expect(apiJson).toHaveBeenCalledWith('/gallery/voices');
  });
});

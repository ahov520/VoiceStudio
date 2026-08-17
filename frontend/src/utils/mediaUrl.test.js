import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();

async function loadFileToMediaUrl({ tauri }) {
  vi.resetModules();
  vi.doMock('./apiBase', () => ({
    API_BASE: 'http://127.0.0.1:3900',
    isTauriContext: () => tauri,
  }));
  vi.doMock('../api/client', () => ({ apiFetch }));
  return (await import('./media')).fileToMediaUrl;
}

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperties(URL, {
    createObjectURL: { configurable: true, value: vi.fn(() => 'blob:new-preview') },
    revokeObjectURL: { configurable: true, value: vi.fn() },
  });
});

describe('fileToMediaUrl', () => {
  it('keeps the current preview alive until a Tauri upload finishes', async () => {
    let finishUpload;
    apiFetch.mockReturnValue(
      new Promise((resolve) => {
        finishUpload = resolve;
      }),
    );
    const fileToMediaUrl = await loadFileToMediaUrl({ tauri: true });

    const pending = fileToMediaUrl(new File(['audio'], 'next.wav'), {
      videoUrl: 'blob:current-preview',
      audioUrl: 'blob:current-preview',
    });
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    finishUpload({ json: async () => ({ url: '/preview/next.wav' }) });
    await expect(pending).resolves.toEqual({
      videoUrl: 'http://127.0.0.1:3900/preview/next.wav',
      audioUrl: 'http://127.0.0.1:3900/preview/next.wav',
    });
    expect(URL.revokeObjectURL).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:current-preview');
  });

  it('releases the current preview only after a failed upload has a browser fallback', async () => {
    apiFetch.mockRejectedValue(new Error('offline'));
    const fileToMediaUrl = await loadFileToMediaUrl({ tauri: true });

    await expect(
      fileToMediaUrl(new File(['audio'], 'next.wav'), {
        videoUrl: 'blob:current-preview',
        audioUrl: 'blob:current-preview',
      }),
    ).resolves.toEqual({ videoUrl: 'blob:new-preview', audioUrl: 'blob:new-preview' });
    expect(URL.createObjectURL).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:current-preview');
  });
});

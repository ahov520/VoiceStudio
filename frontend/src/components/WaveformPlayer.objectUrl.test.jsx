import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const mediaMocks = vi.hoisted(() => ({
  fileToMediaUrl: vi.fn(),
}));

vi.mock('../utils/media', () => ({
  isTauri: true,
  fileToMediaUrl: mediaMocks.fileToMediaUrl,
}));

vi.mock('wavesurfer.js', () => ({
  default: {
    create: vi.fn(() => {
      throw new Error('not needed for URL lifecycle test');
    }),
  },
}));

import WaveformPlayer from './WaveformPlayer';
import { activePlaybackSource } from '../utils/playback';

describe('WaveformPlayer object URL lifecycle', () => {
  afterEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
  });

  it('revokes the Tauri blob fallback when the player unmounts', async () => {
    const blobUrl = 'blob:tauri-preview-fallback';
    mediaMocks.fileToMediaUrl.mockResolvedValue({ videoUrl: blobUrl, audioUrl: blobUrl });
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});

    const { unmount } = render(<WaveformPlayer src={new File(['audio'], 'sample.wav')} />);
    await waitFor(() => expect(mediaMocks.fileToMediaUrl).toHaveBeenCalledOnce());
    await waitFor(() => expect(document.querySelector('audio')).toHaveAttribute('src', blobUrl));

    unmount();
    expect(revoke).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith(blobUrl);
  });

  it('revokes a blob fallback that resolves after unmount', async () => {
    let resolvePreview;
    mediaMocks.fileToMediaUrl.mockReturnValue(
      new Promise((resolve) => {
        resolvePreview = resolve;
      }),
    );
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    const { unmount } = render(<WaveformPlayer src={new File(['audio'], 'sample.wav')} />);
    await waitFor(() => expect(mediaMocks.fileToMediaUrl).toHaveBeenCalledOnce());
    unmount();

    const blobUrl = 'blob:late-tauri-preview-fallback';
    resolvePreview({ videoUrl: blobUrl, audioUrl: blobUrl });
    await waitFor(() => expect(revoke).toHaveBeenCalledWith(blobUrl));
  });

  it('releases the native fallback playback claim on unmount', async () => {
    const blobUrl = 'blob:native-fallback';
    mediaMocks.fileToMediaUrl.mockResolvedValue({ audioUrl: blobUrl });
    vi.spyOn(console, 'warn').mockImplementation(() => {});

    const { unmount } = render(<WaveformPlayer src={new File(['audio'], 'sample.wav')} />);
    const audio = await waitFor(() => document.querySelector('audio'));
    fireEvent.play(audio);
    expect(activePlaybackSource()).toBe('output');

    unmount();
    expect(activePlaybackSource()).toBeNull();
  });
});

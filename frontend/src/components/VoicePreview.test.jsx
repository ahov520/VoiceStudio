import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';

const mocks = vi.hoisted(() => ({
  generateSpeech: vi.fn(),
  stopActivePlayback: vi.fn(),
}));

vi.mock('../api/generate', () => ({ generateSpeech: mocks.generateSpeech }));
vi.mock('../utils/playback', () => ({ stopActivePlayback: mocks.stopActivePlayback }));
vi.mock('./WaveformPlayer', () => ({
  default: ({ src }) => <div data-testid="waveform-player">{src}</div>,
}));

import VoicePreview from './VoicePreview';

function renderPreview(props = {}) {
  return render(
    <I18nextProvider i18n={i18n}>
      <VoicePreview
        open
        onClose={vi.fn()}
        fileToMediaUrl={vi.fn(async () => ({ audioUrl: 'preview.wav' }))}
        {...props}
      />
    </I18nextProvider>,
  );
}

describe('VoicePreview', () => {
  beforeEach(() => {
    mocks.generateSpeech.mockReset();
    mocks.stopActivePlayback.mockReset();
  });

  it('aborts generation when the preview closes and ignores its late response', async () => {
    let resolveResponse;
    mocks.generateSpeech.mockImplementation(
      (_body, { signal }) =>
        new Promise((resolve) => {
          resolveResponse = resolve;
          signal.addEventListener('abort', () => {});
        }),
    );
    const fileToMediaUrl = vi.fn(async () => ({ audioUrl: 'stale.wav' }));
    const { rerender } = renderPreview({ fileToMediaUrl });

    fireEvent.click(screen.getByRole('button', { name: /preview/i }));
    await waitFor(() => expect(mocks.generateSpeech).toHaveBeenCalledOnce());
    const signal = mocks.generateSpeech.mock.calls[0][1].signal;

    rerender(
      <I18nextProvider i18n={i18n}>
        <VoicePreview open={false} onClose={vi.fn()} fileToMediaUrl={fileToMediaUrl} />
      </I18nextProvider>,
    );
    expect(signal.aborted).toBe(true);

    resolveResponse({ ok: true, blob: async () => new Blob(['audio']) });
    await waitFor(() => expect(fileToMediaUrl).not.toHaveBeenCalled());

    rerender(
      <I18nextProvider i18n={i18n}>
        <VoicePreview open onClose={vi.fn()} fileToMediaUrl={fileToMediaUrl} />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId('waveform-player')).not.toBeInTheDocument();
  });
});

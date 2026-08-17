import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { copyToClipboard, requestDictationCapture, toast } = vi.hoisted(() => ({
  copyToClipboard: vi.fn(),
  requestDictationCapture: vi.fn(),
  toast: { error: vi.fn(), success: vi.fn() },
}));

vi.mock('../utils/copyText', () => ({ copyText: copyToClipboard }));
vi.mock('../utils/dictationCapture', () => ({ requestDictationCapture }));
vi.mock('../components/EngineQuickSwitch', () => ({ default: () => null }));
vi.mock('../hooks/useEffectiveDictationShortcut', () => ({
  useEffectiveDictationShortcut: () => ({
    info: {
      accelerator: 'Super+Shift+V',
      display: 'Super+Shift+V',
      backend: 'portal',
    },
  }),
}));
vi.mock('react-hot-toast', () => ({ toast }));

import TranscriptionsPage, { addTranscription } from './Transcriptions';

describe('Transcriptions capture entry point', () => {
  beforeEach(() => {
    localStorage.clear();
    copyToClipboard.mockReset().mockResolvedValue(true);
    requestDictationCapture.mockReset().mockResolvedValue(undefined);
    toast.error.mockReset();
    toast.success.mockReset();
  });

  it('shows the effective shortcut and starts the shared recorder from the empty state', async () => {
    render(<TranscriptionsPage />);
    expect(screen.getByText(/Super\+Shift\+V/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Start dictation' }).at(-1));
    await waitFor(() => expect(requestDictationCapture).toHaveBeenCalledWith('start'));
  });

  it('reports a capture-controller failure', async () => {
    requestDictationCapture.mockRejectedValueOnce(new Error('event channel unavailable'));
    render(<TranscriptionsPage />);
    fireEvent.click(screen.getAllByRole('button', { name: 'Start dictation' }).at(-1));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        'Could not start dictation. Check microphone access, then try again.',
      ),
    );
  });

  it('keeps the capture action available for whitespace-only searches', () => {
    render(<TranscriptionsPage />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Search transcriptions…' }), {
      target: { value: '   ' },
    });

    expect(screen.getAllByRole('button', { name: 'Start dictation' })).toHaveLength(2);
    expect(screen.getByText('No transcriptions yet')).toBeInTheDocument();
  });

  it('shows a successful transcript emitted by the shared recorder', async () => {
    render(<TranscriptionsPage />);
    act(() => {
      addTranscription({ text: 'The shared capture path works.', language: 'en' });
    });

    expect(await screen.findByText('The shared capture path works.')).toBeInTheDocument();
  });

  it('refreshes when another tab changes the transcript history', async () => {
    render(<TranscriptionsPage />);
    act(() => {
      localStorage.setItem('omni_transcriptions', JSON.stringify([
        { id: 42, text: 'Added in another tab', language: 'en', timestamp: new Date().toISOString() },
      ]));
      window.dispatchEvent(new StorageEvent('storage', { key: 'omni_transcriptions' }));
    });

    expect(await screen.findByText('Added in another tab')).toBeInTheDocument();
  });

  it('copies the selected transcript through the shared clipboard helper', async () => {
    addTranscription({ text: 'Text worth copying', language: 'en' });
    render(<TranscriptionsPage />);

    fireEvent.click(screen.getByRole('listitem'));
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));

    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith('Text worth copying'));
    expect(copyToClipboard).toHaveBeenCalledTimes(1);
    expect(toast.success).toHaveBeenCalledWith('Copied to clipboard');
  });
});

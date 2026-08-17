import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider } from 'react-i18next';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import en from '../i18n/locales/en.json';

vi.mock('../api/engines', () => ({
  listEngines: vi.fn().mockResolvedValue({ tts: { active: 'x', backends: [] } }),
}));
vi.mock('../api/generate', () => ({ audioUrl: (file) => `http://test.local/audio/${file}` }));
vi.mock('../api/audiobook', () => ({
  audiobookPlan: vi.fn(),
  audiobookGenerate: vi.fn(),
  audiobookUploadCover: vi.fn(),
  audiobookPreviewChapter: vi.fn(),
  audiobookImport: vi.fn(),
}));

import AudiobookTab from './AudiobookTab';
import { useAppStore } from '../store';

function renderTab() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <AudiobookTab profiles={[]} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('AudiobookTab cover object URLs', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.getState().setScript('');
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn().mockReturnValueOnce('blob:first').mockReturnValueOnce('blob:second'),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it('releases each cover preview once when cleared or unmounted', () => {
    const view = renderTab();
    fireEvent.click(screen.getByRole('button', { name: en.audiobook.details }));

    fireEvent.change(screen.getByLabelText(en.audiobook.cover_add), {
      target: { files: [new File(['first'], 'first.png')] },
    });
    fireEvent.click(screen.getByRole('button', { name: en.audiobook.cover_remove }));
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenLastCalledWith('blob:first');

    fireEvent.change(screen.getByLabelText(en.audiobook.cover_add), {
      target: { files: [new File(['second'], 'second.png')] },
    });

    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
    expect(URL.revokeObjectURL).toHaveBeenLastCalledWith('blob:second');
  });

  it('allows selecting the same cover file again after removal', () => {
    renderTab();
    fireEvent.click(screen.getByRole('button', { name: en.audiobook.details }));
    const input = screen.getByLabelText(en.audiobook.cover_add);
    const file = new File(['cover'], 'cover.png', { type: 'image/png' });

    fireEvent.change(input, { target: { files: [file] } });
    expect(input.value).toBe('');
    fireEvent.click(screen.getByRole('button', { name: en.audiobook.cover_remove }));
    fireEvent.change(screen.getByLabelText(en.audiobook.cover_add), { target: { files: [file] } });

    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
  });
});

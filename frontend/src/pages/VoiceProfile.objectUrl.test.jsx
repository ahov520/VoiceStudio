import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';

const { generateSpeech } = vi.hoisted(() => ({ generateSpeech: vi.fn() }));

vi.mock('../api/generate', () => ({ generateSpeech }));
vi.mock('../api/profiles', () => ({
  getProfile: vi.fn().mockResolvedValue({ id: 'voice-1', name: 'Test voice' }),
  getProfileUsage: vi.fn().mockResolvedValue(null),
  updateProfile: vi.fn(),
  deleteProfile: vi.fn(),
  unlockProfile: vi.fn(),
  recordConsent: vi.fn(),
  revokeConsent: vi.fn(),
  exportPersona: vi.fn(),
}));
vi.mock('../hooks/useRecording', () => ({
  default: () => ({ isRecording: false, isCleaning: false, startRecording: vi.fn() }),
}));
vi.mock('../components/profile/ProfileHeader', () => ({ default: () => null }));
vi.mock('../components/profile/ProfileDetails', () => ({ default: () => null }));
vi.mock('../components/profile/ProfileActivity', () => ({
  default: ({ runTest }) => <button onClick={runTest}>Generate preview</button>,
}));

import VoiceProfile from './VoiceProfile';

describe('VoiceProfile preview object URLs', () => {
  beforeEach(() => {
    generateSpeech.mockReset();
    generateSpeech.mockResolvedValue({ blob: () => Promise.resolve(new Blob(['audio'])) });
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn().mockReturnValueOnce('blob:first').mockReturnValueOnce('blob:second'),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  it('releases each generated preview once when replaced or unmounted', async () => {
    const view = render(
      <I18nextProvider i18n={i18n}>
        <VoiceProfile voiceId="voice-1" onBack={vi.fn()} />
      </I18nextProvider>,
    );

    const generate = await screen.findByRole('button', { name: 'Generate preview' });
    fireEvent.click(generate);
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(1));

    fireEvent.click(generate);
    await waitFor(() => expect(URL.createObjectURL).toHaveBeenCalledTimes(2));
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:first');

    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
    expect(URL.revokeObjectURL).toHaveBeenLastCalledWith('blob:second');
  });
});

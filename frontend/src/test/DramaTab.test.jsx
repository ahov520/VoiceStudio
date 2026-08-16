import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import i18n from '../i18n';

import DramaTab from '../pages/DramaTab';
import { parseDrama, saveDramaProject, listDramaProjects } from '../api/drama';

vi.mock('../api/drama', () => ({
  parseDrama: vi.fn(),
  saveDramaProject: vi.fn(),
  listDramaProjects: vi.fn(),
  getDramaProject: vi.fn(),
  deleteDramaProject: vi.fn(),
}));

const SCRIPT = '林晚: 你走吧。\n老陈: 别这样。';

const PARSE_RESULT = {
  cast: [
    {
      name: '林晚',
      aliases: [],
      description: '女主',
      voice: { recipe_instruct: '女主' },
      candidates: [],
    },
    {
      name: '老陈',
      aliases: [],
      description: '男配',
      voice: { recipe_instruct: '男配' },
      candidates: [],
    },
  ],
  lines: [
    { speaker: '林晚', text: '你走吧。', emotion: 'sad', intensity: 0.8, stage: '' },
    { speaker: '老陈', text: '别这样。', emotion: 'calm', intensity: 0.4, stage: '' },
  ],
  script_text: '# Drama\n[voice:林晚][slow]你走吧。[/slow] [pause 400]',
  voice_map: {},
};

function renderTab() {
  return render(
    <I18nextProvider i18n={i18n}>
      <DramaTab profiles={[]} />
    </I18nextProvider>,
  );
}

describe('DramaTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    parseDrama.mockResolvedValue(PARSE_RESULT);
    listDramaProjects.mockResolvedValue({ projects: [] });
    saveDramaProject.mockResolvedValue({ id: 'abc', name: '测试剧' });
  });

  it('runs the director and shows cast + emotion-annotated lines', async () => {
    renderTab();
    fireEvent.change(screen.getByPlaceholderText(/Paste a script/), {
      target: { value: SCRIPT },
    });
    fireEvent.click(screen.getByRole('button', { name: /Auto-direct/ }));

    await waitFor(() => expect(parseDrama).toHaveBeenCalledWith(SCRIPT, []));
    expect((await screen.findAllByText('林晚')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('老陈').length).toBeGreaterThan(0);
    // Emotion select on the sad line defaults to sad.
    const sadSelect = screen.getAllByLabelText('Emotion')[0];
    expect(sadSelect.value).toBe('sad');
    // Compiled audiobook script shown.
    expect(screen.getByDisplayValue(/\[voice:林晚\]/)).toBeInTheDocument();
  });

  it('saves the project through the API', async () => {
    renderTab();
    fireEvent.change(screen.getByPlaceholderText(/Paste a script/), {
      target: { value: SCRIPT },
    });
    fireEvent.click(screen.getByRole('button', { name: /Auto-direct/ }));
    await waitFor(() => expect(screen.getAllByText('林晚').length).toBeGreaterThan(0));

    fireEvent.change(screen.getByPlaceholderText(/Project name/), {
      target: { value: '测试剧' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save/ }));

    await waitFor(() =>
      expect(saveDramaProject).toHaveBeenCalledWith(
        expect.objectContaining({ name: '测试剧', cast: PARSE_RESULT.cast }),
      ),
    );
  });

  it('shows a parse error inline when the director fails', async () => {
    parseDrama.mockRejectedValue(new Error('LLM unavailable'));
    renderTab();
    fireEvent.change(screen.getByPlaceholderText(/Paste a script/), {
      target: { value: SCRIPT },
    });
    fireEvent.click(screen.getByRole('button', { name: /Auto-direct/ }));
    expect(await screen.findByText('LLM unavailable')).toBeInTheDocument();
  });
});

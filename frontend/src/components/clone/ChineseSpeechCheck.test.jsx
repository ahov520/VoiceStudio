import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ChineseSpeechCheck from './ChineseSpeechCheck';

// The dictionary round-trip is the only backend touch — stub the client.
vi.mock('../../api/client', () => ({
  apiJson: vi.fn(async () => []),
  apiFetch: vi.fn(async () => ({ ok: true, status: 200 })),
}));
import { apiJson, apiFetch } from '../../api/client';

const t = (k) =>
  ({
    'zhcheck.open_btn': 'Speech check',
    'zhcheck.title': 'Chinese speech check',
    'zhcheck.save': 'Save',
    'zhcheck.covered': 'in dictionary',
    'zhcheck.no_polyphones': 'No polyphones detected.',
    'zhcheck.apply': 'Apply',
    'zhcheck.applied': 'Applied',
    'zhcheck.error': 'Failed',
    'common.close': 'Close',
  })[k] ?? k;

describe('ChineseSpeechCheck', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing without Chinese text', () => {
    const { container } = render(
      <ChineseSpeechCheck t={t} text="english only" setText={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('lists polyphone rows and number fixes, saves a dictionary entry', async () => {
    const setText = vi.fn();
    render(<ChineseSpeechCheck t={t} text="去重庆市的银行，已有10w粉丝" setText={setText} />);

    fireEvent.click(screen.getByTestId('zhcheck-toggle'));
    await waitFor(() => expect(apiJson).toHaveBeenCalledWith('/pronunciation'));

    expect(screen.getAllByTestId('zhcheck-row').length).toBe(2);
    expect(screen.getByTestId('zhcheck-save-重庆')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('zhcheck-save-重庆'));
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const [url, opts] = apiFetch.mock.calls[0];
    expect(url).toBe('/pronunciation');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({
      term: '重庆',
      replacement: '崇庆',
      type: 'respelling',
      language: 'zh',
      enabled: true,
    });
    await waitFor(() => expect(screen.getByText('in dictionary')).toBeInTheDocument());
  });

  it('applies a number rewrite to the text', async () => {
    const setText = vi.fn();
    render(<ChineseSpeechCheck t={t} text="已有10w粉丝" setText={setText} />);
    fireEvent.click(screen.getByTestId('zhcheck-toggle'));
    await waitFor(() => expect(apiJson).toHaveBeenCalled());

    fireEvent.click(screen.getByTestId('zhcheck-apply-number'));
    expect(setText).toHaveBeenCalledWith('已有10万粉丝');
    await waitFor(() => expect(screen.getByText('Applied')).toBeInTheDocument());
  });

  it('marks terms already present in the zh dictionary', async () => {
    apiJson.mockResolvedValueOnce([{ id: 7, term: '重庆', language: 'zh' }]);
    render(<ChineseSpeechCheck t={t} text="去重庆市" setText={() => {}} />);
    fireEvent.click(screen.getByTestId('zhcheck-toggle'));
    await waitFor(() => expect(screen.getByText('in dictionary')).toBeInTheDocument());
  });
});

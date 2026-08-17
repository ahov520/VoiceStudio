import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const { clearTauriLogs, toastError, toastSuccess, backendRefetch, tauriRefetch } = vi.hoisted(() => ({
  clearTauriLogs: vi.fn(),
  toastError: vi.fn(),
  toastSuccess: vi.fn(),
  backendRefetch: vi.fn(),
  tauriRefetch: vi.fn(),
}));

// Partial mocks: only what the test drives is stubbed. The footer renders
// more consumers of these modules than this test cares about (engine quick
// switch, compute chip), and a hand-written module object silently drops
// whichever export they gain next.
vi.mock('../api/system', async (importOriginal) => ({
  ...(await importOriginal()),
  clearSystemLogs: vi.fn(),
  clearTauriLogs,
}));
vi.mock('../api/hooks', async (importOriginal) => ({
  ...(await importOriginal()),
  useSystemLogs: () => ({ data: null, refetch: backendRefetch }),
  useTauriLogs: () => ({ data: null, refetch: tauriRefetch }),
  useVisibleNotifications: () => ({ notifications: [] }),
  isDismissibleNotification: () => false,
}));
vi.mock('../components/NetworkToggle', () => ({ default: () => null }));
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { error: toastError, success: toastSuccess }),
}));

import LogsFooter from '../components/LogsFooter';

function renderFooter() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <LogsFooter />
    </QueryClientProvider>,
  );
}

describe('LogsFooter Tauri cleanup failure', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('omnivoice.logs.active', 'tauri');
    clearTauriLogs.mockReset();
    toastError.mockReset();
    toastSuccess.mockReset();
    backendRefetch.mockReset();
    tauriRefetch.mockReset();
  });

  it('shows failure and never claims the log was cleared', async () => {
    clearTauriLogs.mockRejectedValueOnce(new Error('desktop log is locked'));
    renderFooter();

    fireEvent.click(screen.getByRole('button', { name: /expand logs panel/i }));
    fireEvent.click(screen.getByRole('button', { name: /clear log/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('keeps refresh disabled until both log queries finish', async () => {
    let resolveBackend;
    let resolveTauri;
    backendRefetch.mockReturnValueOnce(new Promise((resolve) => { resolveBackend = resolve; }));
    tauriRefetch.mockReturnValueOnce(new Promise((resolve) => { resolveTauri = resolve; }));
    renderFooter();
    fireEvent.click(screen.getByRole('button', { name: /expand logs panel/i }));
    const refresh = screen.getByRole('button', { name: /refresh logs/i });
    fireEvent.click(refresh);
    expect(refresh).toBeDisabled();
    resolveBackend();
    await Promise.resolve();
    expect(refresh).toBeDisabled();
    resolveTauri();
    await waitFor(() => expect(refresh).not.toBeDisabled());
  });
});

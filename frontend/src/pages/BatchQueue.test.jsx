import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { listBatchJobs } = vi.hoisted(() => ({ listBatchJobs: vi.fn() }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key) => `translated:${key}` }),
}));
vi.mock('../api/batch', () => ({
  listBatchJobs,
  getBatchJob: vi.fn(),
  cancelBatchJob: vi.fn(),
  deleteBatchJob: vi.fn(),
  enqueueBatchJob: vi.fn(),
}));
vi.mock('../components/BatchAddDialog', () => ({ default: () => null }));
vi.mock('../ui', () => ({
  Panel: ({ children }) => <div>{children}</div>,
  Button: ({ children, onClick }) => <button onClick={onClick}>{children}</button>,
  Badge: ({ children }) => <span>{children}</span>,
  Tabs: ({ items, onChange }) => (
    <div>
      {items.map((item) => (
        <button key={item.id} onClick={() => onChange(item.id)}>
          {item.id}
        </button>
      ))}
    </div>
  ),
  EmptyState: ({ description }) => <div>{description}</div>,
}));

import BatchQueue from './BatchQueue';

describe('BatchQueue empty states', () => {
  beforeEach(() => listBatchJobs.mockReset().mockResolvedValue([]));

  it.each([
    ['done', 'translated:batch.no_completed'],
    ['failed', 'translated:batch.no_failed'],
  ])('localizes the %s tab description', async (tab, description) => {
    render(<BatchQueue />);
    await waitFor(() => expect(listBatchJobs).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: tab }));

    expect(await screen.findByText(description)).toBeInTheDocument();
  });
});

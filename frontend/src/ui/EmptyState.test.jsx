import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Inbox, Plus } from 'lucide-react';

import EmptyState from './EmptyState';

describe('EmptyState', () => {
  it('renders icon, title and description', () => {
    render(<EmptyState icon={Inbox} title="Nothing here" description="Add something to begin" />);
    expect(screen.getByText('Nothing here')).toBeTruthy();
    expect(screen.getByText('Add something to begin')).toBeTruthy();
    // Icon slot is decorative — hidden from the accessibility tree.
    expect(document.querySelector('[aria-hidden="true"]')).toBeTruthy();
  });

  it('omits the description and CTA when not given', () => {
    const { container } = render(<EmptyState title="Empty" />);
    expect(container.querySelector('p')).toBeNull();
    expect(container.querySelector('button')).toBeNull();
  });

  it('fires the CTA onClick and renders its label', () => {
    const onClick = vi.fn();
    render(
      <EmptyState
        title="No jobs"
        action={{ label: 'Add to queue', onClick, leading: <Plus size={10} /> }}
      />,
    );
    const btn = screen.getByRole('button', { name: 'Add to queue' });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('supports the sm size without dropping content', () => {
    render(<EmptyState size="sm" title="Compact" description="still shown" />);
    expect(screen.getByText('Compact')).toBeTruthy();
    expect(screen.getByText('still shown')).toBeTruthy();
  });
});

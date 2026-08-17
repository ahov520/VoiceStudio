import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { notifications, useNotifications } = vi.hoisted(() => ({
  notifications: [],
  useNotifications: vi.fn(() => ({ notifications })),
}));

vi.mock('../api/hooks', () => ({
  useVisibleNotifications: () => useNotifications(),
}));

import NotificationPanel from './NotificationPanel';

describe('NotificationPanel', () => {
  beforeEach(() => notifications.splice(0));

  it('keeps the header usable while notifications are loading', () => {
    useNotifications.mockReturnValueOnce(undefined);
    render(<NotificationPanel />);
    expect(screen.getByRole('button', { name: 'Notifications (0)' })).toBeInTheDocument();
  });

  it('exposes one localized accessible name and opens the notification panel', () => {
    notifications.push({ id: 'warning', level: 'warn' });
    const onOpen = vi.fn();
    window.addEventListener('omni:open-notifications', onOpen, { once: true });

    render(<NotificationPanel />);

    const button = screen.getByRole('button', { name: 'Notifications (1)' });
    expect(button).toHaveAttribute('title', 'Notifications');
    expect(screen.getByText('1')).toHaveAttribute('aria-hidden', 'true');
    fireEvent.click(button);
    expect(onOpen).toHaveBeenCalledOnce();
  });

  it('keeps the badge compact while exposing the exact count accessibly', () => {
    notifications.push(...Array.from({ length: 125 }, (_, i) => ({ id: `n-${i}`, level: 'error' })));

    render(<NotificationPanel />);

    const button = screen.getByRole('button', { name: 'Notifications (125)' });
    expect(button).toHaveTextContent('99+');
    expect(button).not.toHaveTextContent('125');
  });

  it('uses the informational badge tone when no warning or error exists', () => {
    notifications.push({ id: 'status', level: 'info' });

    render(<NotificationPanel />);

    expect(screen.getByText('1')).toHaveClass('bg-info');
    expect(screen.getByText('1')).not.toHaveClass('bg-danger');
  });

  it('is non-submitting when rendered inside a form', () => {
    render(
      <form>
        <NotificationPanel />
      </form>,
    );

    expect(screen.getByRole('button')).toHaveAttribute('type', 'button');
  });
});

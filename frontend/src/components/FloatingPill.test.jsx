import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import FloatingPill from './FloatingPill';
import { useAppStore } from '../store';

describe('FloatingPill dismissal', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.getState().dismissPill();
  });

  afterEach(() => {
    vi.useRealTimers();
    useAppStore.getState().dismissPill();
  });

  it('does not dismiss a new operation when an old exit animation finishes', () => {
    act(() => useAppStore.getState().showPill('generating', 'Old render'));
    render(<FloatingPill />);

    fireEvent.click(screen.getByRole('button'));
    act(() => {
      vi.advanceTimersByTime(1);
      useAppStore.getState().showPill('transcribing', 'New capture');
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByText(/New capture/)).toBeInTheDocument();
    expect(useAppStore.getState().visible).toBe(true);
  });
});

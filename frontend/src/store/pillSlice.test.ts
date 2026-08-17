import { createStore } from 'zustand/vanilla';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPillSlice, type PillSlice } from './pillSlice';

const makeStore = () => createStore<PillSlice>()(createPillSlice);

describe('pill auto-dismiss', () => {
  afterEach(() => vi.useRealTimers());

  it('does not let an old completion timer dismiss a new operation', () => {
    vi.useFakeTimers();
    const store = makeStore();

    store.getState().completePill('First done');
    vi.advanceTimersByTime(1000);
    store.getState().showPill('generating', 'New render');
    vi.advanceTimersByTime(2000);

    expect(store.getState()).toMatchObject({
      visible: true,
      stage: 'generating',
      label: 'New render',
    });
  });

  it('gives the latest completion its full display time', () => {
    vi.useFakeTimers();
    const store = makeStore();

    store.getState().completePill('First done');
    vi.advanceTimersByTime(2000);
    store.getState().completePill('Second done');
    vi.advanceTimersByTime(1000);

    expect(store.getState()).toMatchObject({ visible: true, label: 'Second done' });
    vi.advanceTimersByTime(2000);
    expect(store.getState()).toMatchObject({ visible: false, stage: 'idle' });
  });
});

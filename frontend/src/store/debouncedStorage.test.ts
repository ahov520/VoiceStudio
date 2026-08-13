import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createDebouncedStorage } from './debouncedStorage';

function makeInner() {
  return {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  };
}

describe('createDebouncedStorage (persist write-behind)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('coalesces a burst of writes into one real setItem', () => {
    const inner = makeInner();
    const storage = createDebouncedStorage(inner, 300);
    // A typing burst: persist calls setItem once per keystroke.
    for (let i = 0; i < 50; i++) {
      storage.setItem('omnivoice.app', { state: { text: `v${i}` }, version: 7 } as never);
    }
    expect(inner.setItem).not.toHaveBeenCalled();
    vi.advanceTimersByTime(300);
    expect(inner.setItem).toHaveBeenCalledTimes(1);
    // The LAST value wins — nothing intermediate is written.
    expect(inner.setItem).toHaveBeenCalledWith('omnivoice.app', {
      state: { text: 'v49' },
      version: 7,
    });
  });

  it('guarantees a write at most delayMs after the first change (trailing throttle)', () => {
    const inner = makeInner();
    const storage = createDebouncedStorage(inner, 300);
    storage.setItem('k', { state: 1, version: 1 } as never);
    // Keep typing past the window — a resettable debounce would starve here.
    vi.advanceTimersByTime(200);
    storage.setItem('k', { state: 2, version: 1 } as never);
    vi.advanceTimersByTime(100);
    expect(inner.setItem).toHaveBeenCalledTimes(1);
    expect(inner.setItem).toHaveBeenCalledWith('k', { state: 2, version: 1 });
  });

  it('getItem always reads the inner storage — rehydrate means DISK truth', () => {
    // An externally written payload (a test seeding localStorage, a future
    // second window) must be readable immediately, even while a write from
    // this window is still queued — the v6→v7 uiScale migration test relies
    // on exactly this.
    const inner = makeInner();
    inner.getItem.mockReturnValue({ state: 'external', version: 6 } as never);
    const storage = createDebouncedStorage(inner, 300);
    storage.setItem('k', { state: 'queued', version: 7 } as never);
    expect(storage.getItem('k')).toEqual({ state: 'external', version: 6 });
  });

  it('flush() writes the pending value immediately (pagehide/beforeunload path)', () => {
    const inner = makeInner();
    const storage = createDebouncedStorage(inner, 300);
    storage.setItem('k', { state: 'bye', version: 1 } as never);
    storage.flush();
    expect(inner.setItem).toHaveBeenCalledTimes(1);
    expect(inner.setItem).toHaveBeenCalledWith('k', { state: 'bye', version: 1 });
    // Timer was cancelled — no double write later.
    vi.advanceTimersByTime(1000);
    expect(inner.setItem).toHaveBeenCalledTimes(1);
  });

  it('a pagehide event flushes the pending write', () => {
    const inner = makeInner();
    const storage = createDebouncedStorage(inner, 300);
    storage.setItem('k', { state: 'closing', version: 1 } as never);
    window.dispatchEvent(new Event('pagehide'));
    expect(inner.setItem).toHaveBeenCalledWith('k', { state: 'closing', version: 1 });
  });

  it('removeItem drops any pending write for that key', () => {
    const inner = makeInner();
    const storage = createDebouncedStorage(inner, 300);
    storage.setItem('k', { state: 'x', version: 1 } as never);
    storage.removeItem('k');
    vi.advanceTimersByTime(1000);
    expect(inner.setItem).not.toHaveBeenCalled();
    expect(inner.removeItem).toHaveBeenCalledWith('k');
  });
});

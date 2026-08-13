/**
 * Write-behind wrapper for zustand's persist storage.
 *
 * The persist middleware serializes + writes localStorage on EVERY store
 * update. With a whole imported novel in `storyTracks`/`script` that is a
 * multi-megabyte synchronous JSON.stringify + setItem per keystroke — the
 * single biggest reason the app felt laggy everywhere once a book was loaded
 * (the write happens no matter which page you're on).
 *
 * This wrapper coalesces writes: the first change arms a trailing timer, and
 * everything that lands within the window rides the same flush — so typing
 * costs at most one real write per `delayMs` instead of one per key.
 *
 * Reads always come from the inner storage. `rehydrate()` means "give me the
 * DISK truth" — it runs at startup (nothing pending yet) or as an explicit
 * reload of externally written state (tests, future multi-window sync), and
 * a queued write is by definition older than either. Short-circuiting reads
 * through the pending value would make an external write invisible until the
 * flush landed (it broke the v6→v7 uiScale migration test exactly that way).
 *
 * Durability: the pending write is flushed synchronously on `pagehide`,
 * `beforeunload` and tab-hide (`visibilitychange` → hidden), which covers
 * app close, reload and minimize on both browsers and the Tauri webviews.
 */
import type { PersistStorage, StorageValue } from 'zustand/middleware';

export const DEFAULT_PERSIST_DEBOUNCE_MS = 300;

export function createDebouncedStorage<S>(
  inner: PersistStorage<S>,
  delayMs: number = DEFAULT_PERSIST_DEBOUNCE_MS,
): PersistStorage<S> & { flush: () => void } {
  let pending: [string, StorageValue<S>] | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const flush = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    if (!pending) return;
    const [name, value] = pending;
    pending = null;
    inner.setItem(name, value);
  };

  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', flush);
    window.addEventListener('beforeunload', flush);
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') flush();
    });
  }

  return {
    flush,
    getItem: (name) => inner.getItem(name),
    setItem: (name, value) => {
      pending = [name, value];
      // Trailing throttle rather than a resettable debounce: continuous
      // typing must not starve the write forever — the first change in a
      // window guarantees a flush at most `delayMs` later.
      if (timer === null) {
        timer = setTimeout(flush, delayMs);
      }
    },
    removeItem: (name) => {
      if (pending && pending[0] === name) pending = null;
      inner.removeItem(name);
    },
  };
}

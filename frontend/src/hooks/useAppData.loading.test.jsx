import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useState } from 'react';

import { useLatestListLoader } from './useAppData';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function useHarness(fetchList) {
  const [items, setItems] = useState([]);
  const load = useLatestListLoader(fetchList, setItems, 'refresh failed:');
  return { items, load };
}

describe('useLatestListLoader', () => {
  it('does not let a slower stale refresh overwrite newer data', async () => {
    const first = deferred();
    const second = deferred();
    const fetchList = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useHarness(fetchList));

    let firstLoad;
    let secondLoad;
    act(() => {
      firstLoad = result.current.load();
      secondLoad = result.current.load();
    });
    await act(async () => {
      second.resolve(['new']);
      await secondLoad;
    });
    await act(async () => {
      first.resolve(['stale']);
      await firstLoad;
    });

    expect(result.current.items).toEqual(['new']);
  });

  it('ignores a stale failure after a newer refresh succeeds', async () => {
    const first = deferred();
    const second = deferred();
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const fetchList = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useHarness(fetchList));

    const firstLoad = result.current.load();
    const secondLoad = result.current.load();
    await act(async () => {
      second.resolve(['current']);
      await secondLoad;
      first.reject(new Error('old network failure'));
      await firstLoad;
    });

    expect(result.current.items).toEqual(['current']);
    expect(warning).not.toHaveBeenCalled();
    warning.mockRestore();
  });
});

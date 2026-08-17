import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { checkMicrophone, checkAccessibility } = vi.hoisted(() => ({
  checkMicrophone: vi.fn(),
  checkAccessibility: vi.fn(),
}));

vi.mock('../utils/permissions', () => ({
  inTauri: () => true,
  checkMicrophone: (...args) => checkMicrophone(...args),
  checkAccessibility: (...args) => checkAccessibility(...args),
}));

import usePermissions from './usePermissions';

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('usePermissions', () => {
  beforeEach(() => {
    checkMicrophone.mockReset();
    checkAccessibility.mockReset();
  });

  it('keeps the newest result when permission probes finish out of order', async () => {
    const oldMic = deferred();
    const oldA11y = deferred();
    const newMic = deferred();
    const newA11y = deferred();
    checkMicrophone.mockReturnValueOnce(oldMic.promise).mockReturnValueOnce(newMic.promise);
    checkAccessibility.mockReturnValueOnce(oldA11y.promise).mockReturnValueOnce(newA11y.promise);

    const { result } = renderHook(() => usePermissions());
    act(() => window.dispatchEvent(new Event('focus')));

    await act(async () => {
      newMic.resolve('granted');
      newA11y.resolve(false);
      await Promise.all([newMic.promise, newA11y.promise]);
    });
    expect(result.current).toMatchObject({ mic: 'granted', a11y: false });

    await act(async () => {
      oldMic.resolve('denied');
      oldA11y.resolve(true);
      await Promise.all([oldMic.promise, oldA11y.promise]);
    });
    expect(result.current).toMatchObject({ mic: 'granted', a11y: false });
  });
});

import { describe, expect, it, vi } from 'vitest';
import { bindAbortHandler } from '../hooks/useDubWorkflow.js';

describe('bindAbortHandler', () => {
  it('runs the handler once when the signal aborts', () => {
    const controller = new AbortController();
    const handler = vi.fn();

    bindAbortHandler(controller.signal, handler);
    controller.abort();

    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('detaches a completed stream without waiting for a later abort', () => {
    const controller = new AbortController();
    const handler = vi.fn();
    const unbind = bindAbortHandler(controller.signal, handler);

    unbind();
    unbind();
    controller.abort();

    expect(handler).not.toHaveBeenCalled();
  });

  it('handles a signal that was already aborted before the stream opened', () => {
    const controller = new AbortController();
    const handler = vi.fn();
    controller.abort();

    bindAbortHandler(controller.signal, handler);

    expect(handler).toHaveBeenCalledTimes(1);
  });
});

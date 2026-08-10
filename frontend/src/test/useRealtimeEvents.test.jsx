import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';

// The cold-start probe in useRealtimeEvents uses a RAW fetch() that does not
// carry the LAN PIN / remote API-key headers apiFetch would attach. It must
// therefore poll the auth-exempt /health endpoint — never a gated path like
// /model/status, which 401s in LAN-share/remote mode and would wedge the
// reconnect loop so the WebSocket never opens. This test pins that contract.
import useRealtimeEvents from '../hooks/useRealtimeEvents';

// Minimal WebSocket stub: records construction and lets us drive onopen.
class FakeWebSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = 0; // CONNECTING
    this.onopen = null;
    this.onmessage = null;
    this.onclose = null;
    this.onerror = null;
    FakeWebSocket.instances.push(this);
  }
  close() {
    this.readyState = 3; // CLOSED
  }
}

function Harness({ handlers = {} }) {
  useRealtimeEvents(handlers);
  return null;
}

describe('useRealtimeEvents cold-start health probe', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket);
    if (!AbortSignal.timeout) {
      AbortSignal.timeout = () => new AbortController().signal;
    }
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('probes the auth-exempt /health endpoint, not a gated path', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);

    render(<Harness />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const probedUrl = String(fetchMock.mock.calls[0][0]);
    expect(probedUrl).toMatch(/\/health$/);
    // Guard against the #439 regression: the gated path drops auth → 401.
    expect(probedUrl).not.toContain('/model/status');
  });

  it('opens the WebSocket once the health probe succeeds', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal('fetch', fetchMock);

    render(<Harness />);

    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    expect(FakeWebSocket.instances[0].url).toContain('/ws/events');
  });

  it('does not log remote-controlled malformed frames or parser details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(<Harness />);
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));

    const privateFrame = 'token=private-value\nFORGED';
    FakeWebSocket.instances[0].onmessage({ data: privateFrame });

    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith('[ws/events] malformed message ignored');
    const warningArguments = warn.mock.calls.flat();
    expect(warningArguments).not.toContain(privateFrame);
    expect(warningArguments.every((argument) => !String(argument).includes('SyntaxError'))).toBe(
      true,
    );
  });

  it('does not misclassify or swallow event-handler failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const failure = new Error('handler failed');
    render(
      <Harness
        handlers={{
          failed: () => {
            throw failure;
          },
        }}
      />,
    );
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));

    expect(() => FakeWebSocket.instances[0].onmessage({ data: '{"kind":"failed"}' })).toThrow(
      failure,
    );
    expect(warn).not.toHaveBeenCalled();
  });

  it('dispatches only explicitly registered own handlers', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    const inherited = vi.fn();
    const inheritedToString = vi.fn();
    const handlers = Object.create({ inherited, toString: inheritedToString });
    render(<Harness handlers={handlers} />);
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));

    FakeWebSocket.instances[0].onmessage({ data: '{"kind":"inherited"}' });
    FakeWebSocket.instances[0].onmessage({ data: '{"kind":"toString"}' });
    expect(inherited).not.toHaveBeenCalled();
    expect(inheritedToString).not.toHaveBeenCalled();
  });

  it('does NOT open the WebSocket while the backend is unreachable', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('ECONNREFUSED'));
    vi.stubGlobal('fetch', fetchMock);

    render(<Harness />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(FakeWebSocket.instances.length).toBe(0);
  });
});

import { describe, expect, it, vi } from 'vitest';
import { bindFallbackMediaEvents } from './WaveformTimeline';

describe('WaveformTimeline native-media fallback cleanup', () => {
  it('detaches every fallback listener', () => {
    const media = new EventTarget();
    const handlers = {
      timeupdate: vi.fn(),
      play: vi.fn(),
      pause: vi.fn(),
      ended: vi.fn(),
    };

    const cleanup = bindFallbackMediaEvents(media, handlers);
    for (const event of Object.keys(handlers)) media.dispatchEvent(new Event(event));
    cleanup();
    for (const event of Object.keys(handlers)) media.dispatchEvent(new Event(event));

    for (const handler of Object.values(handlers)) expect(handler).toHaveBeenCalledTimes(1);
  });
});

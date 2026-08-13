import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAppStore } from '../store';

// Batched translation (the "no progress + mysterious abort" fix):
//  - segments travel in TRANSLATE_BATCH_SIZE slices, sequentially — each
//    request returns quickly instead of one webview-killing mega-POST;
//  - every finished batch lands in the store immediately, so a later batch's
//    hard failure keeps the completed work;
//  - a segment-level progress pill is visible while translating;
//  - handleRetryFailedTranslations re-sends ONLY rows with translate_error.

const dubApi = vi.hoisted(() => ({
  dubUpload: vi.fn(),
  dubIngestUrl: vi.fn(),
  dubAbort: vi.fn(),
  dubCleanupSegments: vi.fn(),
  dubTranslate: vi.fn(),
  dubGenerate: vi.fn(),
  tasksStreamUrl: vi.fn(() => ''),
  tasksCancel: vi.fn(),
  transcribeStreamUrl: vi.fn(() => ''),
  dubImportSrt: vi.fn(),
}));
vi.mock('../api/dub', () => dubApi);
vi.mock('../api/client', () => ({
  apiPost: vi.fn(),
  apiFetch: vi.fn(),
  apiJson: vi.fn(),
  API: '',
}));

import useDubWorkflow, { TRANSLATE_BATCH_SIZE } from '../hooks/useDubWorkflow';

const baseState = useAppStore.getState();

function renderWorkflow() {
  return renderHook(() =>
    useDubWorkflow({
      loadProjects: vi.fn(),
      loadProfiles: vi.fn(),
      loadDubHistory: vi.fn(),
      setLastGenFingerprints: vi.fn(),
    }),
  );
}

function seedSegments(n) {
  return Array.from({ length: n }, (_, i) => ({
    id: String(i + 1),
    text: `line ${i + 1}`,
    text_original: `line ${i + 1}`,
    start: i,
    end: i + 1,
  }));
}

describe('handleTranslateAll — batched requests with a progress pill', () => {
  beforeEach(() => {
    useAppStore.setState(baseState, true);
    dubApi.dubTranslate.mockReset();
    useAppStore.setState({
      dubJobId: 'job1',
      dubStep: 'editing',
      dubLangCode: 'es',
      dubSegments: seedSegments(25),
    });
  });

  it('splits 25 segments into ceil(25/BATCH) sequential requests', async () => {
    dubApi.dubTranslate.mockImplementation(async ({ segments }) => ({
      translated: segments.map((s) => ({ id: s.id, text: `es:${s.text}` })),
      target_lang: 'es',
    }));
    const { result } = renderWorkflow();
    let ok;
    await act(async () => {
      ok = await result.current.handleTranslateAll();
    });
    const expectedCalls = Math.ceil(25 / TRANSLATE_BATCH_SIZE);
    expect(dubApi.dubTranslate).toHaveBeenCalledTimes(expectedCalls);
    for (const [payload] of dubApi.dubTranslate.mock.calls) {
      expect(payload.segments.length).toBeLessThanOrEqual(TRANSLATE_BATCH_SIZE);
      expect(payload.target_lang).toBe('es');
    }
    // Every batch covered every segment exactly once, in order.
    const sentIds = dubApi.dubTranslate.mock.calls.flatMap(([p]) => p.segments.map((s) => s.id));
    expect(sentIds).toEqual(seedSegments(25).map((s) => s.id));
    expect(ok).toBe(true);
    const segs = useAppStore.getState().dubSegments;
    expect(segs.every((s) => s.text.startsWith('es:'))).toBe(true);
    // The pill was dismissed when the run finished.
    expect(useAppStore.getState().visible).toBe(false);
  });

  it('shows a segment-level progress pill while batches run', async () => {
    const pillDuringCalls = [];
    dubApi.dubTranslate.mockImplementation(async ({ segments }) => {
      const s = useAppStore.getState();
      pillDuringCalls.push({ visible: s.visible, stage: s.stage, progress: s.progress });
      return {
        translated: segments.map((x) => ({ id: x.id, text: `es:${x.text}` })),
        target_lang: 'es',
      };
    });
    const { result } = renderWorkflow();
    await act(async () => {
      await result.current.handleTranslateAll();
    });
    expect(pillDuringCalls.length).toBeGreaterThan(1);
    expect(pillDuringCalls.every((p) => p.visible && p.stage === 'translating')).toBe(true);
    // Later batches see the progress the earlier ones reported.
    expect(pillDuringCalls.at(-1).progress).toBeGreaterThan(0);
  });

  it('a mid-run hard failure keeps the finished batches and resolves false', async () => {
    let call = 0;
    dubApi.dubTranslate.mockImplementation(async ({ segments }) => {
      call += 1;
      if (call === 2) throw new Error('engine down');
      return {
        translated: segments.map((s) => ({ id: s.id, text: `es:${s.text}` })),
        target_lang: 'es',
      };
    });
    const { result } = renderWorkflow();
    let ok;
    await act(async () => {
      ok = await result.current.handleTranslateAll();
    });
    expect(ok).toBe(false);
    const segs = useAppStore.getState().dubSegments;
    // Batch 1 (first TRANSLATE_BATCH_SIZE rows) landed and survives…
    for (let i = 0; i < TRANSLATE_BATCH_SIZE; i++) {
      expect(segs[i].text).toBe(`es:line ${i + 1}`);
    }
    // …the failed batch's rows keep their source text.
    expect(segs[TRANSLATE_BATCH_SIZE].text).toBe(`line ${TRANSLATE_BATCH_SIZE + 1}`);
    expect(useAppStore.getState().dubError).toMatch(/engine down/);
    expect(useAppStore.getState().isTranslating).toBe(false);
    expect(useAppStore.getState().visible).toBe(false); // pill dismissed
    // Only 2 requests went out — the loop stopped instead of hammering on.
    expect(dubApi.dubTranslate).toHaveBeenCalledTimes(2);
  });

  it('handleRetryFailedTranslations re-sends only the translate_error rows', async () => {
    useAppStore.setState({
      dubSegments: [
        { id: '1', text: 'es:uno', text_original: 'one', start: 0, end: 1 },
        { id: '2', text: 'two', text_original: 'two', start: 1, end: 2, translate_error: 'boom' },
        { id: '3', text: 'three', text_original: 'three', start: 2, end: 3, translate_error: 'boom' },
      ],
    });
    dubApi.dubTranslate.mockImplementation(async ({ segments }) => ({
      translated: segments.map((s) => ({ id: s.id, text: `es:${s.text}` })),
      target_lang: 'es',
    }));
    const { result } = renderWorkflow();
    let ok;
    await act(async () => {
      ok = await result.current.handleRetryFailedTranslations();
    });
    expect(ok).toBe(true);
    expect(dubApi.dubTranslate).toHaveBeenCalledTimes(1);
    const sent = dubApi.dubTranslate.mock.calls[0][0].segments;
    expect(sent.map((s) => s.id)).toEqual(['2', '3']);
    const segs = useAppStore.getState().dubSegments;
    expect(segs[0].text).toBe('es:uno'); // untouched
    expect(segs[1].text).toBe('es:two');
    expect(segs[1].translate_error).toBeUndefined();
    expect(segs[2].text).toBe('es:three');
  });

  it('resolves false with nothing to retry', async () => {
    useAppStore.setState({
      dubSegments: [{ id: '1', text: 'es:uno', text_original: 'one', start: 0, end: 1 }],
    });
    const { result } = renderWorkflow();
    let ok;
    await act(async () => {
      ok = await result.current.handleRetryFailedTranslations();
    });
    expect(ok).toBe(false);
    expect(dubApi.dubTranslate).not.toHaveBeenCalled();
  });
});

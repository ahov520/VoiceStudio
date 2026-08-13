import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '../i18n';

// VoiceSelector reads /archetypes — mock so the editor renders standalone
// (same setup as StoriesEditorVoicePicker.test.jsx).
vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));

import StoriesEditor from '../components/StoriesEditor';
import { useAppStore } from '../store';

// jsdom has no layout, so ResizeObserver reports 0 — stub the observer and a
// viewport size so the virtual list mounts rows.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const PROFILES = [{ id: 'p_clone', name: 'Aria' }];

function makeTracks(n) {
  return Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    character: 'narrator',
    text: `Line number ${i + 1} of the imported novel.`,
    profileId: null,
    emotion: null,
    speed: null,
    generating: false,
    audioUrl: null,
  }));
}

function seedStore(trackCount) {
  useAppStore.setState({
    cast: [{ id: 'narrator', name: 'Narrator', color: '#b8bb26', profileId: null }],
    storyTracks: makeTracks(trackCount),
    storyProjects: [],
    currentProjectId: null,
  });
}

function renderEditor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <StoriesEditor profiles={PROFILES} />
    </QueryClientProvider>,
  );
}

describe('StoriesEditor virtualization (imported-novel performance)', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1200);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(600);
  });

  it('small stories keep the plain list — every line stays mounted', () => {
    seedStore(20);
    renderEditor();
    expect(screen.getAllByRole('listitem')).toHaveLength(20);
  });

  it('a 2,000-line import mounts only the visible window of rows', () => {
    seedStore(2000);
    renderEditor();
    const rows = screen.getAllByRole('listitem');
    // 600px viewport / 64px rows ≈ 10 visible + overscan. The exact count is
    // react-window's business — the regression this pins is "not all 2,000".
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThan(120);
  });

  it('editing a line in the virtual list updates the store', () => {
    seedStore(500);
    renderEditor();
    const textareas = screen.getAllByRole('textbox', { name: /Narrator/ });
    fireEvent.change(textareas[0], { target: { value: 'Edited first line' } });
    expect(useAppStore.getState().storyTracks[0].text).toBe('Edited first line');
    // Untouched neighbours keep their object identity (memo-friendly updates).
    expect(useAppStore.getState().storyTracks[1].text).toBe(
      'Line number 2 of the imported novel.',
    );
  });
});

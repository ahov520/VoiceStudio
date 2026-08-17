import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import SearchableSelect from './SearchableSelect';

const RECENTS_KEY = 'omnivoice.recents.searchable-select-test';

afterEach(() => {
  localStorage.removeItem(RECENTS_KEY);
  vi.restoreAllMocks();
});

describe('SearchableSelect persisted recents', () => {
  it.each(['null', '{}', '42', '"voice-a"'])(
    'ignores a valid non-array JSON value: %s',
    (storedValue) => {
      localStorage.setItem(RECENTS_KEY, storedValue);

      render(
        <SearchableSelect
          value="voice-a"
          onChange={() => {}}
          options={[
            { value: 'voice-a', label: 'Voice A' },
            { value: 'voice-b', label: 'Voice B' },
          ]}
          recentsKey={RECENTS_KEY}
        />,
      );

      fireEvent.click(screen.getByRole('button'));
      expect(screen.getByRole('option', { name: 'Voice A' })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'Voice B' })).toBeInTheDocument();
    },
  );

  it('does not repeat pinned recent and popular options in the main list', () => {
    localStorage.setItem(RECENTS_KEY, JSON.stringify(['voice-a']));

    render(
      <SearchableSelect
        value="voice-c"
        onChange={() => {}}
        options={[
          { value: 'voice-a', label: 'Voice A' },
          { value: 'voice-b', label: 'Voice B' },
          { value: 'voice-c', label: 'Voice C' },
        ]}
        popular={['voice-b']}
        recentsKey={RECENTS_KEY}
      />,
    );

    fireEvent.click(screen.getByRole('button'));

    expect(screen.getAllByRole('option')).toHaveLength(3);
    expect(screen.getAllByRole('option', { name: 'Voice A' })).toHaveLength(1);
    expect(screen.getAllByRole('option', { name: 'Voice B' })).toHaveLength(1);
  });
});

describe('SearchableSelect portal layout', () => {
  it('cancels deferred menu focus when unmounted before the next tick', () => {
    vi.useFakeTimers();
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');
    const view = render(
      <SearchableSelect
        value="voice-a"
        options={[{ value: 'voice-a', label: 'Voice A' }]}
      />,
    );

    fireEvent.click(screen.getByRole('button'));
    view.unmount();
    vi.runAllTimers();

    expect(focus).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('keeps the portal menu at its minimum width for narrow triggers', () => {
    const rect = { width: 120, left: 20, right: 140, top: 40, bottom: 68 };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(rect);

    render(
      <SearchableSelect
        value="voice-a"
        options={[{ value: 'voice-a', label: 'Voice A' }]}
        menuPortal
      />,
    );
    fireEvent.click(screen.getByRole('button'));

    const menu = screen.getByRole('listbox');
    expect(menu).toHaveStyle({ width: '220px' });
  });

  it('clamps the highlighted option when async results shrink', () => {
    const onChange = vi.fn();
    const view = render(
      <SearchableSelect
        value="voice-a"
        onChange={onChange}
        options={[
          { value: 'voice-a', label: 'Voice A' },
          { value: 'voice-b', label: 'Voice B' },
          { value: 'voice-c', label: 'Voice C' },
        ]}
      />,
    );
    fireEvent.click(screen.getByRole('button'));
    const search = screen.getByRole('combobox');
    fireEvent.keyDown(search, { key: 'ArrowDown' });
    fireEvent.keyDown(search, { key: 'ArrowDown' });

    view.rerender(
      <SearchableSelect
        value="voice-a"
        onChange={onChange}
        options={[{ value: 'voice-a', label: 'Voice A' }]}
      />,
    );

    expect(search).toHaveAttribute('aria-activedescendant', expect.stringMatching(/option-0$/));
    fireEvent.keyDown(search, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('voice-a');
  });
});

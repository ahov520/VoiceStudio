import { beforeEach, describe, expect, it } from 'vitest';
import { discardPendingWrites } from '../utils/coalescedJsonStorage';
import { isChineseOsLanguage } from '../utils/isChineseOs';
import { APP_STORE_KEY, useAppStore } from './index';

const persist = async (state: object, version: number) => {
  // The test deliberately replaces durable storage with an old fixture. A
  // pending current-state write must not mask that fixture during rehydrate.
  discardPendingWrites((key) => key === APP_STORE_KEY);
  localStorage.setItem(APP_STORE_KEY, JSON.stringify({ state, version }));
  await useAppStore.persist.rehydrate();
};

describe('isChineseOsLanguage', () => {
  it.each(['zh', 'zh-CN', 'zh-TW', 'zh-cn', 'ZH-Hans'])(
    'matches Chinese language tags (%s)',
    (lang) => {
      expect(isChineseOsLanguage(lang)).toBe(true);
    },
  );

  it.each(['en', 'en-US', 'ja', 'ko', '', undefined, null])(
    'rejects non-Chinese tags (%s)',
    (lang) => {
      expect(isChineseOsLanguage(lang)).toBe(false);
    },
  );
});

describe('navStyle persistence migration (zh-UX pass 3)', () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ navStyle: 'rail' });
  });

  it('pins a pre-navStyle (v6) install to the icon rail on upgrade', async () => {
    // A v6 record has no navStyle field at all. Without the v8 pin it would
    // fall through to the locale-aware slice default and silently change the
    // navigation for an existing user on a Chinese OS.
    await persist({}, 6);
    expect(useAppStore.getState().navStyle).toBe('rail');
  });

  it('preserves an explicit navStyle choice through the v8 migration', async () => {
    await persist({ navStyle: 'tabs' }, 7);
    expect(useAppStore.getState().navStyle).toBe('tabs');
  });
});

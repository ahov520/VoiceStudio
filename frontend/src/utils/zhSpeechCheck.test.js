import { describe, it, expect } from 'vitest';
import {
  scanPolyphones,
  suggestNumberFixes,
  applyNumberFix,
  respellWord,
  hasChinese,
  POLYPHONES,
} from './zhSpeechCheck';

describe('scanPolyphones', () => {
  it('returns [] for empty / non-Chinese text', () => {
    expect(scanPolyphones('')).toEqual([]);
    expect(scanPolyphones('english only 123')).toEqual([]);
  });

  it('auto-resolves readings from collocations', () => {
    const rows = scanPolyphones('他去了重庆市，在银行门口等着，孩子慢慢长大了。');
    const by = Object.fromEntries(rows.map((r) => [r.char, r]));
    expect(by.重.word).toBe('重庆');
    expect(by.重.detected).toBe('chóng');
    expect(by.行.word).toBe('银行');
    expect(by.行.detected).toBe('háng');
    expect(by.长.word).toBe('长大');
    expect(by.长.detected).toBe('zhǎng');
  });

  it('uses a ±2-char window and null reading when the context is unknown', () => {
    const rows = scanPolyphones('这个人真行。');
    expect(rows).toHaveLength(1);
    expect(rows[0].char).toBe('行');
    expect(rows[0].detected).toBeNull();
    expect(rows[0].word).toContain('行');
    expect(rows[0].options.length).toBeGreaterThanOrEqual(2);
  });

  it('collapses repeated words into one row with a count', () => {
    const rows = scanPolyphones('去银行取钱，银行关门了。');
    const bank = rows.find((r) => r.word === '银行');
    expect(bank.count).toBe(2);
  });

  it('every reading option carries a common-char homophone respelling', () => {
    for (const readings of Object.values(POLYPHONES)) {
      for (const r of readings) {
        expect(r.pinyin).toBeTruthy();
        expect(r.respelling).toBeTruthy();
        expect(r.words.length).toBeGreaterThan(0);
      }
    }
  });
});

describe('suggestNumberFixes / applyNumberFix', () => {
  it('rewrites w/k units to 万/千', () => {
    const text = '已有10w粉丝，花了3.5k买设备';
    const fixes = suggestNumberFixes(text);
    expect(fixes.find((f) => f.kind === 'unit' && f.raw === '10w').fixed).toBe('10万');
    expect(fixes.find((f) => f.kind === 'unit' && f.raw === '3.5k').fixed).toBe('3.5千');
  });

  it('ignores w/k that are part of words or version strings', () => {
    expect(suggestNumberFixes('v2 web app')).toEqual([]);
    expect(suggestNumberFixes('kept in the dark')).toEqual([]);
  });

  it('groups an 11-digit phone 3-4-4 and other long runs in 4s', () => {
    const [phone] = suggestNumberFixes('电话13800138000说');
    expect(phone.fixed).toBe('138 0013 8000');
    const [id] = suggestNumberFixes('编号1234567890');
    expect(id.fixed).toBe('1234 5678 90');
  });

  it('leaves short digit groups alone', () => {
    expect(suggestNumberFixes('第2024章，卖了300块')).toEqual([]);
  });

  it('applyNumberFix splices the exact span', () => {
    const text = '已有10w粉丝';
    const fix = suggestNumberFixes(text)[0];
    expect(applyNumberFix(text, fix)).toBe('已有10万粉丝');
  });
});

describe('respellWord / hasChinese', () => {
  it('swaps every occurrence of the polyphone char for the homophone', () => {
    expect(respellWord('重庆', '重', { respelling: '崇' })).toBe('崇庆');
    expect(respellWord('慢慢地', '地', { respelling: '的' })).toBe('慢慢的');
  });

  it('hasChinese gates on any CJK char', () => {
    expect(hasChinese('重庆')).toBe(true);
    expect(hasChinese('abc 123 .,')).toBe(false);
    expect(hasChinese('')).toBe(false);
  });
});

/**
 * ZhSpeechCheck — Chinese speech pre-flight for the studio script (zh-UX pass 2).
 *
 * A compact affordance next to the script textarea (only when the text has
 * Chinese): polyphone words the engine may misread, each pickable to the
 * correct reading and saved as a pronunciation-dictionary entry (whole-word
 * homophone respell — services/pronunciation applies it on every synthesis),
 * plus one-click rewrites for number readings (w/k unit suffixes, 11-digit runs
 * grouped 3-4-4). Dictionary round-trip reuses the Settings → Pronunciation
 * API (GET/POST/PUT /pronunciation) — no new backend surface.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { Languages, Check as CheckIcon, X } from 'lucide-react';
import { apiJson, apiFetch } from '../../api/client';
import {
  scanPolyphones,
  suggestNumberFixes,
  applyNumberFix,
  respellWord,
  hasChinese,
} from '../../utils/zhSpeechCheck';

const PILL =
  'inline-flex items-center gap-[4px] px-2 py-1 text-[0.66rem] bg-[var(--chrome-bg)] border rounded-[var(--chrome-radius-pill)] cursor-pointer transition-[color,border-color] duration-[var(--dur-fast)] focus-visible:[outline:2px_solid_var(--chrome-accent)] focus-visible:[outline-offset:1px] text-[var(--chrome-fg-muted)] border-transparent hover:text-[var(--chrome-fg)] hover:border-transparent';
const PILL_ACTIVE = 'text-[var(--chrome-fg)] border-transparent';
const ROW_BTN =
  'px-[8px] py-[2px] text-[0.66rem] rounded-[var(--chrome-radius-pill)] border border-transparent cursor-pointer bg-[color-mix(in_srgb,var(--color-brand)_14%,transparent)] text-[var(--chrome-fg)] hover:bg-[color-mix(in_srgb,var(--color-brand)_24%,transparent)] disabled:opacity-50 disabled:cursor-default';

export default function ZhSpeechCheck({ t, text, setText }) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState(null); // { term -> id } for zh-scoped rows
  const [saved, setSaved] = useState({}); // term -> 'ok' | 'err'
  const [applied, setApplied] = useState({}); // raw number -> true
  const [picks, setPicks] = useState({}); // term -> chosen pinyin

  const rows = useMemo(() => (open ? scanPolyphones(text) : []), [open, text]);
  const numFixes = useMemo(() => (open ? suggestNumberFixes(text) : []), [open, text]);

  // Load the dictionary once per open so "already covered" marks are honest.
  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    apiJson('/pronunciation')
      .then((data) => {
        const list = Array.isArray(data) ? data : (data?.entries ?? []);
        const map = {};
        for (const e of list) {
          if ((e.language ?? '') === 'zh' || (e.language ?? '') === '*') {
            map[e.term] = e.id;
          }
        }
        if (alive) setEntries(map);
      })
      .catch(() => alive && setEntries({}));
    return () => {
      alive = false;
    };
  }, [open]);

  if (!hasChinese(text)) return null;

  const zhTerms = entries ?? {};
  const pending = rows.filter((r) => !zhTerms[r.word]);
  const todo = pending.length + numFixes.length;

  const saveRow = async (row) => {
    const chosen =
      row.options.find((o) => o.pinyin === (picks[row.word] ?? row.detected)) ?? row.options[0];
    const replacement = respellWord(row.word, row.char, chosen);
    try {
      const existingId = zhTerms[row.word];
      const res = existingId
        ? await apiFetch(`/pronunciation/${encodeURIComponent(existingId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ replacement }),
          })
        : await apiFetch('/pronunciation', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              term: row.word,
              replacement,
              type: 'respelling',
              language: 'zh',
              enabled: true,
            }),
          });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSaved((s) => ({ ...s, [row.word]: 'ok' }));
    } catch (err) {
      setSaved((s) => ({ ...s, [row.word]: 'err' }));
    }
  };

  return (
    <div className="relative">
      <button
        type="button"
        className={`${PILL} ${open ? PILL_ACTIVE : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        data-testid="zhcheck-toggle"
      >
        <Languages size={11} /> {t('zhcheck.open_btn')}{' '}
        {todo > 0 && <span className="tabular-nums text-[var(--chrome-fg)]">{todo}</span>}
      </button>
      {open && <div className="fixed inset-0 z-[19]" onClick={() => setOpen(false)} />}
      {open && (
        <div
          className="absolute right-0 top-[calc(100%+6px)] z-20 w-[min(400px,calc(100vw-16px))] p-[10px] bg-[var(--chrome-bg)] border border-transparent rounded-[10px] shadow-[0_8px_24px_rgba(0,0,0,0.45)]"
          role="dialog"
          aria-label={t('zhcheck.title')}
          data-testid="zhcheck-panel"
        >
          <div className="flex items-center gap-[6px] mb-[6px]">
            <span className="text-[0.72rem] font-semibold text-[var(--chrome-fg)] flex-1">
              {t('zhcheck.title')}
            </span>
            <button
              type="button"
              className="border-0 bg-transparent text-[var(--chrome-fg-muted)] cursor-pointer px-1 rounded-[4px] hover:text-[var(--chrome-fg)]"
              onClick={() => setOpen(false)}
              aria-label={t('common.close', { defaultValue: 'Close' })}
            >
              <X size={12} />
            </button>
          </div>

          <div className="text-[0.62rem] text-[var(--chrome-fg-muted)] mb-[8px]">
            {t('zhcheck.polyphone_hint')}
          </div>

          {rows.length === 0 && (
            <div className="text-[0.66rem] text-[var(--chrome-fg-muted)] mb-[8px]">
              {t('zhcheck.no_polyphones')}
            </div>
          )}
          {rows.map((row) => {
            const covered = !!zhTerms[row.word];
            const state = saved[row.word];
            return (
              <div
                key={`${row.char}|${row.word}`}
                className="flex items-center gap-[6px] py-[3px] text-[0.66rem]"
                data-testid="zhcheck-row"
              >
                <span className="text-[var(--chrome-fg)] min-w-0 truncate" title={row.word}>
                  {row.word}
                </span>
                {row.count > 1 && (
                  <span className="text-[var(--chrome-fg-dim)] tabular-nums">×{row.count}</span>
                )}
                <select
                  className="bg-[var(--chrome-bg)] border border-transparent rounded-[4px] bg-[var(--chrome-hover-bg)] text-[0.62rem] px-[4px] py-[1px] text-[var(--chrome-fg)] max-w-[110px]"
                  value={picks[row.word] ?? row.detected ?? row.options[0].pinyin}
                  onChange={(e) => setPicks((p) => ({ ...p, [row.word]: e.target.value }))}
                  aria-label={t('zhcheck.reading_for', { word: row.word })}
                >
                  {row.options.map((o) => (
                    <option key={o.pinyin} value={o.pinyin}>
                      {o.pinyin}
                    </option>
                  ))}
                </select>
                {covered || state === 'ok' ? (
                  <span className="inline-flex items-center gap-[3px] text-[var(--color-success)]">
                    <CheckIcon size={10} />
                    {t('zhcheck.covered')}
                  </span>
                ) : (
                  <button
                    type="button"
                    className={ROW_BTN}
                    onClick={() => void saveRow(row)}
                    data-testid={`zhcheck-save-${row.word}`}
                  >
                    {state === 'err' ? t('zhcheck.error') : t('zhcheck.save')}
                  </button>
                )}
              </div>
            );
          })}

          {numFixes.length > 0 && (
            <>
              <div className="text-[0.62rem] text-[var(--chrome-fg-muted)] mt-[8px] mb-[4px]">
                {t('zhcheck.numbers_section')}
              </div>
              {numFixes.map((f) => (
                <div
                  key={`${f.kind}-${f.index}`}
                  className="flex items-center gap-[6px] py-[3px] text-[0.66rem] font-[var(--chrome-font-mono)]"
                >
                  <span className="text-[var(--chrome-fg)]">{f.raw}</span>
                  <span className="text-[var(--chrome-fg-dim)]">→</span>
                  <span className="text-[var(--chrome-fg)]">{f.fixed}</span>
                  <span className="flex-1" />
                  {applied[f.raw] ? (
                    <span className="inline-flex items-center gap-[3px] text-[var(--color-success)] font-[var(--font-sans)]">
                      <CheckIcon size={10} />
                      {t('zhcheck.applied')}
                    </span>
                  ) : (
                    <button
                      type="button"
                      className={`${ROW_BTN} font-[var(--font-sans)]`}
                      onClick={() => {
                        setText(applyNumberFix(text, f));
                        setApplied((a) => ({ ...a, [f.raw]: true }));
                      }}
                      data-testid="zhcheck-apply-number"
                    >
                      {t('zhcheck.apply')}
                    </button>
                  )}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

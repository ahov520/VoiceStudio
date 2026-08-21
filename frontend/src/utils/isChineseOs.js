/**
 * Chinese-OS detection (zh-UX).
 *
 * Used to seed friendlier out-of-the-box defaults for mainstream Chinese
 * creators — the labeled titlebar-tab navigation instead of the icon-only
 * rail (uiSlice), and the 1.1× UI-scale floor (App.jsx). These only ever
 * seed a FIRST launch: zustand persist merges a persisted preference over
 * the slice default (and the v8 migration pins existing installs), so anyone
 * who already has a setting keeps it.
 */

/** Pure form — takes the language string so it's unit-testable. */
export function isChineseOsLanguage(lang) {
  return (lang || '').toLowerCase().startsWith('zh');
}

/** True when the OS language is Chinese (zh, zh-CN, zh-TW, …). */
export function isChineseOs() {
  return typeof navigator !== 'undefined' && isChineseOsLanguage(navigator.language);
}

// Shared browser-download helpers.
//
// Used by every "save this file" path that runs outside the Tauri desktop
// shell (browser dev mode + the Docker web-server build). In Tauri we use the
// native save dialog instead; calling that dialog when no Tauri runtime is
// present throws "Cannot read properties of undefined (reading 'invoke')"
// (issue #256), so callers must guard on isTauri and route here otherwise.
import { apiFetch } from '../api/client';

/**
 * Parse a download filename out of a Content-Disposition header.
 * Returns null when the header is absent or unparseable.
 */
export function parseFilenameFromContentDisposition(header) {
  if (!header) return null;
  const utf8 = header.match(/filename\*=(?:UTF-8|utf-8)''([^;]+)/i);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, ''));
    } catch {
      /* fall through to the plain match */
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1].trim() : null;
}

/** Make a server-provided filename safe and usable across desktop browsers. */
export function sanitizeDownloadFilename(filename, fallback = 'download') {
  const basename = String(filename ?? '').split(/[\\/]/).pop() ?? '';
  let sanitized = basename
    .replace(/[<>:"|?*\u0000-\u001f]/g, '_')
    .replace(/[. ]+$/g, '')
    .trim();
  // Windows rejects these device names even when an extension is present
  // (for example, `CON.wav`). Prefixing keeps the user's extension intact
  // while making the name valid on all supported desktop platforms.
  const stem = sanitized.split('.')[0]?.toUpperCase();
  if (/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/.test(stem)) sanitized = `_${sanitized}`;
  return sanitized && sanitized !== '.' && sanitized !== '..' ? sanitized : fallback;
}

/**
 * Fetch `url` and trigger a standard browser blob download via a temporary
 * <a download> element. Prefers the server-provided Content-Disposition
 * filename, falling back to `fallbackName`. Returns the filename used.
 *
 * `deps` lets tests inject fetch/document/url without a real DOM + network.
 * The default fetch is `apiFetch`, so backend downloads carry the LAN-share
 * PIN / remote API-key headers (a raw fetch would 401 under remote-backend).
 */
export async function browserDownload(url, fallbackName, deps = {}) {
  const _fetch = deps.fetch ?? apiFetch;
  const doc = deps.document ?? globalThis.document;
  const urlApi = deps.url ?? globalThis.URL;
  const schedule = deps.setTimeout ?? globalThis.setTimeout;

  const response = await _fetch(url);
  if (!response.ok) throw new Error('Download failed');

  const serverName = parseFilenameFromContentDisposition(
    response.headers?.get?.('content-disposition'),
  );
  const finalName = sanitizeDownloadFilename(serverName || fallbackName);

  const blob = await response.blob();
  const localUrl = urlApi.createObjectURL(blob);
  const a = doc.createElement('a');
  a.href = localUrl;
  a.download = finalName;
  let handedOff = false;
  try {
    doc.body.appendChild(a);
    a.click();
    // WebKit may not consume the blob until after the click handler returns.
    // Keep it alive briefly so browser/Docker downloads are reliable there.
    schedule(() => urlApi.revokeObjectURL(localUrl), 1000);
    handedOff = true;
  } finally {
    try {
      doc.body.removeChild(a);
    } catch {
      // Keep cleanup failures from masking the original download error.
    }
    // If DOM setup/click/scheduling fails, there is no browser download to
    // consume the URL, so release it immediately instead of leaking it.
    if (!handedOff) urlApi.revokeObjectURL(localUrl);
  }
  return finalName;
}

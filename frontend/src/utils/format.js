export function formatTime(s) {
  const m = Math.floor(s / 60);
  const sec = (s % 60).toFixed(1);
  return `${m}:${sec.padStart(4, '0')}`;
}

// Contract: settles with a number or null, NEVER rejects. null means
// "duration unknown — keep the file": callers (ingestRefAudio) have no
// try/catch and must still accept clips this webview can't decode, because
// the backend decodes them with ffmpeg (Tauri WebKit lacks several codecs).
// The timeout guards against media elements that never fire any event.
export async function probeAudioDuration(file) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const a = new Audio();
    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      // Drop the media element's reference as well as the Blob URL. This is
      // important in WebKit, where revoking alone can retain the decoded
      // resource until the element is collected.
      a.src = '';
      a.load?.();
      URL.revokeObjectURL(url);
    };
    const timeout = setTimeout(() => {
      cleanup();
      resolve(null);
    }, 10000);
    a.addEventListener(
      'loadedmetadata',
      () => {
        // Snapshot before cleanup clears the source. Some media elements reset
        // duration to NaN synchronously when load() is called with an empty src.
        const duration = a.duration;
        clearTimeout(timeout);
        cleanup();
        resolve(isFinite(duration) ? duration : null);
      },
      { once: true },
    );
    a.addEventListener(
      'error',
      () => {
        clearTimeout(timeout);
        cleanup();
        resolve(null);
      },
      { once: true },
    );
    a.src = url;
  });
}

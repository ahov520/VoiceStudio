"""Request/job paths must select ASR through ``load_active_asr_backend`` (#1512).

``get_active_asr_backend`` is a PURE selector: it constructs the backend
without ``ensure_loaded()``. An engine that passes the shallow
``is_available()`` probe but dies on first real use (a broken deep import
chain, #1185 — e.g. ctranslate2's native library refusing to load) therefore
reaches ``.transcribe()`` and 500s the request, even with a healthy engine
next in line. ``load_active_asr_backend`` is the loading selector that
records the broken engine and degrades to the next candidate.

Six call sites had drifted back to the pure selector after #1185 landed the
loader; this guard keeps a seventh from reappearing. The selector stays
private to ``services/asr_backend.py`` (the loader's own internals and the
best-effort ``transcribe_reference`` fast path live there).
"""
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]

# The selector's home module: its definition, the loader's internal call, and
# transcribe_reference's construct-only fast path.
_ALLOWED = {BACKEND_DIR / "services" / "asr_backend.py"}


def test_no_get_active_asr_backend_outside_its_home_module():
    offenders = []
    for path in sorted(BACKEND_DIR.rglob("*.py")):
        if path in _ALLOWED or "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "get_active_asr_backend" in line:
                offenders.append(f"{path.relative_to(BACKEND_DIR)}:{lineno}")
    assert not offenders, (
        "Select ASR with load_active_asr_backend() — eager ensure_loaded(), "
        "degrading past engines whose deep import chain is broken (#1512) — "
        "not the pure selector get_active_asr_backend(), which lets a broken "
        "engine reach .transcribe() and 500 the request: "
        + ", ".join(offenders)
    )

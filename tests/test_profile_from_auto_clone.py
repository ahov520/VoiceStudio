"""POST /profiles/from-auto-clone — promote a dub auto-clone to a profile.

Auto-extracted speaker clones are job-scoped (they live under the dub job's
directory and vanish with it). The endpoint copies the reference WAV into
VOICES_DIR and inserts the same kind='clone' row POST /profiles produces.

Runs against an isolated tmp data dir with ONLY the profiles router mounted
(pattern mirrors tests/test_profile_unification.py, minus `main` — the full
app pulls optional heavy deps that a minimal test env may lack); the dub job
is faked by monkeypatching services.dub_pipeline — no ASR/diarization stack
needed.
"""

import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_FAKE_WAV = b"RIFF" + b"\x00" * 2000


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    """TestClient over the profiles router only, with an isolated data dir."""
    mp = pytest.MonkeyPatch()
    tmp_path = tmp_path_factory.mktemp("auto-clone-profiles-data")
    mp.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))

    import importlib
    import core.config as _cfg
    importlib.reload(_cfg)
    import core.db as _db
    importlib.reload(_db)
    from api.routers import profiles as _profiles
    importlib.reload(_profiles)

    _db.init_db()

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(_profiles.router)
    try:
        yield TestClient(app), _cfg, _profiles
    finally:
        mp.undo()


@pytest.fixture()
def dub_job(tmp_path, monkeypatch):
    """A fake dub job with one per-speaker clone + one segment-only speaker."""
    job_dir = tmp_path / "dub" / "job-1"
    job_dir.mkdir(parents=True)
    ref = job_dir / "voice_speaker_1.wav"
    ref.write_bytes(_FAKE_WAV)
    seg_ref = job_dir / "seg_ref_seg_3.wav"
    seg_ref.write_bytes(_FAKE_WAV)
    job = {
        "source_lang": "en",
        "speaker_clones": {
            "Speaker 1": {
                "ref_audio": str(ref),
                "ref_text": "hello world",
                "duration": 7.5,
                "source_count": 2,
            },
        },
        "segments": [
            {"id": "seg_3", "speaker_id": "Speaker 2", "start": 0.0, "end": 4.0},
        ],
        "segment_clones": {
            "seg_3": {"ref_audio": str(seg_ref), "ref_text": "a line", "duration": 4.0},
        },
    }
    from services import dub_pipeline
    monkeypatch.setattr(dub_pipeline, "get_job", lambda job_id: job if job_id == "job-1" else None)
    monkeypatch.setattr(
        dub_pipeline,
        "safe_job_dir",
        lambda job_id: str(job_dir) if job_id == "job-1" else None,
    )
    return job, job_dir


def test_promotes_speaker_clone(app_client, dub_job):
    client, cfg, _ = app_client
    r = client.post(
        "/profiles/from-auto-clone",
        json={"job_id": "job-1", "speaker_id": "Speaker 1", "name": "Narrator"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Narrator"
    assert body["kind"] == "clone"

    row = client.get(f"/profiles/{body['id']}").json()
    assert row["name"] == "Narrator"
    assert row["ref_text"] == "hello world"
    assert row["language"] == "en"
    assert row["kind"] == "clone"
    # The reference was copied into VOICES_DIR under the profile id.
    copied = os.path.join(cfg.VOICES_DIR, f"{body['id']}.wav")
    assert os.path.isfile(copied)
    assert open(copied, "rb").read() == _FAKE_WAV


def test_accepts_ui_auto_id_form(app_client, dub_job):
    """The dub editor's selects carry `auto:<slug>`, not the raw label."""
    client, _, _ = app_client
    r = client.post(
        "/profiles/from-auto-clone",
        json={"job_id": "job-1", "speaker_id": "auto:speaker_1"},
    )
    assert r.status_code == 200, r.text
    # No name given → defaults to the requested speaker label.
    assert r.json()["name"] == "auto:speaker_1"


def test_segment_only_speaker_falls_back_to_longest_segment_ref(app_client, dub_job):
    """Speakers with no pooled clone (untrusted diarization) still promote —
    from their best per-segment reference, the same source build_cast_sources
    surfaces in the cast UI."""
    client, _, _ = app_client
    r = client.post(
        "/profiles/from-auto-clone",
        json={"job_id": "job-1", "speaker_id": "Speaker 2", "name": "Guest"},
    )
    assert r.status_code == 200, r.text
    row = client.get(f"/profiles/{r.json()['id']}").json()
    assert row["ref_text"] == "a line"


def test_unknown_job_is_404(app_client, dub_job):
    client, _, _ = app_client
    r = client.post(
        "/profiles/from-auto-clone",
        json={"job_id": "nope", "speaker_id": "Speaker 1"},
    )
    assert r.status_code == 404


def test_speaker_without_clone_is_404(app_client, dub_job):
    client, _, _ = app_client
    r = client.post(
        "/profiles/from-auto-clone",
        json={"job_id": "job-1", "speaker_id": "Speaker 9"},
    )
    assert r.status_code == 404


def test_ref_path_outside_job_dir_is_rejected(app_client, dub_job, tmp_path, monkeypatch):
    """A persisted job blob pointing outside its own directory must not be
    read — same containment rule as every other dub route."""
    job, _ = dub_job
    outside = tmp_path / "elsewhere.wav"
    outside.write_bytes(_FAKE_WAV)
    job["speaker_clones"]["Speaker 1"]["ref_audio"] = str(outside)
    client, _, _ = app_client
    r = client.post(
        "/profiles/from-auto-clone",
        json={"job_id": "job-1", "speaker_id": "Speaker 1"},
    )
    assert r.status_code == 400

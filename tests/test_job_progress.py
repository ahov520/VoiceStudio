import json


def test_progress_is_persisted(monkeypatch, tmp_path):
    from core import db, job_store

    db_path = tmp_path / "progress.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    job_store.create("j1", type="test")
    job_store.update_progress("j1", 1.5, "generating")
    row = job_store.get("j1")
    assert row["progress"] == 1.0
    assert row["stage"] == "generating"


def test_non_finite_progress_is_ignored(monkeypatch, tmp_path):
    from core import db, job_store

    db_path = tmp_path / "progress-finite.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    job_store.create("j1", type="test")
    job_store.update_progress("j1", 0.25, "starting")
    for value in (float("nan"), float("inf"), float("-inf")):
        job_store.update_progress("j1", value, "corrupt")
    row = job_store.get("j1")
    assert row["progress"] == 0.25
    assert row["stage"] == "starting"


def test_progress_without_stage_preserves_last_stage(monkeypatch, tmp_path):
    from core import db, job_store

    db_path = tmp_path / "progress-stage.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    job_store.create("j1", type="test")
    job_store.update_progress("j1", 0.25, "encoding")
    job_store.update_progress("j1", 0.5)
    row = job_store.get("j1")
    assert row["progress"] == 0.5
    assert row["stage"] == "encoding"


def test_late_progress_cannot_overwrite_terminal_state(monkeypatch, tmp_path):
    from core import db, job_store

    db_path = tmp_path / "progress-terminal.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    job_store.create("j1", type="test")
    job_store.mark_done("j1")
    job_store.update_progress("j1", 0.25, "late-worker-event")
    row = job_store.get("j1")
    assert row["status"] == "done"
    assert row["progress"] == 1.0
    assert row["stage"] == "done"


def test_failed_and_cancelled_jobs_expose_terminal_stage(monkeypatch, tmp_path):
    from core import db, job_store

    db_path = tmp_path / "progress-terminal-stages.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()

    job_store.create("failed", type="test")
    job_store.update_progress("failed", 0.5, "encoding")
    job_store.mark_failed("failed", "encoder exited")
    assert job_store.get("failed")["stage"] == "failed"

    job_store.create("cancelled", type="test")
    job_store.update_progress("cancelled", 0.5, "encoding")
    job_store.mark_cancelled("cancelled")
    assert job_store.get("cancelled")["stage"] == "cancelled"


def test_task_progress_event_parser(monkeypatch):
    from core.tasks import TaskManager
    seen = {}
    monkeypatch.setattr("core.tasks.job_store.update_progress", lambda *args: seen.update(progress=args[1], stage=args[2]))
    TaskManager._persist_progress_event("j1", "data: " + json.dumps({"type": "progress", "percent": 25, "stage": "encoding"}) + "\n\n")
    assert seen == {"progress": 0.25, "stage": "encoding"}

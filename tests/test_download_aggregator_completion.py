from utils import download_aggregator as da


def test_completion_ignores_late_progress_callbacks():
    agg = da.start("repo", total_bytes=100, files_total=1)
    agg.update_byte_bar("file", 20, 100)
    da.complete("repo")

    da.feed("repo", "file", "B", 40, 100, False)
    da.add_bytes("repo", "segment", 10)
    da.feed("repo", "files", "it", 0, 1, False)
    da.feed("repo", "file", "B", 100, 100, True)

    snapshot = agg.snapshot()
    assert snapshot["bytes_done"] == 100
    assert snapshot["files_done"] == 1


def test_completion_is_idempotent(monkeypatch):
    agg = da.start("repo", total_bytes=100, files_total=1)
    emitted = []
    monkeypatch.setattr(da.hf_progress, "emit", emitted.append)

    da.complete("repo")
    da.complete("repo")

    assert len(emitted) == 1
    assert emitted[0]["bytes_done"] == 100
    assert agg.snapshot()["bytes_done"] == 100

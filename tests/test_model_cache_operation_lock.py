from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.routers.setup import download


@pytest.fixture(autouse=True)
def _clear_operations():
    download._active_installs.clear()
    download._active_deletes.clear()
    yield
    download._active_installs.clear()
    download._active_deletes.clear()


def test_delete_rejects_concurrent_install(monkeypatch):
    download._active_installs.add("org/model")
    monkeypatch.setattr(
        download.hf_progress,
        "emit",
        lambda _event: pytest.fail("a rejected delete must not emit progress"),
    )

    with pytest.raises(HTTPException) as exc:
        download.delete_model("org/model")

    assert exc.value.status_code == 409


def test_delete_operation_is_released_after_failure(monkeypatch):
    import huggingface_hub

    def fail_scan():
        assert "org/model" in download._active_deletes
        raise OSError("cache unavailable")

    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", fail_scan)
    monkeypatch.setattr(download.hf_progress, "emit", lambda _event: None)

    with pytest.raises(HTTPException) as exc:
        download.delete_model("org/model")

    assert exc.value.status_code == 500
    assert "org/model" not in download._active_deletes

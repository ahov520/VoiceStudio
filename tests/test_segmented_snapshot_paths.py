"""Cache-path validation for metadata returned by HF endpoints and mirrors."""

import pytest

from api.routers.setup.download import _safe_cache_component, _safe_repo_file


@pytest.mark.parametrize(
    "value",
    ["../outside.bin", "weights/../../outside.bin", "/absolute.bin", r"..\outside.bin", "C:/outside.bin"],
)
def test_repo_file_rejects_paths_outside_snapshot(value):
    with pytest.raises(ValueError, match="unsafe filename"):
        _safe_repo_file(value)


@pytest.mark.parametrize("value", ["../commit", "dir/blob", r"dir\blob", "C:blob", ""])
def test_cache_component_rejects_nested_or_platform_absolute_values(value):
    with pytest.raises(ValueError, match="unsafe"):
        _safe_cache_component(value, label="metadata")


def test_repo_file_accepts_nested_portable_path():
    assert _safe_repo_file("models/encoder/model.safetensors") == "models/encoder/model.safetensors"


def test_cache_component_accepts_hf_hashes():
    assert _safe_cache_component("012345abcdef", label="ETag") == "012345abcdef"

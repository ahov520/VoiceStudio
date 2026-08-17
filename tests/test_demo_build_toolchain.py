import importlib.util
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_omnivoice_build_does_not_fall_through_to_macos_say(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(ROOT / "scripts" / "build_demos.sh", scripts / "build_demos.sh")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python3"
    fake_python.write_text("#!/bin/sh\nexit 0\n")
    fake_python.chmod(0o755)

    result = subprocess.run(
        ["bash", str(scripts / "build_demos.sh"), "--engine", "omnivoice"],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "'say' not found" not in result.stderr


def test_omnivoice_renderer_updates_main_demo_manifest(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "render_demos_omnivoice", ROOT / "scripts" / "render_demos_omnivoice.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    samples = tmp_path / "samples"
    manifest = samples / "demo" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"version": "test"}))
    dubbing_manifest = samples / "demo" / "dubbing" / "manifest.json"
    dubbing_manifest.parent.mkdir()
    dubbing_manifest.write_text(json.dumps({"rendered_by": "dubbing"}))

    monkeypatch.setattr(module, "SAMPLES_DIR", samples)
    monkeypatch.setattr(module, "_git_sha", lambda: "abc1234")
    module.update_manifest(None)

    assert json.loads(manifest.read_text())["rendered_by"] == "omnivoice@abc1234"
    assert json.loads(dubbing_manifest.read_text()) == {"rendered_by": "dubbing"}

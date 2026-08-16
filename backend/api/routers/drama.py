"""Drama Director API - AI role assignment + emotion annotation for audiobook dramas.

Endpoints:
  POST /drama/parse          - script -> {cast, lines, script_text, voice_map}
  POST /drama/projects       - save a .ovsdrama project (file-based, local-first)
  GET  /drama/projects       - list saved projects
  GET  /drama/projects/{id}  - load one project
  DELETE /drama/projects/{id}- remove a project

The parse endpoint is the director: it assigns roles (cast), per-line emotion
(语气腔调) and compiles an audiobook-ready script with [voice:NAME] + SSML-lite
markers. Projects are plain JSON files under DATA_DIR/drama/ - no DB migration.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.config import DATA_DIR
from core.path_security import safe_filename
from services import drama_director
from services.drama_director import (
    build_audiobook_script,
    parse_script_director,
    suggest_cast_voices,
    voice_map_for,
)

router = APIRouter(prefix="/drama", tags=["Drama Director"])
logger = logging.getLogger("omnivoice.api")

_DRAMA_DIR = os.path.join(DATA_DIR, "drama")
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_MAX_SCRIPT_CHARS = 200_000


def _drama_dir() -> str:
    os.makedirs(_DRAMA_DIR, exist_ok=True)
    return _DRAMA_DIR


def _project_path(project_id: str) -> str:
    if not _ID_RE.match(project_id):
        raise HTTPException(status_code=400, detail="Invalid project id.")
    return os.path.join(_drama_dir(), f"{project_id}.ovsdrama")


class DramaParseRequest(BaseModel):
    script: str = Field(..., max_length=_MAX_SCRIPT_CHARS)
    #: Optional local voice profiles [{id,name,kind,...}] used for role matching.
    profiles: list[dict] = Field(default_factory=list)


class DramaProjectSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    script: str = Field(..., max_length=_MAX_SCRIPT_CHARS)
    cast: list[dict] = Field(default_factory=list)
    lines: list[dict] = Field(default_factory=list)


@router.post("/parse")
async def drama_parse(req: DramaParseRequest) -> dict:
    """Run the director: parse roles, annotate emotion, compile the script."""
    text = (req.script or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Script is empty.")
    parsed = await _run_parse(text)
    cast = suggest_cast_voices(parsed["cast"], req.profiles or None)
    lines = parsed["lines"]
    return {
        "cast": cast,
        "lines": lines,
        "script_text": build_audiobook_script(cast, lines),
        "voice_map": voice_map_for(cast),
    }


async def _run_parse(text: str) -> dict:
    """parse_script_director is sync + potentially slow (LLM); run it off-loop."""
    import asyncio
    return await asyncio.to_thread(parse_script_director, text)


@router.post("/projects")
async def drama_project_save(req: DramaProjectSaveRequest) -> dict:
    """Persist a drama project as DATA_DIR/drama/<id>.ovsdrama."""
    project_id = uuid.uuid4().hex[:12]
    name = safe_filename(req.name)
    payload = {
        "id": project_id,
        "name": name,
        "script": req.script,
        "cast": req.cast,
        "lines": req.lines,
    }
    path = _project_path(project_id)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return {"id": project_id, "name": name}


@router.get("/projects")
async def drama_project_list() -> dict:
    """List saved projects (id + name + line count), newest first."""
    entries = []
    for fn in sorted(os.listdir(_drama_dir()), reverse=True):
        if not fn.endswith(".ovsdrama"):
            continue
        path = os.path.join(_drama_dir(), fn)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            entries.append({
                "id": data.get("id", fn[:-len(".ovsdrama")]),
                "name": data.get("name") or fn,
                "line_count": len(data.get("lines") or []),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return {"projects": entries}


@router.get("/projects/{project_id}")
async def drama_project_get(project_id: str) -> dict:
    path = _project_path(project_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Project not found.")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@router.delete("/projects/{project_id}")
async def drama_project_delete(project_id: str) -> dict:
    path = _project_path(project_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Project not found.")
    os.remove(path)
    return {"ok": True}

"""로컬 저장소: CLI 로 받은 원본(악기·악보)과 사용자가 만든 재생목록·폴더. 전부 data/*.json, UTF-8, 원자적 쓰기."""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

# exe(PyInstaller onefile) 로 묶였을 땐 exe 옆에, 소스로 돌릴 땐 이 파일 옆에 data/ 를 둔다
_BASE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_BASE, "data")
FIXTURE_DIR = os.path.join(_BASE, "fixtures")


def _path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def load(name: str, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name: str, obj) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(name))


def save_fixture(command: str, body) -> None:
    """CLI 응답 원본을 fixtures/<command>.json 으로도 남긴다 (오프라인 개발·회귀용)."""
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    with open(os.path.join(FIXTURE_DIR, f"{command}.json"), "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=1)


# ── 수신 캐시 ──
def get_cache(kind: str) -> dict:
    """kind = 'scores' | 'instruments' → {"fetched_at": ts, "items": [...]}"""
    return load(f"{kind}.json", {"fetched_at": None, "items": []})


def set_cache(kind: str, items: list) -> dict:
    d = {"fetched_at": time.time(), "items": items}
    save(f"{kind}.json", d)
    return d


# ── 재생목록·폴더 ──
def get_lists() -> dict:
    d = load("playlists.json", None)
    if not d:
        d = {"folders": [], "playlists": []}
    d.setdefault("folders", [])
    d.setdefault("playlists", [])
    return d


def set_lists(d: dict) -> None:
    save("playlists.json", d)


def new_id() -> str:
    return uuid.uuid4().hex[:10]


# ── 아티스트 사전·수동 지정 ──
def get_artists() -> dict:
    """{"artists":[{id,name,aliases[]}], "assign":{원본제목: artistId}, "noise":[추가 잡음어]}"""
    d = load("artists.json", None) or {}
    d.setdefault("artists", [])
    d.setdefault("assign", {})
    d.setdefault("noise", [])
    return d


def set_artists(d: dict) -> None:
    save("artists.json", d)

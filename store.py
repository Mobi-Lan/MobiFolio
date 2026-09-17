"""로컬 저장소: CLI 로 받은 원본(악기·악보)과 사용자가 만든 재생목록·폴더. 전부 data/*.json, UTF-8, 원자적 쓰기."""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

# 데이터 위치: MABI_DATA_DIR(일렉트론이 앱 폴더를 넘김) > exe 옆(PyInstaller) > 이 파일 옆(소스 실행)
_BASE = os.environ.get("MABI_DATA_DIR") or (os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__)))
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


# ── 설정 ──
DEFAULT_SETTINGS = {
    "cli_exe": "",            # 비우면 자동 탐색 (C:\Nexon\MabinogiMobile\MabinogiMobile_CLI.exe → PATH)
    "gap_sec": 2,             # 곡 사이 대기(초)
    "advance_margin": 2.0,    # 곡 끝 몇 초 전에 정지하고 다음 곡으로 (연주가 반복 설정이라 스스로 안 끝날 수 있음)
    "default_inst": "",       # 기본 악기 ("" = 악기 그대로)
    "auto_sync": True,        # 시작 시 CLI 연결돼 있으면 자동 갱신
    "stop_before_play": True, # 재생 전 현재 연주를 먼저 정지
}


def get_settings() -> dict:
    d = dict(DEFAULT_SETTINGS)
    d.update({k: v for k, v in (load("settings.json", {}) or {}).items() if k in DEFAULT_SETTINGS})
    return d


def set_settings(patch: dict) -> dict:
    d = get_settings()
    for k, v in (patch or {}).items():
        if k in DEFAULT_SETTINGS:
            d[k] = v
    save("settings.json", d)
    return d


# ── 최근 재생 · 곡 길이 캐시 ──
def get_recent() -> list:
    return load("recent.json", [])


def push_recent(title: str, inst: str = "") -> None:
    r = [x for x in get_recent() if x.get("title") != title]
    r.insert(0, {"title": title, "inst": inst, "ts": time.time()})
    save("recent.json", r[:100])


def get_durations() -> dict:
    return load("durations.json", {})


def set_duration(title: str, seconds: float) -> None:
    d = get_durations()
    if title and seconds and seconds > 0 and abs(d.get(title, 0) - seconds) > 0.5:
        d[title] = round(float(seconds), 2)
        save("durations.json", d)


# ── 재생목록·폴더 ──
def _norm_item(x) -> dict:
    """항목 = {"title": DisplayTitle, "inst": 악기명|""}. 예전 문자열 항목도 받아준다."""
    if isinstance(x, str):
        return {"title": x, "inst": ""}
    return {"title": str(x.get("title", "")), "inst": str(x.get("inst", "") or "")}


def get_lists() -> dict:
    d = load("playlists.json", None)
    if not d:
        d = {"folders": [], "playlists": []}
    d.setdefault("folders", [])
    d.setdefault("playlists", [])
    for pl in d["playlists"]:
        pl["items"] = [_norm_item(x) for x in pl.get("items", []) if _norm_item(x)["title"]]
        pl.setdefault("memo", "")
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

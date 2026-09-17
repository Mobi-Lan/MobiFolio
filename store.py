"""로컬 저장소: CLI 로 받은 원본(악기·악보)과 사용자가 만든 재생목록·폴더. 전부 data/*.json, UTF-8, 원자적 쓰기.

QA 반영:
- 모든 읽기·쓰기·읽고-고쳐-쓰기는 모듈 전역 RLock 아래 (ThreadingHTTPServer 가 핸들러를 동시에 돌린다).
- 임시 파일명은 스레드별로 유일, os.replace 는 공유 위반(WinError 32/5) 시 짧게 재시도.
- 못 읽는 파일(잘림·BOM·권한)은 <name>.corrupt-<ts> 로 옮겨 두고 기본값을 쓴다 → 다음 저장이 원본을 영구 덮어쓰지 않는다.
- 모양이 틀린 JSON(dict 자리에 list 등)은 기본값으로.
- 설정은 키별 타입·범위 강제.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid

# 데이터 위치: MABI_DATA_DIR(개발·테스트용 강제) > 사용자 폴더 %LOCALAPPDATA%\MobiFolio (배포판·개발 실행이 같은 저장소를 쓴다)
def user_base() -> str:
    env = os.environ.get("MABI_DATA_DIR")
    if env and (not getattr(sys, "frozen", False) or os.environ.get("MABI_DEV") == "1"):   # 배포판은 개발용 환경변수 무시
        return env
    la = os.environ.get("LOCALAPPDATA")
    if la:
        return os.path.join(la, "MobiFolio")
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))


_BASE = user_base()
DATA_DIR = os.path.join(_BASE, "data")
FIXTURE_DIR = os.path.join(_BASE, "fixtures")
LOCK = threading.RLock()
_DATA_FILES = ("scores.json", "instruments.json", "settings.json", "recent.json", "durations.json", "cli_log.json", "playlists.json", "artists.json")


def _read_json_file(path: str):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def _merge(name: str, docs: list) -> object:
    """같은 파일의 여러 판본을 하나로. docs 는 (mtime, 내용) — 최신이 앞."""
    docs = [d for _, d in sorted(docs, key=lambda x: -x[0])]
    if not docs:
        return None
    if name == "artists.json":     # 아티스트·지정·잡음어 합집합 (같은 id 는 최신 우선, 별칭은 합침)
        out = {"artists": [], "assign": {}, "noise": []}
        seen: dict[str, dict] = {}
        for d in docs:
            if not isinstance(d, dict):
                continue
            for a in d.get("artists") or []:
                if isinstance(a, dict) and isinstance(a.get("id"), str):
                    if a["id"] in seen:
                        seen[a["id"]]["aliases"] = list(dict.fromkeys(seen[a["id"]].get("aliases", []) + list(a.get("aliases") or [])))
                    else:
                        seen[a["id"]] = dict(a); out["artists"].append(seen[a["id"]])
            for t, v in (d.get("assign") or {}).items():
                out["assign"].setdefault(t, v)
            for w in d.get("noise") or []:
                if w not in out["noise"]:
                    out["noise"].append(w)
        return out
    if name == "durations.json":   # 합집합 (최신 우선)
        out = {}
        for d in docs:
            if isinstance(d, dict):
                for k, v in d.items():
                    out.setdefault(k, v)
        return out
    if name == "recent.json":      # 제목별 최신 ts
        best: dict[str, dict] = {}
        for d in docs:
            for x in (d if isinstance(d, list) else []):
                if isinstance(x, dict) and isinstance(x.get("title"), str):
                    if x["title"] not in best or (x.get("ts") or 0) > (best[x["title"]].get("ts") or 0):
                        best[x["title"]] = x
        return sorted(best.values(), key=lambda x: -(x.get("ts") or 0))[:100]
    if name == "playlists.json":   # 폴더·재생목록 id 합집합
        out = {"folders": [], "playlists": []}
        seen_f: set = set(); seen_p: set = set()
        for d in docs:
            if not isinstance(d, dict):
                continue
            for f in d.get("folders") or []:
                if isinstance(f, dict) and f.get("id") not in seen_f:
                    seen_f.add(f.get("id")); out["folders"].append(f)
            for pl in d.get("playlists") or []:
                if isinstance(pl, dict) and pl.get("id") not in seen_p:
                    seen_p.add(pl.get("id")); out["playlists"].append(pl)
        return out
    return docs[0]                 # settings/scores/instruments/cli_log: 최신 판본


def migrate_legacy(candidates: list) -> list:
    """예전 위치(exe 옆 data/, 프로젝트 data/)의 파일을 한 번만 사용자 폴더로 옮긴다.
    새 위치에 data/ 가 아직 없을 때만 실행. 두 저장소가 갈라져 있던 경우 파일 종류별로 병합한다."""
    if _BASE == os.environ.get("MABI_DATA_DIR") or os.path.isfile(_path("migrated.json")):   # 개발용 강제 위치면 이전하지 않는다
        return []
    # 새 위치에 '사용자 데이터'(설정·최근·길이·재생목록·아티스트)가 이미 있으면 건드리지 않는다. 캐시(scores/instruments/cli_log)만 있으면 이전 진행
    if any(os.path.isfile(_path(n)) for n in ("settings.json", "recent.json", "durations.json", "playlists.json", "artists.json")):
        return []
    found: dict[str, list] = {}
    srcs: list[str] = []
    for base in candidates:
        if not base:
            continue
        d = os.path.join(os.path.abspath(base), "data")
        if os.path.abspath(d) == os.path.abspath(DATA_DIR) or not os.path.isdir(d):
            continue
        for name in _DATA_FILES:
            src = os.path.join(d, name)
            if os.path.isfile(src):
                doc = _read_json_file(src)
                if doc is not None:
                    found.setdefault(name, []).append((os.path.getmtime(src), doc)); srcs.append(src)
    if not found:
        return []
    os.makedirs(DATA_DIR, exist_ok=True)
    for name, docs in found.items():
        merged = _merge(name, docs)
        if merged is not None:
            save(name, merged)
    save("migrated.json", {"at": time.time(), "from": srcs})
    print(f"[store] 예전 데이터 {len(srcs)}개 파일을 병합해 {DATA_DIR} 로 옮겼습니다", flush=True)
    return srcs


def _path(name: str) -> str:
    return os.path.join(DATA_DIR, name)


def load(name: str, default):
    """파일이 없으면 default. 있는데 못 읽으면 .corrupt-<ts> 로 격리하고 default (원본 보존)."""
    p = _path(name)
    with LOCK:
        if not os.path.exists(p):
            return default
        try:
            with open(p, encoding="utf-8-sig") as f:   # 메모장이 BOM 을 붙여도 읽힌다
                return json.load(f)
        except Exception as e:
            bad = f"{p}.corrupt-{int(time.time())}"
            try:
                os.replace(p, bad)
                print(f"[store] {name} 을 읽지 못해 {os.path.basename(bad)} 로 옮겼습니다: {e}", flush=True)
            except OSError:
                pass
            return default


def _load_dict(name: str) -> dict:
    d = load(name, {})
    return d if isinstance(d, dict) else {}


def save(name: str, obj) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = _path(f"{name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        last: Exception | None = None
        for i in range(6):   # 백신·백업 도구가 잠깐 잡고 있을 때 (WinError 32/5)
            try:
                os.replace(tmp, _path(name))
                return
            except PermissionError as e:
                last = e
                time.sleep(0.05 * (i + 1))
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise last if last else OSError(f"save failed: {name}")


def save_fixture(command: str, body) -> None:
    """CLI 응답 원본을 fixtures/<command>.json 으로도 남긴다 (오프라인 개발·회귀용). 실패해도 동기화는 막지 않는다."""
    try:
        os.makedirs(FIXTURE_DIR, exist_ok=True)
        tmp = os.path.join(FIXTURE_DIR, f"{command}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(FIXTURE_DIR, f"{command}.json"))
    except Exception as e:
        print(f"[store] fixture 저장 실패 {command}: {e}", flush=True)


# ── 수신 캐시 ──
def get_cache(kind: str) -> dict:
    """kind = 'scores' | 'instruments' → {"fetched_at": ts, "items": [...]}"""
    d = _load_dict(f"{kind}.json")
    items = d.get("items")
    return {"fetched_at": d.get("fetched_at"), "items": items if isinstance(items, list) else []}


def set_cache(kind: str, items: list) -> dict:
    d = {"fetched_at": time.time(), "items": list(items or [])}
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
_RANGES = {"gap_sec": (0, 60), "advance_margin": (0, 30)}


def _coerce(k: str, v):
    """키별 타입 강제. 이상하면 None(→ 기본값 유지)."""
    d = DEFAULT_SETTINGS[k]
    if isinstance(d, bool):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return None
    if isinstance(d, (int, float)):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        if x != x:   # NaN
            return None
        lo, hi = _RANGES.get(k, (-1e9, 1e9))
        x = min(max(x, lo), hi)
        return int(x) if isinstance(d, int) and x == int(x) else x
    if isinstance(d, str):
        return v.strip() if isinstance(v, str) else None
    return None


def get_settings() -> dict:
    d = dict(DEFAULT_SETTINGS)
    for k, v in _load_dict("settings.json").items():
        if k in DEFAULT_SETTINGS:
            c = _coerce(k, v)
            if c is not None:
                d[k] = c
    return d


def set_settings(patch: dict) -> dict:
    with LOCK:
        d = get_settings()
        for k, v in (patch or {}).items() if isinstance(patch, dict) else []:
            if k in DEFAULT_SETTINGS:
                c = _coerce(k, v)
                if c is not None:
                    d[k] = c
        save("settings.json", d)
        return d


# ── 최근 재생 · 곡 길이 캐시 ──
def get_recent() -> list:
    r = load("recent.json", [])
    return [x for x in r if isinstance(x, dict) and isinstance(x.get("title"), str)] if isinstance(r, list) else []


def push_recent(title: str, inst: str = "") -> None:
    with LOCK:
        r = [x for x in get_recent() if x.get("title") != title]
        r.insert(0, {"title": title, "inst": inst or "", "ts": time.time()})
        save("recent.json", r[:100])


def get_durations() -> dict:
    out = {}
    for k, v in _load_dict("durations.json").items():
        try:
            f = float(v)
            if f > 0:
                out[k] = f
        except (TypeError, ValueError):
            continue
    return out


def set_duration(title: str, seconds) -> None:
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return
    if not title or not (s > 0):
        return
    with LOCK:
        d = get_durations()
        if abs(d.get(title, 0) - s) > 0.5:
            d[title] = round(s, 2)
            save("durations.json", d)


# ── CLI 응답 로그 (재시작 후에도 보이게) ──
def get_log() -> list:
    r = load("cli_log.json", [])
    return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []


def set_log(items: list) -> None:
    save("cli_log.json", list(items)[:60])


# ── 재생목록·폴더 ──
def _norm_item(x) -> dict | None:
    """항목 = {"title": DisplayTitle, "inst": 악기명|""}. 예전 문자열 항목도 받아준다. 모양이 틀리면 None(버림)."""
    if isinstance(x, str):
        t = x.strip()
        return {"title": t, "inst": ""} if t else None
    if isinstance(x, dict) and isinstance(x.get("title"), str) and x["title"].strip():
        inst = x.get("inst")
        return {"title": x["title"], "inst": inst.strip() if isinstance(inst, str) else ""}
    return None


def get_lists() -> dict:
    d = _load_dict("playlists.json")
    folders = [f for f in (d.get("folders") or []) if isinstance(f, dict) and isinstance(f.get("id"), str) and f["id"]]
    for f in folders:
        f["name"] = str(f.get("name") or "새 폴더")
        f["parent"] = f.get("parent") if isinstance(f.get("parent"), str) else None
    pls = [pl for pl in (d.get("playlists") or []) if isinstance(pl, dict) and isinstance(pl.get("id"), str) and pl["id"]]
    for pl in pls:
        pl["name"] = str(pl.get("name") or "새 재생목록")
        pl["folder"] = pl.get("folder") if isinstance(pl.get("folder"), str) else None
        pl["items"] = [it for it in (_norm_item(x) for x in (pl.get("items") or [])) if it]
        pl["memo"] = str(pl.get("memo") or "")
    return {"folders": folders, "playlists": pls}


def set_lists(d: dict) -> None:
    save("playlists.json", d)


def new_id() -> str:
    return uuid.uuid4().hex[:10]


# ── 아티스트 사전·수동 지정 ──
def get_artists() -> dict:
    """{"artists":[{id,name,aliases[]}], "assign":{원본제목: artistId}, "noise":[추가 잡음어]}. 항목 모양을 정규화한다."""
    d = _load_dict("artists.json")
    arts = []
    for a in d.get("artists") or []:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not a["id"]:
            continue
        a["name"] = str(a.get("name") or "").strip() or a["id"]
        a["aliases"] = [x for x in (a.get("aliases") or []) if isinstance(x, str) and x.strip()]
        arts.append(a)
    ids = {a["id"] for a in arts}
    assign = {t: v for t, v in (d.get("assign") or {}).items() if isinstance(t, str) and v in ids} if isinstance(d.get("assign"), dict) else {}
    noise = [x for x in (d.get("noise") or []) if isinstance(x, str) and x.strip()]
    return {"artists": arts, "assign": assign, "noise": noise}


def set_artists(d: dict) -> None:
    save("artists.json", d)

"""로컬 웹 서버 (127.0.0.1:19997). UI 는 ui/index.html, API 는 /api/*. 표준 라이브러리만 쓴다.

동기화: POST /api/sync → get_instruments, get_music_scores 를 CLI 로 받아 data/ 에 저장 (+ fixtures/ 원본).
재생:   POST /api/play {"title": DisplayTitle, "instrument": Name|null} → change_instrument → play_music_score.
정지:   POST /api/stop → get_activity.Performance.IsPlaying 확인 후 stop_action (invalid_state 는 짧게 재시도).
"""
from __future__ import annotations

import errno
import secrets
import urllib.request
import hashlib
import hmac
import base64
import io
import json
import re
import socket
import os
import sys
import time
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
_DEV_ENV_OK = (not FROZEN) or os.environ.get("MABI_DEV") == "1"   # 배포판은 개발용 환경변수(MABI_DATA_DIR·MABI_CLI_EXE)를 무시
BASE = (os.environ.get("MABI_DATA_DIR") if _DEV_ENV_OK else None) or (os.path.join(os.environ["LOCALAPPDATA"], "MobiFolio") if os.environ.get("LOCALAPPDATA")
                                           else (os.path.dirname(sys.executable) if FROZEN else HERE))   # 데이터·로그 위치 (store.user_base 와 같은 규칙)
os.makedirs(BASE, exist_ok=True)
RES = getattr(sys, "_MEIPASS", HERE)                               # 묶인 리소스(ui/) 위치
if FROZEN:
    # --noconsole 이면 stdout 이 없다 → 로그를 exe 옆 파일로
    _logp = os.path.join(BASE, "mobifolio.log")
    try:
        if os.path.getsize(_logp) > 2_000_000:   # 무한 성장 방지: 2MB 넘으면 새로 시작
            os.remove(_logp)
    except OSError:
        pass
    try:
        _logf = open(_logp, "a", encoding="utf-8", buffering=1)
    except OSError:   # exe 옆에 쓸 수 없으면(읽기 전용 폴더 등) 임시 폴더로
        _logf = open(os.path.join(os.environ.get("TEMP", "."), "mobifolio.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _logf
elif sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, HERE)
import cli_transport as cli   # noqa: E402
import library as lib         # noqa: E402
import store                  # noqa: E402

# 예전 위치(일렉트론이 넘긴 앱 폴더, exe 옆, 프로젝트 폴더)의 data/ 를 사용자 폴더로 한 번 옮긴다
store.migrate_legacy([os.path.join(os.environ["LOCALAPPDATA"], "MabiScoreBox") if os.environ.get("LOCALAPPDATA") else None,   # 이전 이름(악보함) 시절 사용자 폴더
                      os.environ.get("MABI_LEGACY_DIR"), os.path.dirname(sys.executable) if FROZEN else None,
                      os.path.dirname(os.path.dirname(sys.executable)) if FROZEN else None,   # 패키지: resources\.. = 앱 폴더
                      HERE if not FROZEN else None])

# 경량판(LITE): 일렉트론 셸 없이 이 exe 를 바로 실행한 경우 — 스스로 빈 포트·토큰을 만들고 Edge/Chrome 앱 창을 띄운다
LITE_REUSE = os.environ.get("MABI_LITE_REUSE") == "1"   # 자동 업데이트로 다시 뜬 경우: 창은 이미 있으니 새로 띄우지 않는다
LITE = FROZEN and not os.environ.get("MABI_PARENT_PID") and (not os.environ.get("MABI_NO_BROWSER") or LITE_REUSE)


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = int(os.environ.get("MABI_PLAYLIST_PORT") or (_free_port() if LITE else 19997))
TOKEN = os.environ.get("MABI_TOKEN", "") or (secrets.token_hex(24) if LITE else "")   # 셸(또는 경량판 스스로)이 실행마다 만드는 비밀 — 있으면 모든 API 가 이 값을 요구한다
PARENT = os.environ.get("MABI_PARENT_PID", "")
LITE_FILE = os.path.join(BASE, "lite.json")   # 경량판이 떠 있는 포트 (두 번째 실행이 창만 다시 열 때 씀)
UPDATE_DIR = os.path.join(BASE, "update")     # 받은 새 exe 와 교체 스크립트
MAX_UPDATE_BYTES = 200 * 1024 * 1024
VERSION = "0.2.6"
_srv = None   # ThreadingHTTPServer (종료용)


def _say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _shutdown() -> None:
    time.sleep(0.2)
    threading.Timer(3.0, lambda: os._exit(0)).start()   # 어떤 이유로든 3초 안에 못 끝나면 강제 종료 (멈춘 프로세스를 남기지 않게)
    if LITE:
        try:
            with open(LITE_FILE, encoding="utf-8") as f:
                mine = int(json.load(f).get("pid", 0)) == os.getpid()
            if mine:
                os.remove(LITE_FILE)   # 내 기록일 때만 지운다 (업데이트로 뜬 새 인스턴스의 기록은 남겨야 한다)
        except (OSError, ValueError):
            pass
    try:
        _say("shutdown: stopping server")
        try:
            import faulthandler
            faulthandler.dump_traceback_later(2.0, exit=False, file=sys.stderr)   # 2초 넘게 걸리면 어디서 막혔는지 로그에 남긴다
        except Exception:
            pass
        if _srv:
            _srv.shutdown()
        _say("shutdown: done")
    finally:
        os._exit(0)


def _watch_parent() -> None:
    """MABI_PARENT_PID(일렉트론)가 죽으면 같이 끝난다 — 셸이 강제 종료돼도 고아 백엔드가 남지 않게."""
    pid = os.environ.get("MABI_PARENT_PID")
    if not pid or not pid.isdigit():
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000, False, int(pid))   # SYNCHRONIZE
        if not h:
            return
        def wait():
            k32.WaitForSingleObject(h, 0xFFFFFFFF)
            os._exit(0)
        threading.Thread(target=wait, daemon=True).start()
    except Exception:
        pass
UI_DIR = os.path.join(RES, "ui")
if _DEV_ENV_OK and os.environ.get("MABI_UI_DIR"):   # 개발용: 다른 화면 꾸러미(미니판 mini/ui 등)를 같은 백엔드로 띄운다
    UI_DIR = os.path.abspath(os.environ["MABI_UI_DIR"])


def _load_ui_meta() -> dict:
    """ui/app.json (선택). {"name": "Mini", "width": 440, "height": 920, "update": false}
    화면 꾸러미가 앱 창 크기, 단일 실행·창 프로필 이름(name), 자동 업데이트 사용 여부를 정한다. 없으면 기본(정식 화면)."""
    try:
        with open(os.path.join(UI_DIR, "app.json"), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


UI_META = _load_ui_meta()
UI_NAME = re.sub(r"[^A-Za-z0-9]", "", str(UI_META.get("name") or ""))[:16]   # "" = 기본 화면
if UI_NAME:
    LITE_FILE = os.path.join(BASE, f"lite-{UI_NAME}.json")   # 화면 꾸러미마다 따로 (미니판과 경량판을 같이 띄울 수 있게)
_APP_PROFILE = ".appwindow-profile" + (f"-{UI_NAME}" if UI_NAME else "")


def _window_size() -> str:
    try:
        w = int(UI_META.get("width") or 1280); h = int(UI_META.get("height") or 860)
    except (TypeError, ValueError):
        w, h = 1280, 860
    return f"{min(max(w, 300), 4000)},{min(max(h, 300), 4000)}"
_cli_lock = threading.Lock()   # CLI 는 한 번에 하나만 (게임 파이프 직렬)
_log: list[dict] = store.get_log()   # 최근 CLI 응답 요약 (UI 「CLI 응답」) — data/cli_log.json 에 남겨 재시작 후에도 보인다
_last_play: dict = {"title": "", "inst": ""}   # 길이 캐시 키(DisplayTitle)용

cli.set_exe_override(str(store.get_settings().get("cli_exe") or ""))
_log_lock = threading.Lock()
_ok_hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}", "127.0.0.1", "localhost"}
MAX_BODY = 1_000_000
_PFX = re.compile(r"^악보\s*[:：]\s*")


def _note(r, summary: str = "") -> None:
    """CLI 결과를 로그에 남긴다 (최근 60건, UI 는 40건). 여러 스레드가 동시에 불러도 안전하게."""
    row = {"ts": time.time(), "command": r.command, "ok": r.ok, "error": r.error, "message": r.message,
           "elapsed": round(r.elapsed, 3), "summary": summary,
           "raw": (json.dumps(r.body, ensure_ascii=False)[:1500] if r.body is not None else r.raw[:1500])}
    with _log_lock:
        _log.insert(0, row)
        del _log[60:]
        snap = list(_log)
    try:
        store.set_log(snap)
    except Exception as e:
        print(f"[log] 저장 실패: {e}", flush=True)


_build_memo: dict = {"key": None, "items": None}


def _build_items() -> list:
    """lib.build 는 193곡 기준 수 ms 지만 요청마다 다시 도는 것을 막는다 (같은 캐시·같은 아티스트 상태면 재사용)."""
    sc = store.get_cache("scores")
    art = store.get_artists()
    key = (sc["fetched_at"], len(sc["items"]), json.dumps(art, sort_keys=True, ensure_ascii=False))
    with store.LOCK:
        if _build_memo["key"] == key and _build_memo["items"] is not None:
            return _build_memo["items"]
        items = lib.build(sc["items"], art)
        _build_memo["key"], _build_memo["items"] = key, items
        return items


def _s(v, default: str = "") -> str:
    """문자열 강제 (None/숫자/딕셔너리가 와도 .strip() 에서 죽지 않게)."""
    return v.strip() if isinstance(v, str) else (str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default)


def _titles(v) -> list[str]:
    """titles 는 문자열 배열만 (문자열 하나를 글자 단위로 돌지 않게)."""
    if isinstance(v, list):
        return [x for x in v if isinstance(x, str) and x.strip()]
    return []


def _json(handler, obj, status=200):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler) -> dict:
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return {"_error": "bad_length"}
    if n <= 0:
        return {}
    if n > MAX_BODY:
        left = min(n, 8 * MAX_BODY)   # 상한까지만 비우고 답한다 (그 이상은 연결을 닫는다)
        while left > 0:
            chunk = handler.rfile.read(min(65536, left))
            if not chunk:
                break
            left -= len(chunk)
        handler.close_connection = True
        return {"_error": "too_large"}
    raw = handler.rfile.read(n)
    for enc in ("utf-8", "mbcs"):   # 브라우저는 utf-8. 콘솔 도구가 cp949 로 보내도 조용히 빈 값이 되지 않게
        try:
            v = json.loads(raw.decode(enc))
            return v if isinstance(v, dict) else {"_error": "not_object"}
        except Exception:
            continue
    return {"_error": "decode_error"}


def _cli(command, body=None, timeout=660.0):
    with _cli_lock:
        return cli.call(command, body, timeout)


def _probe() -> dict:
    """cli.probe() 를 CLI 잠금 안에서 (한 번에 하나 규칙)."""
    with _cli_lock:
        return cli.probe()


# ── 동기화 ──
def sync() -> dict:
    out = {"ok": True, "steps": []}
    st = _cli("status", timeout=20)
    out["steps"].append(st.to_dict())
    _note(st, "연결 확인")
    if not st.ok:
        out["ok"] = False
        return out
    for command, kind, label in (("get_instruments", "instruments", "악기"), ("get_music_scores", "scores", "악보")):
        r = _cli(command, "", timeout=120)   # 빈 필터 = 전체
        n = len(r.body) if isinstance(r.body, list) else None
        out["steps"].append({**r.to_dict(), "body": None, "count": n})
        _note(r, f"{label} {n}건 수신" if r.ok else f"{label} 수신 실패")
        if not r.ok:
            out["ok"] = False
            continue
        items = r.body if isinstance(r.body, list) else (r.body.get("items") if isinstance(r.body, dict) else None)
        if not isinstance(items, list):
            out["ok"] = False
            out["steps"][-1]["error"] = "bad_shape"
            _note(r, f"{label} 응답 모양 이상 — 캐시 유지")
            continue
        if not items and store.get_cache(kind)["items"]:
            out["steps"][-1]["error"] = "empty_result"
            _note(r, f"{label} 0건 — 기존 캐시 유지")
            continue
        store.set_cache(kind, items)
        if not FROZEN:   # 원본 응답 사본(fixtures/)은 개발 실행에서만 — 배포판에 중복 사본을 남기지 않는다
            store.save_fixture(command, r.body)
    return out


# ── 재생목록 ──
def _lists() -> dict:
    """store.get_lists() + 항목 key 보정 (예전 '제목' 키, 없어진 '제목#n' → 같은 제목의 첫 악보)."""
    d = store.get_lists()
    items = _build_items()
    for pl in d["playlists"]:
        seen: set[str] = set(); kept = []
        for it in pl["items"]:
            it["key"] = lib.resolve_key(it["key"], it["title"], items)
            if it["key"] not in seen:   # 이어붙인 결과 같은 악보가 둘이면 첫 항목만 (reorder 가 key 로 합치며 항목이 사라지지 않게)
                seen.add(it["key"]); kept.append(it)
        pl["items"] = kept
    return d


def lists_op(op: str, p: dict) -> dict:
    with store.LOCK:   # 읽고-고쳐-쓰기 전체를 잠가 동시 요청이 서로의 변경을 덮어쓰지 않게
        return _lists_op(op, p)


def _lists_op(op: str, p: dict) -> dict:
    d = _lists()
    cur_items = _build_items()
    key_title = lib.key_map(cur_items)   # key → 제목 (담을 때 제목을 같이 저장해 두면 보관함이 바뀌어도 이름은 남는다)
    pls, fds = d["playlists"], d["folders"]
    now = time.time()
    fids = {f["id"] for f in fds}
    name = _s(p.get("name"))[:200]
    folder = p.get("folder") if p.get("folder") in fids else None
    if op == "folder_create":
        parent = p.get("parent") if p.get("parent") in fids else None
        f = {"id": store.new_id(), "name": name or "새 폴더", "parent": parent, "created": now}
        fds.append(f)
    elif op == "folder_rename":
        for f in fds:
            if f["id"] == p.get("id") and name:
                f["name"] = name
    elif op == "folder_delete":
        fid = p.get("id")
        # 하위 폴더는 상위로, 재생목록은 폴더 없음으로
        parent = next((f.get("parent") for f in fds if f["id"] == fid), None)
        d["folders"] = [f for f in fds if f["id"] != fid]
        for f in d["folders"]:
            if f.get("parent") == fid:
                f["parent"] = parent
        for pl in pls:
            if pl.get("folder") == fid:
                pl["folder"] = parent
    elif op == "create":
        pl = {"id": store.new_id(), "name": name or "새 재생목록", "folder": folder,
              "items": [], "created": now, "updated": now}
        pls.append(pl)
    elif op == "rename":
        for pl in pls:
            if pl["id"] == p.get("id") and name:
                pl["name"] = name; pl["updated"] = now
    elif op == "move":
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["folder"] = folder; pl["updated"] = now
    elif op == "delete":
        d["playlists"] = [pl for pl in pls if pl["id"] != p.get("id")]
    elif op == "add":
        for pl in pls:
            if pl["id"] == p.get("id"):
                have = {it["key"] for it in pl["items"]}
                for k in _titles(p.get("keys") or p.get("titles")):   # keys = 내부 식별자 (예전 UI 의 titles 도 받는다)
                    k = lib.resolve_key(k, key_title.get(k) or lib.key_title_guess(k), cur_items)   # 제목만 온 동명 악보·없는 채번은 그 제목의 첫 장으로
                    if k not in have:
                        pl["items"].append({"key": k, "title": key_title.get(k) or lib.key_title_guess(k), "inst": ""}); have.add(k)
                pl["updated"] = now
    elif op == "remove":
        rm = set(_titles(p.get("keys") or p.get("titles")))
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["items"] = [it for it in pl["items"] if it["key"] not in rm]; pl["updated"] = now
    elif op == "reorder":   # items = key 순서 배열
        for pl in pls:
            if pl["id"] == p.get("id"):
                by = {it["key"]: it for it in pl["items"]}
                order = [by[k] for k in _titles(p.get("items")) if k in by]
                seen = {it["key"] for it in order}
                pl["items"] = order + [it for it in pl["items"] if it["key"] not in seen]; pl["updated"] = now
    elif op == "set_inst":   # 곡별 악기. key(예전 UI 는 title) 없으면 목록 전체
        one = next((v for v in (p.get("key"), p.get("title")) if isinstance(v, str) and v.strip()), "")   # 원문 그대로 비교 (끝 공백 제목도 있다)
        for pl in pls:
            if pl["id"] == p.get("id"):
                for it in pl["items"]:
                    if not one or it["key"] == one or (not p.get("key") and it["title"] == one):
                        it["inst"] = _s(p.get("inst"))
                pl["updated"] = now
    elif op == "memo":
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["memo"] = _s(p.get("memo"))[:4000]; pl["updated"] = now
    else:
        return {"ok": False, "error": "unknown_op"}
    store.set_lists(d)
    return {"ok": True, **d}


# ── 아티스트 사전·수동 지정 ──
def artists_op(op: str, p: dict) -> dict:
    with store.LOCK:
        return _artists_op(op, p)


def _artists_op(op: str, p: dict) -> dict:
    d = store.get_artists()
    arts = d["artists"]
    by_id = {a["id"]: a for a in arts}

    def ensure(name: str) -> str:
        """이름으로 아티스트를 찾거나 만든다 (별칭 포함, 대소문자·기호 무시). id 반환."""
        n = lib.norm(name)
        for a in arts:
            if lib.norm(a["name"]) == n or any(lib.norm(x) == n for x in a.get("aliases", [])):
                return a["id"]
        a = {"id": store.new_id(), "name": name.strip(), "aliases": []}
        arts.append(a); by_id[a["id"]] = a
        return a["id"]

    name = _s(p.get("name"))[:200]
    if op == "create":
        ensure(name or "이름 없음")
    elif op == "rename":
        a = by_id.get(p.get("id"))
        if not a:
            return {"ok": False, "error": "not_found"}
        if name:
            a["name"] = name
    elif op == "alias":
        a = by_id.get(p.get("id"))
        al = _s(p.get("alias"))[:200]
        if not a:
            return {"ok": False, "error": "not_found"}
        if al and al not in a["aliases"] and lib.norm(al) != lib.norm(a["name"]):
            a["aliases"].append(al)
    elif op == "merge":   # from → into : from 의 이름은 into 의 별칭으로, 지정도 옮긴다
        src, dst = by_id.get(p.get("from")), by_id.get(p.get("into"))
        if not src or not dst:
            return {"ok": False, "error": "not_found"}
        if src is not dst:
            dst["aliases"] = list(dict.fromkeys(dst["aliases"] + [src["name"]] + src.get("aliases", [])))
            for t, aid in list(d["assign"].items()):
                if aid == src["id"]:
                    d["assign"][t] = dst["id"]
            d["artists"] = [a for a in arts if a["id"] != src["id"]]
    elif op == "assign":   # titles[] → id 또는 name(없으면 생성). 자동 추출 키('auto:…')를 넘기면 그 이름으로 생성
        aid = p.get("id")
        if not aid or aid not in by_id:
            if not name:
                return {"ok": False, "error": "empty_name", "message": "아티스트 이름이 비어 있습니다."}
            aid = ensure(name)
        for t in _titles(p.get("titles")):
            d["assign"][t] = aid
    elif op == "unassign":
        for t in _titles(p.get("titles")):
            d["assign"].pop(t, None)
    elif op == "delete":
        aid = p.get("id")
        if aid not in by_id:
            return {"ok": False, "error": "not_found"}
        d["artists"] = [a for a in arts if a["id"] != aid]
        d["assign"] = {t: v for t, v in d["assign"].items() if v != aid}
    elif op == "noise":   # 잡음어 추가/제거
        w = _s(p.get("word")).lower()[:50]
        if w:
            if p.get("remove"):
                d["noise"] = [x for x in d["noise"] if x != w]
            elif w not in d["noise"]:
                d["noise"].append(w)
    else:
        return {"ok": False, "error": "unknown_op"}
    store.set_artists(d)
    return {"ok": True, **d}


# ── 재생·정지 ──
def _ensemble() -> dict:
    """주변 플레이어의 연주 상태만 간추린다 (합주 인식용). 이름은 UI 표시용 RealmName 하나만."""
    r = _cli("get_near_pcs", timeout=30)
    out = {"ok": r.ok, "error": r.error, "message": r.message, "players": []}
    rows = r.body if isinstance(r.body, list) else []
    for x in rows:
        if not isinstance(x, dict):
            continue
        pf = x.get("Performance") if isinstance(x.get("Performance"), dict) else {}
        out["players"].append({
            "name": str(x.get("RealmName") or ""), "distance": x.get("Distance"),
            "playing": bool(pf.get("IsPlaying")), "title": str(pf.get("MusicTitle") or ""),
            "channels": pf.get("ChannelCount") or 0, "total": pf.get("TotalDurationSeconds") or 0,
            "elapsed": pf.get("ElapsedSeconds") or 0, "remaining": pf.get("RemainingSeconds"), "loop": bool(pf.get("IsLoop")),
        })
    return out


def _activity() -> dict:
    a = _cli("get_activity", timeout=30)
    perf = (a.body or {}).get("Performance") if isinstance(a.body, dict) else None
    # 재생 중이면 마지막으로 튼 곡의 길이를 기억해 둔다 (목록 총길이·진행 막대용).
    # 게임 쪽 MusicTitle 은 '악보: ' 접두가 없으므로 접두를 뗀 뒤 같은 곡일 때만 기록 (게임에서 직접 튼 다른 곡이 덮어쓰지 않게)
    if isinstance(perf, dict) and perf.get("IsPlaying") and _last_play.get("title"):
        mine = _PFX.sub("", _last_play["title"]).strip()
        theirs = _PFX.sub("", str(perf.get("MusicTitle") or "")).strip()
        if mine and (not theirs or mine == theirs):
            store.set_duration(_last_play["title"], perf.get("TotalDurationSeconds") or 0)
    return a.to_dict()


# ── 자동 업데이트 (경량판) ──
def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4]) or (0,)


def _safe_url(u: str) -> bool:
    """https 만 허용. 루프백 http 는 로컬 테스트용으로만 허용한다."""
    u = (u or "").strip().lower()
    return u.startswith("https://") or u.startswith("http://127.0.0.1")


def update_check() -> dict:
    """설정의 latest.json 을 읽어 새 버전이 있는지 본다. 보내는 것은 없다(사용자 정보 없음)."""
    if UI_META.get("update") is False:   # 이 화면 꾸러미는 자동 업데이트를 쓰지 않는다 (미니판: latest.json 이 경량판 exe 를 가리키므로)
        return {"ok": True, "available": False, "current": VERSION, "disabled": True}
    url = str(store.get_settings().get("update_url") or "").strip()
    if not _safe_url(url):
        return {"ok": False, "error": "no_url", "message": "업데이트 확인 주소(https)가 설정되지 않았습니다.", "current": VERSION}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"MobiFolio/{VERSION}", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read(65536).decode("utf-8-sig"))
    except Exception as e:
        return {"ok": False, "error": "fetch_failed", "message": f"업데이트 정보를 받지 못했습니다: {type(e).__name__}", "current": VERSION}
    if not isinstance(data, dict):
        return {"ok": False, "error": "bad_manifest", "message": "latest.json 형식이 잘못되었습니다.", "current": VERSION}
    latest = str(data.get("version") or "")
    dl = str(data.get("url") or "")
    sha = str(data.get("sha256") or "").lower()
    ok_manifest = bool(latest) and _safe_url(dl) and re.fullmatch(r"[0-9a-f]{64}", sha or "") is not None
    return {"ok": True, "current": VERSION, "latest": latest, "available": ok_manifest and _vtuple(latest) > _vtuple(VERSION),
            "url": dl, "sha256": sha, "notes": str(data.get("notes") or "")[:2000], "lite": LITE, "manifest_ok": ok_manifest}


def update_apply(info: dict) -> dict:
    """새 exe 를 받아 SHA256 을 검증하고 제자리에 바꿔 넣은 뒤, 같은 포트·토큰으로 새 프로세스를 띄우고 자신은 끝난다 (경량판만).
    Windows 는 실행 중인 exe 의 '이름 바꾸기'를 허용하므로 외부 스크립트 없이 된다: 현재 exe → .bak, 새 파일 → 현재 이름.
    열려 있는 창의 페이지는 새 포트로 이동하므로 창이 닫혔다 열리지 않는다. .bak 은 새 프로세스가 지운다."""
    if not LITE:
        return {"ok": False, "error": "not_lite", "message": "자동 업데이트는 경량판(MobiFolioLite.exe)에서만 지원합니다."}
    dl, sha, latest = str(info.get("url") or ""), str(info.get("sha256") or "").lower(), str(info.get("latest") or "")
    if not _safe_url(dl) or not re.fullmatch(r"[0-9a-f]{64}", sha or ""):
        return {"ok": False, "error": "bad_manifest", "message": "다운로드 주소나 SHA256 이 없습니다."}
    os.makedirs(UPDATE_DIR, exist_ok=True)
    part = os.path.join(UPDATE_DIR, "MobiFolioLite.download.part")
    h = hashlib.sha256(); size = 0
    try:
        req = urllib.request.Request(dl, headers={"User-Agent": f"MobiFolio/{VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPDATE_BYTES:
                    raise ValueError("too large")
                h.update(chunk); f.write(chunk)
    except Exception as e:
        try:
            os.remove(part)
        except OSError:
            pass
        return {"ok": False, "error": "download_failed", "message": f"다운로드 실패: {type(e).__name__}"}
    if h.hexdigest() != sha:
        os.remove(part)
        return {"ok": False, "error": "sha_mismatch", "message": "받은 파일의 SHA256 이 latest.json 과 다릅니다. 적용하지 않았습니다."}
    cur = sys.executable   # onefile: 부모(부트로더) exe 경로 = 실제 배포 파일
    bak = cur + ".bak"
    try:
        try:
            os.remove(bak)
        except OSError:
            pass
        os.rename(cur, bak)          # 실행 중이어도 이름 바꾸기는 된다
        try:
            os.replace(part, cur)
        except OSError:
            os.rename(bak, cur); raise
    except OSError as e:
        return {"ok": False, "error": "replace_failed", "message": f"파일을 바꾸지 못했습니다: {e}"}
    # 새 프로세스: 같은 포트를 물려주면 페이지가 그대로 이동할 수 있다 — 이 프로세스가 포트를 놓아야 하므로 새 포트를 준다
    new_port = _free_port()
    # 옛 부트로더(부모)는 자식이 끝난 뒤에도 남는 경우가 있어(실측), 새 인스턴스가 그 pid 를 넘겨받아 정리한다 (+ 옛 임시 폴더)
    # PyInstaller 부트로더가 자식에게 주는 내부 변수(_PYI_*, _MEIPASS2)를 물려주면 새 exe 가 '이미 풀린 임시 폴더'를 쓰는
    # 자식 모드로 떠서(우리 옛 임시 폴더 → 곧 삭제됨) 화면 파일을 잃는다 — 반드시 걷어내고 띄운다
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    env.update(MABI_PLAYLIST_PORT=str(new_port), MABI_TOKEN=TOKEN, MABI_LITE_REUSE="1",
               MABI_OLD_PID=str(os.getppid()), MABI_OLD_MEI=getattr(sys, "_MEIPASS", ""))
    env.pop("MABI_NO_BROWSER", None)
    _lite_release()   # 단일 인스턴스 뮤텍스·lite.json 을 먼저 놓는다
    try:
        import subprocess
        # PyInstaller 부트로더는 자식을 Job 객체에 넣고 그 안의 프로세스가 다 끝날 때까지 기다린다 — 새 인스턴스는 Job 에서 떼어 띄운다
        flags = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED | NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
        try:
            subprocess.Popen([cur], cwd=os.path.dirname(cur), env=env, close_fds=True, creationflags=flags)
        except OSError:
            subprocess.Popen([cur], cwd=os.path.dirname(cur), env=env, close_fds=True, creationflags=flags & ~0x01000000)
    except OSError as e:
        return {"ok": False, "error": "spawn_failed", "message": f"새 버전을 실행하지 못했습니다: {e}"}
    _say(f"update: {VERSION} -> {latest}, new instance on port {new_port}")
    threading.Timer(2.0, _shutdown).start()
    return {"ok": True, "message": f"{latest} 로 업데이트합니다.", "restart": True, "port": new_port}


def _cleanup_bak() -> None:
    """업데이트 뒤처리: 옛 부트로더가 남아 있으면 끝내고(우리 프로세스), 옛 임시 폴더와 <exe>.bak 을 지운다."""
    bak = sys.executable + ".bak"
    old_pid = os.environ.get("MABI_OLD_PID", "")
    old_mei = os.environ.get("MABI_OLD_MEI", "")
    if not FROZEN or not (os.path.exists(bak) or old_pid):
        return
    def go():
        if old_pid.isdigit():
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                h = k32.OpenProcess(0x00100000 | 0x0001, False, int(old_pid))   # SYNCHRONIZE | TERMINATE
                if h:
                    if k32.WaitForSingleObject(h, 15000) != 0:   # 15초 안에 스스로 안 끝나면
                        k32.TerminateProcess(h, 0); _say(f"update: old bootloader {old_pid} did not exit — terminated")
                    k32.CloseHandle(h)
            except Exception as e:
                _say(f"update: old process check failed: {e}")
        if old_mei and os.path.isdir(old_mei) and os.path.basename(old_mei).startswith("_MEI"):
            import shutil
            shutil.rmtree(old_mei, ignore_errors=True)
        err = None
        for _ in range(30):
            if not os.path.exists(bak):
                return
            try:
                os.remove(bak); _say("update: removed old .bak"); return
            except OSError as e:
                err = e; time.sleep(1.0)
        _say(f"update: could not remove .bak after 30s: {err}")
    threading.Thread(target=go, daemon=True).start()


def _mark_equipped(name: str) -> None:
    """change_instrument 성공 후 instruments 캐시의 IsEquipped 를 앱이 아는 대로 맞춘다 (fetched_at 은 유지)."""
    try:
        with store.LOCK:
            c = store.get_cache("instruments")
            changed = False
            for x in c["items"]:
                if isinstance(x, dict):
                    want = (lib.pick(x, lib.NAME_KEYS).strip() == name.strip())
                    if bool(x.get("IsEquipped")) != want:
                        x["IsEquipped"] = want; changed = True
            if changed:
                store.save("instruments.json", c)
    except Exception as e:
        print(f"[play] 장착 표시 갱신 실패: {e}", flush=True)


def play(title: str, instrument: str | None, key: str = "") -> dict:
    """(설정) 연주 중이면 먼저 정지 → (악기 지정 시) change_instrument → play_music_score."""
    out = {"ok": True, "steps": []}
    if not (title or "").strip():
        return {"ok": False, "steps": [], "error": "empty_title", "message": "재생할 악보 제목이 비어 있습니다."}
    s = store.get_settings()
    inst = instrument if isinstance(instrument, str) and instrument.strip() else (s.get("default_inst") or None)
    # 없는 악보/악기는 현재 연주를 끊기 전에 걸러낸다 (캐시가 있을 때만 검사)
    cache = [lib.pick(x, lib.TITLE_KEYS) for x in store.get_cache("scores")["items"] if isinstance(x, dict)]
    if cache and title not in cache:
        return {"ok": False, "steps": [], "error": "not_found", "message": "보관함에 없는 악보입니다. 갱신 후 다시 시도하세요."}
    if title != title.strip():   # 실측: 제목 끝에 공백이 있으면 CLI 가 어떤 표기로도 못 찾는다 → 현재 연주를 끊기 전에 알린다
        return {"ok": False, "steps": [], "error": "cli_title_name",
                "message": "게임 CLI 가 제목 끝에 공백이 있는 악보를 찾지 못합니다 (CLI 쪽 문제). 게임에서 악보 이름의 끝 공백을 지운 뒤 갱신하면 재생됩니다."}
    insts = [lib.pick(x, lib.NAME_KEYS) for x in store.get_cache("instruments")["items"] if isinstance(x, dict)]
    if inst and insts and inst not in insts and inst.strip() not in [x.strip() for x in insts]:
        return {"ok": False, "steps": [], "error": "not_found", "message": f"보유하지 않은 악기입니다: {inst}"}
    if s.get("stop_before_play"):
        a = _cli("get_activity", timeout=30)
        perf = (a.body or {}).get("Performance") if isinstance(a.body, dict) else None
        if isinstance(perf, dict) and perf.get("IsPlaying"):
            r0 = _cli("stop_action", timeout=60)
            out["steps"].append(r0.to_dict()); _note(r0, "현재 연주 정지")
            if r0.error == "invalid_state":
                time.sleep(0.8)
    if inst:
        # 이름 양끝에 공백이 있는 악기는 CLI 가 어떤 표기로도 못 찾는다(실측) — 이런 악기만 '이미 장착 중'이면 변경을 건너뛴다.
        # 정상 이름은 항상 CLI 에 맡긴다 (CLI 가 "Already equipped." 로 즉시 답하고, 캐시는 마지막 갱신 시점이라 믿을 수 없다).
        broken = inst != inst.strip() or inst not in insts
        equipped = next((lib.pick(x, lib.NAME_KEYS) for x in store.get_cache("instruments")["items"] if isinstance(x, dict) and x.get("IsEquipped")), "")
        if broken and equipped and equipped.strip() == inst.strip():
            out["steps"].append({"command": "change_instrument", "ok": True, "skipped": True, "message": "이미 장착 중 (마지막 갱신 기준)"})
        else:
            r = _cli("change_instrument", {"name": inst}, timeout=120)
            out["steps"].append(r.to_dict()); _note(r, f"악기 → {inst}")
            if not r.ok and r.error == "not_found" and inst != inst.strip():   # 공백 뗀 이름으로 한 번 더 (CLI 가 고쳐질 때를 대비)
                r = _cli("change_instrument", {"name": inst.strip()}, timeout=120)
                out["steps"].append(r.to_dict()); _note(r, f"악기 → {inst.strip()} (공백 제거 재시도)")
            if not r.ok:
                out["ok"] = False
                if r.error == "not_found" and broken:
                    out["error"] = "cli_instrument_name"
                    out["message"] = "게임 CLI 가 이 악기를 찾지 못합니다 (이름 끝 공백 때문 — CLI 쪽 문제). 게임에서 직접 장착한 뒤 악기를 「악기 그대로」로 두고 재생하세요."
                return out
            _mark_equipped(inst)   # 성공했으면 캐시의 장착 표시도 맞춘다 (다음 갱신 전까지 UI 「장착」 배지·건너뛰기 판정에 쓰임)
    for attempt in range(4):   # 정지 직후 상태 전이 중이면 invalid_state → 짧게 재시도 (다음 곡으로 건너뛰지 않게)
        r = _cli("play_music_score", {"title": title}, timeout=60)   # 즉시 반환 명령: 잠금을 오래 잡지 않게
        out["steps"].append(r.to_dict()); _note(r, f"재생 · {title}" + (f" (재시도 {attempt})" if attempt else ""))
        if r.ok or r.error != "invalid_state":
            break
        time.sleep(0.8 + 0.4 * attempt)
    out["ok"] = r.ok
    if r.ok:
        _last_play.update({"title": title, "inst": inst or ""})
        store.push_recent(title, inst or "", key or "")
    return out


def stop() -> dict:
    """연주 정지. get_activity.Performance.IsPlaying 이 false 면 정지할 게 없으니 성공으로 본다(invalid_state 재시도 낭비 방지).
    연주 중인데 invalid_state 가 오면 상태 전이 중(§7-1) 이라 짧게 재시도."""
    out = {"ok": False, "steps": []}
    a = _cli("get_activity", timeout=30)
    out["steps"].append(a.to_dict())
    perf = (a.body or {}).get("Performance") if isinstance(a.body, dict) else None
    if isinstance(perf, dict) and not perf.get("IsPlaying"):
        out["ok"] = True
        out["message"] = "재생 중인 곡이 없습니다."
        return out
    for attempt in range(4):
        r = _cli("stop_action", timeout=60)
        out["steps"].append(r.to_dict()); _note(r, "정지")
        if r.ok:
            out["ok"] = True
            return out
        if r.error != "invalid_state":
            return out
        time.sleep(1.0 + attempt * 0.5)
    return out


class H(SimpleHTTPRequestHandler):
    timeout = 30   # 느린/멈춘 클라이언트가 스레드를 무한 점유하지 않게

    def __init__(self, *a, **k):
        super().__init__(*a, directory=UI_DIR, **k)

    def log_message(self, fmt, *args):   # 조용히
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/health":   # 일렉트론 셸이 "이 포트가 정말 모비폴리오인지" 확인하는 용도
            return _json(self, {"app": "mobifolio", "version": VERSION, "pid": os.getpid(), "frozen": FROZEN, "lite": LITE, "ui": UI_NAME or "full",
                                "parent": int(PARENT) if PARENT.isdigit() else None})
        if u.path == "/api/state":
            sc, ins = store.get_cache("scores"), store.get_cache("instruments")
            return _json(self, {"cli": _probe(), "scores": {"fetched_at": sc["fetched_at"], "count": len(sc["items"])},
                                "instruments": {"fetched_at": ins["fetched_at"], "count": len(ins["items"])}})
        if u.path == "/api/scores":
            items = [dict(it) for it in _build_items()]
            dur = store.get_durations(); rec = {(x.get("key") or x["title"]): x["ts"] for x in store.get_recent()}
            for it in items:
                it["duration"] = dur.get(it["title"])
                it["lastPlayed"] = rec.get(it["key"]) or (rec.get(it["title"]) if it["dupNo"] == 1 else None)   # 채번 전 기록('제목')은 첫 악보에
            found = lib.search(items, q.get("q", ""))
            if q.get("view") == "recent":
                found = sorted([it for it in found if it["lastPlayed"]], key=lambda it: -it["lastPlayed"])
            if q.get("initial"):
                found = [it for it in found if it["initial"] == q["initial"]]
            if q.get("artist"):                       # artistKey (수동 id 또는 'auto:…')
                found = [it for it in found if it["artistKey"] == q["artist"]]
            if q.get("bucket"):                       # 'other' = 미분류
                found = [it for it in found if it["bucket"] == q["bucket"]]
            return _json(self, {"items": found, "summary": lib.summary(items)})
        if u.path == "/api/artists":
            items = _build_items()
            return _json(self, {"artists": lib.artists_summary(items), "other": sum(1 for it in items if it["bucket"] == "other"),
                                "state": store.get_artists()})
        if u.path == "/api/normalize":
            # 정규화 검토용: 제목 → 정리·분리·규칙 을 전부 보여 준다
            items = _build_items()
            return _json(self, {"summary": lib.summary(items),
                                "rows": [{k: it[k] for k in ("title", "cleaned", "removed", "tags", "variant", "artist", "song", "rule", "bucket")} for it in items]})
        if u.path == "/api/instruments":
            return _json(self, {"items": lib.build_instruments(store.get_cache("instruments")["items"])})
        if u.path == "/api/playlists":
            return _json(self, _lists())
        if u.path == "/api/activity":
            if not self._guard():   # CLI 를 실행시키는 GET 이라 출처 검사
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, _activity())
        if u.path == "/api/ensemble":   # 합주 인식용 주변 플레이어 연주 상태 (읽기 전용)
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, _ensemble())
        if u.path == "/api/hold":   # 창이 살아 있는 동안 열어 두는 연결 (경량판 종료 판정). 5초마다 한 바이트를 보내 끊김을 감지
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            global _holds, _had_hold, _last_hold_close
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            with _hold_lock:
                _holds += 1; _had_hold = True
            try:
                while True:
                    self.wfile.write(b".")
                    self.wfile.flush()
                    time.sleep(5.0)
            except (OSError, ValueError):
                pass
            finally:
                with _hold_lock:
                    _holds -= 1; _last_hold_close = time.time()
            return
        if u.path == "/api/log":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, {"items": _log[:40]})
        if u.path == "/api/settings":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            # nocli=1: 설정 창을 열 때 — CLI 상태는 /api/state 가 이미 주기적으로 주므로 다시 묻지 않는다 (게임이 꺼져 있으면 probe 가 5초 걸린다)
            return _json(self, {"settings": store.get_settings(), "cli": None if q.get("nocli") else _probe()})
        if u.path == "/api/update/check":
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            return _json(self, update_check())
        if u.path.startswith("/api/cli/"):
            # 읽기 전용 명령 디버그 통로 (필드 모양 확인용). 실행 명령은 막고, 다른 사이트의 <img>/fetch 로는 못 부르게 출처 검사
            if not self._guard():
                return _json(self, {"ok": False, "error": "forbidden"}, 403)
            command = u.path[len("/api/cli/"):]
            if not (command in ("status", "capabilities") or command.startswith("get_")):   # 읽기 명령만 (화이트리스트)
                return _json(self, {"ok": False, "error": "not_allowed"}, 403)
            return _json(self, _cli(command, q.get("body"), timeout=120).to_dict())
        if u.path.startswith("/api/"):
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        if u.path in ("/", "/index.html"):
            return self._serve_index()
        if u.path.endswith((".py", ".tmp", ".json", ".log")) or "/." in u.path:   # ui/ 아래에 없지만, 혹시 몰라 원천 차단
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        return super().do_GET()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _serve_index(self):
        """index.html 에 실행 토큰을 심고, 인라인 스크립트 해시로 CSP 를 건다 (외부 스크립트·인라인 핸들러 전부 차단)."""
        try:
            with open(os.path.join(UI_DIR, "index.html"), "rb") as f:
                html = f.read()
        except OSError:
            return _json(self, {"ok": False, "error": "ui_missing"}, 500)
        # 브라우저는 인라인 스크립트를 LF 로 정규화한 뒤 CSP 해시를 잰다 → CRLF 로 체크아웃된 파일이면
        # 해시가 어긋나 스크립트가 통째로 차단된다(core.autocrlf=true 인 Windows 클론). 서빙 전에 맞춘다.
        html = html.replace(b"\r\n", b"\n")
        html = html.replace(b"__MOBIFOLIO_TOKEN__", TOKEN.encode("ascii"))
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        if q.get("theme") in ("light", "dark"):   # 스크린샷·검토용: 저장된 선택과 무관하게 이번 로드만 테마 고정
            html = html.replace(b'<html lang="ko">', f'<html lang="ko" data-theme="{q["theme"]}">'.encode("ascii"), 1)
        hashes = []
        pos = 0
        while True:
            a = html.find(b"<script>", pos)
            if a < 0:
                break
            b = html.find(b"</script>", a)
            hashes.append("'sha256-" + base64.b64encode(hashlib.sha256(html[a + 8:b]).digest()).decode("ascii") + "'")
            pos = b + 9
        csp = ("default-src 'self'; script-src " + (" ".join(hashes) or "'none'") +
               "; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' http://127.0.0.1:*; font-src 'self'; "
               "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(html)

    def _guard(self) -> bool:
        """CSRF 방지: 브라우저의 다른 사이트가 보낸 폼/스크립트 요청을 거른다.
        (1) Host 가 우리 주소, (2) Origin 이 있으면 우리 출처, (3) UI/셸이 붙이는 X-Requested-With 헤더."""
        host = (self.headers.get("Host") or "").lower()
        origin = (self.headers.get("Origin") or "").lower()
        if host not in _ok_hosts:
            return False
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return False
        if self.headers.get("X-Requested-With") != "mobifolio":
            return False
        if TOKEN and not hmac.compare_digest(self.headers.get("X-MobiFolio-Token") or "", TOKEN):   # 배포판: 실행마다 다른 토큰 — 페이지(index.html)와 셸만 안다
            return False
        global _last_ui
        _last_ui = time.time()   # 살아 있는 UI 의 심장박동
        return True

    def do_POST(self):
        try:
            self._post()
        except Exception as e:   # 어떤 입력에도 연결을 끊지 않고 JSON 으로 답한다
            import traceback; traceback.print_exc()
            try:
                _json(self, {"ok": False, "error": "internal", "message": type(e).__name__}, 500)
            except Exception:
                pass

    def _post(self):
        u = urlparse(self.path)
        if u.path == "/api/bye":   # 페이지 닫힘 신호 (sendBeacon 은 헤더를 못 붙이므로 토큰은 본문으로)
            p = _read_json(self)
            if not TOKEN or hmac.compare_digest(str(p.get("token") or ""), TOKEN):
                global _bye_at
                _bye_at = time.time()
            return _json(self, {"ok": True})
        if not self._guard():
            return _json(self, {"ok": False, "error": "forbidden", "message": "허용되지 않은 출처의 요청입니다."}, 403)
        p = _read_json(self)
        if "_error" in p:
            return _json(self, {"ok": False, "error": "bad_request", "message": p["_error"]}, 400)
        if u.path == "/api/quit":     # 셸이 창을 닫을 때 정상 종료 요청 (taskkill 대신 → PyInstaller 임시폴더 정리됨)
            _json(self, {"ok": True})
            threading.Thread(target=_shutdown, daemon=True).start()
            return
        if u.path == "/api/sync":
            return _json(self, sync())
        if u.path == "/api/play":
            # 제목·악기 이름은 게임이 준 문자열 그대로 (끝에 공백이 있는 이름이 실제로 있다 — strip 하면 못 찾는다)
            t, inst = p.get("title"), p.get("instrument")
            t = t if isinstance(t, str) else _s(t)
            inst = inst if isinstance(inst, str) and inst.strip() else None
            k = p.get("key")
            return _json(self, play(t, inst, k if isinstance(k, str) else ""))
        if u.path == "/api/stop":
            return _json(self, stop())
        if u.path == "/api/playlists":
            r = lists_op(_s(p.get("op")), p)
            if not r.get("ok"):
                r = {**_lists(), **r}   # 실패해도 UI 가 목록 상태를 잃지 않게
            return _json(self, r, 200 if r.get("ok") else 400)
        if u.path == "/api/artists":
            r = artists_op(_s(p.get("op")), p)
            if not r.get("ok"):
                r = {**store.get_artists(), **r}
            return _json(self, r, 200 if r.get("ok") else 400)
        if u.path == "/api/cli_test":   # 설정 창 「연결 확인」: 저장하지 않고 그 경로로 status 만 호출
            exe = _s(p.get("cli_exe"))
            if exe and not cli.valid_cli_path(exe):
                return _json(self, {"ok": False, "error": "bad_cli_exe", "message": "로컬 드라이브의 MabinogiMobile_CLI.exe 절대 경로가 아닙니다."})
            with _cli_lock:
                old = cli._override
                try:
                    cli.set_exe_override(exe)
                    res = cli.probe()
                finally:
                    cli.set_exe_override(old)
            return _json(self, {"ok": True, "cli": res})
        if u.path == "/api/update/apply":
            return _json(self, update_apply(p))
        if u.path == "/api/settings":
            patch = p.get("settings")
            if not isinstance(patch, dict):
                return _json(self, {"ok": False, "error": "bad_request", "message": "settings 는 객체여야 합니다."}, 400)
            uu = patch.get("update_url")
            if isinstance(uu, str) and uu.strip() and not _safe_url(uu):
                return _json(self, {"ok": False, "error": "bad_update_url", "message": "업데이트 주소는 https:// 로 시작해야 합니다."}, 400)
            exe = patch.get("cli_exe")
            if isinstance(exe, str) and exe.strip() and not cli.valid_cli_path(exe):
                return _json(self, {"ok": False, "error": "bad_cli_exe", "message": "CLI 경로는 로컬 드라이브의 MabinogiMobile_CLI.exe 절대 경로여야 합니다."}, 400)
            before = store.get_settings().get("cli_exe")
            s = store.set_settings(patch)
            cli.set_exe_override(s.get("cli_exe"))
            return _json(self, {"ok": True, "settings": s, "cli": _probe() if s.get("cli_exe") != before else None})   # 경로가 바뀐 때만 다시 확인 (저장이 5초 걸리지 않게)
        return _json(self, {"ok": False, "error": "not_found"}, 404)


def _find_app_browser() -> str | None:
    """앱 모드(주소창·탭 없는 독립 창)를 지원하는 크로미엄 계열 브라우저. Edge 는 Windows 10/11 기본 내장."""
    cands = []
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        base = os.environ.get(env, "")
        if base:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def _launch_app_window(url: str):
    """Edge/Chrome 앱 창(주소창 없음, 전용 프로필)을 띄우고 프로세스 핸들을 돌려준다. 없으면 기본 브라우저 탭(None)."""
    global _app_profile
    exe = _find_app_browser()
    if exe:
        profile = os.path.join(BASE, _APP_PROFILE)
        _app_profile = profile
        args = [exe, f"--app={url}", f"--window-size={_window_size()}", f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-features=TranslateUI,msEdgeStartupBoost",
                # 최소화·가림 상태에서도 페이지 타이머(재생 감시 1초 폴링)가 늦춰지지 않게
                "--disable-background-timer-throttling", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"]
        try:
            import subprocess
            return subprocess.Popen(args, creationflags=0x00000008)   # DETACHED_PROCESS
        except OSError:
            pass
    import webbrowser
    webbrowser.open(url)
    return None


def _open_window() -> None:
    """브라우저 탭이 아니라 앱 창으로 연다. 경량판은 창이 닫히면(브라우저 프로세스 종료) 서버도 끝낸다."""
    if os.environ.get("MABI_NO_BROWSER"):
        return
    url = f"http://127.0.0.1:{PORT}"

    if LITE_REUSE:
        global _app_profile
        _app_profile = os.path.join(BASE, _APP_PROFILE)   # 창은 이미 떠 있고(페이지가 새 포트로 이동) 감시만 이어간다
    else:
        threading.Thread(target=lambda: _launch_app_window(url), daemon=True).start()
    if LITE:
        threading.Thread(target=_lite_watchdog, daemon=True).start()


_last_ui = time.time()   # UI 가 마지막으로 요청한 시각
_bye_at = 0.0            # 페이지가 닫히며 보낸 신호 시각
_start_at = time.time()
_hold_lock = threading.Lock()
_holds = 0               # 열려 있는 /api/hold 연결 수 (= 살아 있는 창 수)
_had_hold = False
_last_hold_close = 0.0


_app_profile = ""        # 앱 창을 띄운 브라우저의 전용 프로필 경로 ("" = 기본 브라우저 탭으로 열었음)


def _app_window_alive() -> bool | None:
    """전용 프로필로 띄운 Edge/Chrome 창이 아직 있는가. 앱 창 모드가 아니면 None(판단 불가).
    Edge 는 한동안 안 쓴 창을 절전(슬리핑 탭)시키며 연결을 끊을 수 있어, 연결 끊김만으로는 '창 닫힘'을 단정할 수 없다 (실측).
    Chromium 은 프로필마다 제목이 프로필 경로인 메시지 전용 창(클래스 Chrome_MessageWindow)을 하나 둔다 — 그 창을 user32 로 찾는다.
    (예전엔 PowerShell 로 프로세스 명령줄을 뒤졌는데, 백신이 'PowerShell 실행' 행동으로 오탐하는 요인이라 뺐다.)"""
    if not _app_profile:
        return None
    try:
        import ctypes
        from ctypes import wintypes as w
        u = ctypes.windll.user32
        u.FindWindowExW.argtypes = [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]; u.FindWindowExW.restype = w.HWND
        u.GetWindowTextLengthW.argtypes = [w.HWND]; u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        want = os.path.normcase(os.path.normpath(_app_profile))
        h = None
        for _ in range(4096):
            h = u.FindWindowExW(w.HWND(-3), h, "Chrome_MessageWindow", None)   # HWND_MESSAGE 아래의 메시지 전용 창들
            if not h:
                break
            n = u.GetWindowTextLengthW(h)
            if n <= 0:
                continue
            buf = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(h, buf, n + 1)
            if os.path.normcase(os.path.normpath(buf.value)) == want:
                return True
        else:
            return None   # 상한까지 뒤졌는데 끝을 못 봄 → 판단 불가 (닫힘으로 단정하지 않는다)
        return False
    except Exception as e:
        print(f"[lite] window check failed: {e}", flush=True)
        return None


def _lite_watchdog() -> None:
    """경량판 종료 판정.
    1) 페이지가 /api/hold 연결을 계속 열어 둔다 — 창이 닫히거나 브라우저가 죽으면 끊긴다 (타이머와 무관, 최소화해도 유지).
    2) 연결이 끊겨도 바로 끝내지 않고, 전용 프로필의 브라우저 프로세스가 아직 있으면(절전된 창) 살아 있는 것으로 본다.
       프로세스까지 없어졌을 때만 종료. 앱 창 모드가 아니면(기본 브라우저 탭) 연결이 60초 이상 없을 때 종료.
    3) 창이 90초 안에 한 번도 붙지 않으면(브라우저를 못 띄운 경우) 고아로 남지 않게 끝낸다."""
    last_check = 0.0; misses = 0
    while True:
        time.sleep(1.0)
        now = time.time()
        with _hold_lock:
            h, had, lc = _holds, _had_hold, _last_hold_close
        gone_for = now - max(lc, _bye_at) if (had or _bye_at) else 0.0
        if (had or _bye_at) and h == 0 and gone_for > 4.0 and now - last_check >= 5.0:
            last_check = now
            alive = _app_window_alive()
            misses = misses + 1 if alive is False else 0
            if misses >= 2:   # 5초 간격 2회 연속 없음 (Edge 가 스스로 재시작하는 짧은 구간에 오판하지 않게)
                _say("[lite] window closed — exiting"); _shutdown()
            if alive is None and gone_for > 60.0:
                _say("[lite] no window connection for 60s — exiting"); _shutdown()
        if not had and now - _start_at > 90.0:
            _say("[lite] no window attached in 90s — exiting"); _shutdown()


_mutex = None


def _lite_release() -> None:
    """뮤텍스와 lite.json 을 놓는다 (업데이트로 새 프로세스에 자리를 넘길 때)."""
    global _mutex
    try:
        if _mutex:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_mutex); _mutex = None
        os.remove(LITE_FILE)
    except OSError:
        pass


def _lite_single_instance() -> bool:
    """경량판 중복 실행: 이미 떠 있으면 그 포트로 창만 하나 더 열고 False. 처음이면 lite.json 에 포트를 적고 True."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        global _mutex
        _mutex = k32.CreateMutexW(None, False, "Local\\MobiFolioLite" + UI_NAME)
        if k32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            try:
                with open(LITE_FILE, encoding="utf-8") as f:
                    port = int(json.load(f).get("port", 0))
                import urllib.request
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2).read()
                _launch_app_window(f"http://127.0.0.1:{port}")
                return False
            except Exception:
                pass   # 죽은 기록이면 그냥 새로 뜬다
        with open(LITE_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": PORT, "pid": os.getpid()}, f)
    except Exception as e:
        print(f"[lite] single-instance check failed: {e}", flush=True)
    return True


_open_browser = _open_window   # 이전 이름 호환


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False   # 같은 사용자의 다른 프로세스가 포트를 가로채지 못하게 (SO_REUSEADDR 끔 + 배타 사용)
    daemon_threads = True
    _slots = threading.BoundedSemaphore(32)   # 동시 연결 상한 — 느린 연결로 스레드를 무한히 잡아 두지 못하게

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.close()
            except OSError:
                pass
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def _reexec_if_inherited_mei() -> None:
    """옛 버전(0.1.x)이 업데이트로 우리를 띄울 때 PyInstaller 내부 변수를 물려줬으면, 우리는 옛 임시 폴더를 빌려 쓰는 상태다
    (곧 삭제되어 화면 파일이 사라진다). 그 경우 깨끗한 환경으로 자신을 다시 실행하고 끝난다."""
    if not (FROZEN and LITE_REUSE):
        return
    old_mei = os.environ.get("MABI_OLD_MEI", ""); mine = getattr(sys, "_MEIPASS", "")
    if not (old_mei and mine and os.path.normcase(os.path.abspath(old_mei)) == os.path.normcase(os.path.abspath(mine))):
        return
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEI"))}
    try:
        import subprocess
        subprocess.Popen([sys.executable], cwd=os.path.dirname(sys.executable), env=env, close_fds=True,
                         creationflags=0x00000008 | 0x00000200 | 0x01000000)
        _say("update: inherited temp dir detected — re-executing cleanly")
    except OSError as e:
        _say(f"update: re-exec failed: {e}")
        return
    os._exit(0)


def main() -> None:
    global _srv
    _reexec_if_inherited_mei()
    os.makedirs(store.DATA_DIR, exist_ok=True)
    if LITE and not _lite_single_instance():
        return
    try:
        srv = _Server(("127.0.0.1", PORT), H)
        _srv = srv
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 10048):
            # 이미 떠 있음(포트 사용 중) → 창만 다시 연다
            print(f"[mobifolio] port {PORT} busy — opening browser only", flush=True)
            _open_browser(); time.sleep(1.5)
            return
        print(f"[mobifolio] port {PORT} bind failed: {e} (errno {e.errno}) — 예약 포트(Hyper-V/WinNAT)일 수 있습니다", flush=True)
        sys.exit(1)
    _say(f"[mobifolio] http://127.0.0.1:{PORT}  cli={cli.find_exe()}  frozen={FROZEN} lite={LITE} reuse={LITE_REUSE} pid={os.getpid()} v{VERSION}")
    _cleanup_bak()
    _watch_parent()
    _open_browser()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

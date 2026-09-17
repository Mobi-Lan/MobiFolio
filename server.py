"""로컬 웹 서버 (127.0.0.1:19997). UI 는 ui/index.html, API 는 /api/*. 표준 라이브러리만 쓴다.

동기화: POST /api/sync → get_instruments, get_music_scores 를 CLI 로 받아 data/ 에 저장 (+ fixtures/ 원본).
재생:   POST /api/play {"title": DisplayTitle, "instrument": Name|null} → change_instrument → play_music_score.
정지:   POST /api/stop → get_activity 로 MainButtonState 확인 후 stop_action (invalid_state 는 짧게 재시도).
"""
from __future__ import annotations

import io
import json
import re
import os
import sys
import time
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("MABI_DATA_DIR") or (os.path.dirname(sys.executable) if FROZEN else HERE)   # 데이터·로그 위치
RES = getattr(sys, "_MEIPASS", HERE)                               # 묶인 리소스(ui/) 위치
if FROZEN:
    # --noconsole 이면 stdout 이 없다 → 로그를 exe 옆 파일로
    _logp = os.path.join(BASE, "mabi-scorebox.log")
    try:
        if os.path.getsize(_logp) > 2_000_000:   # 무한 성장 방지: 2MB 넘으면 새로 시작
            os.remove(_logp)
    except OSError:
        pass
    _logf = open(_logp, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _logf
elif sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, HERE)
import cli_transport as cli   # noqa: E402
import library as lib         # noqa: E402
import store                  # noqa: E402

PORT = int(os.environ.get("MABI_PLAYLIST_PORT", "19997"))
VERSION = "0.1.0"
_srv = None   # ThreadingHTTPServer (종료용)


def _shutdown() -> None:
    time.sleep(0.2)
    try:
        if _srv:
            _srv.shutdown()
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
    handler.send_header("Cache-Control", "no-store")
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
        store.save_fixture(command, r.body)
    return out


# ── 재생목록 ──
def lists_op(op: str, p: dict) -> dict:
    d = store.get_lists()
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
                have = {it["title"] for it in pl["items"]}
                for t in _titles(p.get("titles")):
                    if t not in have:
                        pl["items"].append({"title": t, "inst": ""}); have.add(t)
                pl["updated"] = now
    elif op == "remove":
        rm = set(_titles(p.get("titles")))
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["items"] = [it for it in pl["items"] if it["title"] not in rm]; pl["updated"] = now
    elif op == "reorder":   # items = 제목 순서 배열
        for pl in pls:
            if pl["id"] == p.get("id"):
                by = {it["title"]: it for it in pl["items"]}
                order = [by[t] for t in _titles(p.get("items")) if t in by]
                seen = {it["title"] for it in order}
                pl["items"] = order + [it for it in pl["items"] if it["title"] not in seen]; pl["updated"] = now
    elif op == "set_inst":   # 곡별 악기. title 없으면 목록 전체
        for pl in pls:
            if pl["id"] == p.get("id"):
                for it in pl["items"]:
                    if not _s(p.get("title")) or it["title"] == p.get("title"):
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
        if al and al not in a["aliases"]:
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


def play(title: str, instrument: str | None) -> dict:
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
    insts = [lib.pick(x, lib.NAME_KEYS) for x in store.get_cache("instruments")["items"] if isinstance(x, dict)]
    if inst and insts and inst not in insts:
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
        r = _cli("change_instrument", {"name": inst}, timeout=120)
        out["steps"].append(r.to_dict()); _note(r, f"악기 → {inst}")
        if not r.ok:
            out["ok"] = False
            return out
    for attempt in range(4):   # 정지 직후 상태 전이 중이면 invalid_state → 짧게 재시도 (다음 곡으로 건너뛰지 않게)
        r = _cli("play_music_score", {"title": title}, timeout=660)
        out["steps"].append(r.to_dict()); _note(r, f"재생 · {title}" + (f" (재시도 {attempt})" if attempt else ""))
        if r.ok or r.error != "invalid_state":
            break
        time.sleep(0.8 + 0.4 * attempt)
    out["ok"] = r.ok
    if r.ok:
        _last_play.update({"title": title, "inst": inst or ""})
        store.push_recent(title, inst or "")
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
    def __init__(self, *a, **k):
        super().__init__(*a, directory=UI_DIR, **k)

    def log_message(self, fmt, *args):   # 조용히
        pass

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/api/health":   # 일렉트론 셸이 "이 포트가 정말 악보함인지" 확인하는 용도
            return _json(self, {"app": "scorebox", "version": VERSION, "pid": os.getpid(), "data_dir": store.DATA_DIR, "frozen": FROZEN})
        if u.path == "/api/state":
            sc, ins = store.get_cache("scores"), store.get_cache("instruments")
            return _json(self, {"cli": cli.probe(), "scores": {"fetched_at": sc["fetched_at"], "count": len(sc["items"])},
                                "instruments": {"fetched_at": ins["fetched_at"], "count": len(ins["items"])}})
        if u.path == "/api/scores":
            items = lib.build(store.get_cache("scores")["items"], store.get_artists())
            dur = store.get_durations(); rec = {x["title"]: x["ts"] for x in store.get_recent()}
            for it in items:
                it["duration"] = dur.get(it["title"]); it["lastPlayed"] = rec.get(it["title"])
            found = lib.search(items, q.get("q", ""))
            if q.get("view") == "recent":
                seen: set[str] = set()
                found = [it for it in sorted([it for it in found if it["lastPlayed"]], key=lambda it: -it["lastPlayed"])
                         if not (it["title"] in seen or seen.add(it["title"]))]
            if q.get("initial"):
                found = [it for it in found if it["initial"] == q["initial"]]
            if q.get("artist"):                       # artistKey (수동 id 또는 'auto:…')
                found = [it for it in found if it["artistKey"] == q["artist"]]
            if q.get("bucket"):                       # 'other' = 미분류
                found = [it for it in found if it["bucket"] == q["bucket"]]
            return _json(self, {"items": found, "summary": lib.summary(items)})
        if u.path == "/api/artists":
            items = lib.build(store.get_cache("scores")["items"], store.get_artists())
            return _json(self, {"artists": lib.artists_summary(items), "other": sum(1 for it in items if it["bucket"] == "other"),
                                "state": store.get_artists()})
        if u.path == "/api/normalize":
            # 정규화 검토용: 제목 → 정리·분리·규칙 을 전부 보여 준다
            items = lib.build(store.get_cache("scores")["items"], store.get_artists())
            return _json(self, {"summary": lib.summary(items),
                                "rows": [{k: it[k] for k in ("title", "cleaned", "removed", "tags", "variant", "artist", "song", "rule", "bucket")} for it in items]})
        if u.path == "/api/instruments":
            return _json(self, {"items": lib.build_instruments(store.get_cache("instruments")["items"])})
        if u.path == "/api/playlists":
            return _json(self, store.get_lists())
        if u.path == "/api/activity":
            return _json(self, _activity())
        if u.path == "/api/log":
            return _json(self, {"items": _log[:40]})
        if u.path == "/api/settings":
            return _json(self, {"settings": store.get_settings(), "cli": cli.probe()})
        if u.path.startswith("/api/cli/"):
            # 읽기 전용 명령 디버그 통로 (필드 모양 확인용). 실행 명령은 막는다.
            command = u.path[len("/api/cli/"):]
            if command.startswith(("execute_", "complete_", "write_", "play_", "change_", "stop_", "stand_")):
                return _json(self, {"ok": False, "error": "not_allowed"}, 403)
            return _json(self, _cli(command, q.get("body"), timeout=120).to_dict())
        if u.path.startswith("/api/"):
            return _json(self, {"ok": False, "error": "not_found"}, 404)
        if u.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def _guard(self) -> bool:
        """CSRF 방지: 브라우저의 다른 사이트가 보낸 폼/스크립트 요청을 거른다.
        (1) Host 가 우리 주소, (2) Origin 이 있으면 우리 출처, (3) UI/셸이 붙이는 X-Requested-With 헤더."""
        host = (self.headers.get("Host") or "").lower()
        origin = (self.headers.get("Origin") or "").lower()
        if host not in _ok_hosts:
            return False
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return False
        return self.headers.get("X-Requested-With") == "scorebox"

    def do_POST(self):
        try:
            self._post()
        except Exception as e:   # 어떤 입력에도 연결을 끊지 않고 JSON 으로 답한다
            import traceback; traceback.print_exc()
            try:
                _json(self, {"ok": False, "error": "internal", "message": f"{type(e).__name__}: {e}"}, 500)
            except Exception:
                pass

    def _post(self):
        u = urlparse(self.path)
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
            return _json(self, play(_s(p.get("title")), _s(p.get("instrument")) or None))
        if u.path == "/api/stop":
            return _json(self, stop())
        if u.path == "/api/playlists":
            r = lists_op(_s(p.get("op")), p)
            if not r.get("ok"):
                r = {**store.get_lists(), **r}   # 실패해도 UI 가 목록 상태를 잃지 않게
            return _json(self, r, 200 if r.get("ok") else 400)
        if u.path == "/api/artists":
            r = artists_op(_s(p.get("op")), p)
            if not r.get("ok"):
                r = {**store.get_artists(), **r}
            return _json(self, r, 200 if r.get("ok") else 400)
        if u.path == "/api/settings":
            patch = p.get("settings")
            if not isinstance(patch, dict):
                return _json(self, {"ok": False, "error": "bad_request", "message": "settings 는 객체여야 합니다."}, 400)
            exe = patch.get("cli_exe")
            if isinstance(exe, str) and exe.strip():
                e = exe.strip()
                if not (os.path.isabs(e) and e.lower().endswith(".exe") and os.path.isfile(e)):
                    return _json(self, {"ok": False, "error": "bad_cli_exe", "message": "CLI 경로는 존재하는 .exe 의 절대 경로여야 합니다."}, 400)
            s = store.set_settings(patch)
            cli.set_exe_override(s.get("cli_exe"))
            return _json(self, {"ok": True, "settings": s, "cli": cli.probe()})
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


def _open_window() -> None:
    """브라우저 탭이 아니라 앱 창으로 연다. 전용 프로필을 써서 사용자의 Edge 세션과 섞이지 않는다."""
    if os.environ.get("MABI_NO_BROWSER"):
        return
    url = f"http://127.0.0.1:{PORT}"

    def go():
        exe = _find_app_browser()
        if exe:
            profile = os.path.join(BASE, ".appwindow-profile")
            args = [exe, f"--app={url}", "--window-size=1280,860", f"--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check", "--disable-features=TranslateUI"]
            try:
                import subprocess
                subprocess.Popen(args, creationflags=0x00000008)   # DETACHED_PROCESS
                return
            except OSError:
                pass
        import webbrowser
        webbrowser.open(url)

    threading.Timer(0.6, go).start()


_open_browser = _open_window   # 이전 이름 호환


def main() -> None:
    global _srv
    os.makedirs(store.DATA_DIR, exist_ok=True)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
        _srv = srv
    except OSError:
        # 이미 떠 있음(포트 사용 중) → 창만 다시 연다
        print(f"[mabi-playlist] port {PORT} busy — opening browser only", flush=True)
        _open_browser(); time.sleep(1.5)
        return
    print(f"[mabi-playlist] http://127.0.0.1:{PORT}  cli={cli.find_exe()}  frozen={FROZEN}", flush=True)
    _watch_parent()
    _open_browser()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

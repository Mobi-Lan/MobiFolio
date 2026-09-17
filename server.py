"""로컬 웹 서버 (127.0.0.1:19997). UI 는 ui/index.html, API 는 /api/*. 표준 라이브러리만 쓴다.

동기화: POST /api/sync → get_instruments, get_music_scores 를 CLI 로 받아 data/ 에 저장 (+ fixtures/ 원본).
재생:   POST /api/play {"title": DisplayTitle, "instrument": Name|null} → change_instrument → play_music_score.
정지:   POST /api/stop → get_activity 로 MainButtonState 확인 후 stop_action (invalid_state 는 짧게 재시도).
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(sys.executable) if FROZEN else HERE        # 데이터·로그 위치 (exe 옆)
RES = getattr(sys, "_MEIPASS", HERE)                               # 묶인 리소스(ui/) 위치
if FROZEN:
    # --noconsole 이면 stdout 이 없다 → 로그를 exe 옆 파일로
    _logf = open(os.path.join(BASE, "mabi-scorebox.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _logf
elif sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, HERE)
import cli_transport as cli   # noqa: E402
import library as lib         # noqa: E402
import store                  # noqa: E402

PORT = int(os.environ.get("MABI_PLAYLIST_PORT", "19997"))
UI_DIR = os.path.join(RES, "ui")
_cli_lock = threading.Lock()   # CLI 는 한 번에 하나만 (게임 파이프 직렬)


def _json(handler, obj, status=200):
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler) -> dict:
    n = int(handler.headers.get("Content-Length") or 0)
    if n <= 0:
        return {}
    raw = handler.rfile.read(n)
    for enc in ("utf-8", "mbcs"):   # 브라우저는 utf-8. 콘솔 도구가 cp949 로 보내도 조용히 빈 값이 되지 않게
        try:
            return json.loads(raw.decode(enc))
        except Exception:
            continue
    return {"_decode_error": True}


def _cli(command, body=None, timeout=660.0):
    with _cli_lock:
        return cli.call(command, body, timeout)


# ── 동기화 ──
def sync() -> dict:
    out = {"ok": True, "steps": []}
    st = _cli("status", timeout=20)
    out["steps"].append(st.to_dict())
    if not st.ok:
        out["ok"] = False
        return out
    for command, kind in (("get_instruments", "instruments"), ("get_music_scores", "scores")):
        r = _cli(command, "", timeout=120)   # 빈 필터 = 전체
        out["steps"].append({**r.to_dict(), "body": None, "count": (len(r.body) if isinstance(r.body, list) else None)})
        if not r.ok:
            out["ok"] = False
            continue
        items = r.body if isinstance(r.body, list) else (r.body.get("items") if isinstance(r.body, dict) else [])
        store.set_cache(kind, items or [])
        store.save_fixture(command, r.body)
    return out


# ── 재생목록 ──
def lists_op(op: str, p: dict) -> dict:
    d = store.get_lists()
    pls, fds = d["playlists"], d["folders"]
    now = time.time()
    if op == "folder_create":
        f = {"id": store.new_id(), "name": (p.get("name") or "새 폴더").strip(), "parent": p.get("parent") or None, "created": now}
        fds.append(f)
    elif op == "folder_rename":
        for f in fds:
            if f["id"] == p.get("id"):
                f["name"] = (p.get("name") or f["name"]).strip()
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
        pl = {"id": store.new_id(), "name": (p.get("name") or "새 재생목록").strip(), "folder": p.get("folder") or None,
              "items": [], "created": now, "updated": now}
        pls.append(pl)
    elif op == "rename":
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["name"] = (p.get("name") or pl["name"]).strip(); pl["updated"] = now
    elif op == "move":
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["folder"] = p.get("folder") or None; pl["updated"] = now
    elif op == "delete":
        d["playlists"] = [pl for pl in pls if pl["id"] != p.get("id")]
    elif op == "add":
        for pl in pls:
            if pl["id"] == p.get("id"):
                for t in p.get("titles") or []:
                    if t and t not in pl["items"]:
                        pl["items"].append(t)
                pl["updated"] = now
    elif op == "remove":
        for pl in pls:
            if pl["id"] == p.get("id"):
                pl["items"] = [t for t in pl["items"] if t not in set(p.get("titles") or [])]; pl["updated"] = now
    elif op == "reorder":
        for pl in pls:
            if pl["id"] == p.get("id"):
                order = [t for t in (p.get("items") or []) if t in pl["items"]]
                pl["items"] = order + [t for t in pl["items"] if t not in order]; pl["updated"] = now
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

    if op == "create":
        ensure(p.get("name") or "이름 없음")
    elif op == "rename":
        a = by_id.get(p.get("id"))
        if a and (p.get("name") or "").strip():
            a["name"] = p["name"].strip()
    elif op == "alias":
        a = by_id.get(p.get("id"))
        al = (p.get("alias") or "").strip()
        if a and al and al not in a["aliases"]:
            a["aliases"].append(al)
    elif op == "merge":   # from → into : from 의 이름은 into 의 별칭으로, 지정도 옮긴다
        src, dst = by_id.get(p.get("from")), by_id.get(p.get("into"))
        if src and dst and src is not dst:
            dst["aliases"] = list(dict.fromkeys(dst["aliases"] + [src["name"]] + src.get("aliases", [])))
            for t, aid in list(d["assign"].items()):
                if aid == src["id"]:
                    d["assign"][t] = dst["id"]
            d["artists"] = [a for a in arts if a["id"] != src["id"]]
    elif op == "assign":   # titles[] → id 또는 name(없으면 생성). 자동 추출 키('auto:…')를 넘기면 그 이름으로 생성
        aid = p.get("id")
        if not aid or aid not in by_id:
            aid = ensure(p.get("name") or "")
        for t in p.get("titles") or []:
            if t:
                d["assign"][t] = aid
    elif op == "unassign":
        for t in p.get("titles") or []:
            d["assign"].pop(t, None)
    elif op == "delete":
        aid = p.get("id")
        d["artists"] = [a for a in arts if a["id"] != aid]
        d["assign"] = {t: v for t, v in d["assign"].items() if v != aid}
    elif op == "noise":   # 잡음어 추가/제거
        w = (p.get("word") or "").strip().lower()
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
def play(title: str, instrument: str | None) -> dict:
    out = {"ok": True, "steps": []}
    if not (title or "").strip():
        return {"ok": False, "steps": [], "error": "empty_title", "message": "재생할 악보 제목이 비어 있습니다 (요청 인코딩 확인)."}
    if instrument:
        r = _cli("change_instrument", {"name": instrument}, timeout=120)
        out["steps"].append(r.to_dict())
        if not r.ok:
            out["ok"] = False
            return out
    r = _cli("play_music_score", {"title": title}, timeout=660)
    out["steps"].append(r.to_dict())
    out["ok"] = r.ok
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
        out["steps"].append(r.to_dict())
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
        if u.path == "/api/state":
            sc, ins = store.get_cache("scores"), store.get_cache("instruments")
            return _json(self, {"cli": cli.probe(), "scores": {"fetched_at": sc["fetched_at"], "count": len(sc["items"])},
                                "instruments": {"fetched_at": ins["fetched_at"], "count": len(ins["items"])}})
        if u.path == "/api/scores":
            items = lib.build(store.get_cache("scores")["items"], store.get_artists())
            found = lib.search(items, q.get("q", ""))
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
            return _json(self, _cli("get_activity", timeout=30).to_dict())
        if u.path.startswith("/api/cli/"):
            # 읽기 전용 명령 디버그 통로 (필드 모양 확인용). 실행 명령은 막는다.
            command = u.path[len("/api/cli/"):]
            if command.startswith(("execute_", "complete_", "write_", "play_", "change_", "stop_", "stand_")):
                return _json(self, {"ok": False, "error": "not_allowed"}, 403)
            return _json(self, _cli(command, q.get("body"), timeout=120).to_dict())
        if u.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        p = _read_json(self)
        if u.path == "/api/sync":
            return _json(self, sync())
        if u.path == "/api/play":
            return _json(self, play(str(p.get("title", "")), p.get("instrument") or None))
        if u.path == "/api/stop":
            return _json(self, stop())
        if u.path == "/api/playlists":
            return _json(self, lists_op(str(p.get("op", "")), p))
        if u.path == "/api/artists":
            return _json(self, artists_op(str(p.get("op", "")), p))
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
    os.makedirs(store.DATA_DIR, exist_ok=True)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        # 이미 떠 있음(포트 사용 중) → 창만 다시 연다
        print(f"[mabi-playlist] port {PORT} busy — opening browser only", flush=True)
        _open_browser(); time.sleep(1.5)
        return
    print(f"[mabi-playlist] http://127.0.0.1:{PORT}  cli={cli.find_exe()}  frozen={FROZEN}", flush=True)
    _open_browser()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

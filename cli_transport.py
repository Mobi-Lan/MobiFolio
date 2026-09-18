"""MabinogiMobile_CLI 호출 계층. HANDOFF §1~§3 규칙을 그대로 코드로 옮겼다.

- 비ASCII body 는 통째로 UTF-8 → base64 → "base64:" 접두 (JSON body 도 통째로).
- stdout 은 바이트로 받아 utf-8 → mbcs 폴백으로 풀고 JSON 파서로 읽는다 (\\uXXXX 는 파서가 디코드).
- exit 0 이 성공이 아니다: ok = exit==0 and "error" not in body.
- status/capabilities 는 last-response.json 을 갱신하지 않는다 → stdout 만 본다 (capabilities 도 게임이 꺼져 있으면 game_off).
- 실행 명령은 최대 9분 블로킹 → 타임아웃 11분.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

CREATE_NO_WINDOW = 0x08000000
# 데모 모드: 게임·CLI 없이 demo/demo_cli.py 의 가짜 응답으로 UI 를 띄운다 (시연·스크린샷).
# 배포판(frozen)에서는 환경변수가 있어도 켜지지 않는다.
DEMO = os.environ.get("MABI_DEMO") == "1" and not getattr(sys, "frozen", False)
if DEMO:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo"))   # demo/demo_cli.py
DEMO_EXE = r"C:\Nexon\MabinogiMobile\MabinogiMobile_CLI.exe"
EXE_CANDIDATES = [
    os.environ.get("MABI_CLI_EXE", "") if not getattr(sys, "frozen", False) or os.environ.get("MABI_DEV") == "1" else "",   # 배포판은 개발용 환경변수 무시
    r"C:\Nexon\MabinogiMobile\MabinogiMobile_CLI.exe",
]   # PATH/현재 폴더 탐색(where)은 하지 않는다 — 앱 폴더나 PATH 에 심어 둔 가짜 exe 가 실행되지 않게. 다른 위치면 설정에서 지정
LAST_RESPONSE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MabinogiMobileCLI", "last-response.json")
CAPABILITIES_FILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MabinogiMobileCLI", "CAPABILITIES.json")
LOCAL_COMMANDS = {"status", "capabilities"}      # last-response.json 을 갱신하지 않는 명령 (폴백 생략)
EXIT_MEANING = {0: "ok", 2: "usage_error", 3: "canceled", 4: "unknown_command", 5: "disconnected"}


_override: str = ""   # 설정(data/settings.json)의 cli_exe — server 가 set_exe_override 로 넣는다
_CLI_BASENAME = "mabinogimobile_cli.exe"


def valid_cli_path(p) -> bool:
    """설정·환경변수로 들어온 CLI 경로가 실행해도 되는 모양인지: 로컬 드라이브 절대 경로, UNC·\\\\?\\ 아님,
    파일명이 MabinogiMobile_CLI.exe, 실제 파일. (설정에 아무 exe 나 넣어 실행시키는 것을 막는다)"""
    if not isinstance(p, str):
        return False
    p = p.strip()
    if len(p) < 4 or not (p[0].isascii() and p[0].isalpha() and p[1] == ":" and p[2] in "\\/"):
        return False
    if p.startswith("\\\\") or "\\?\\" in p or "\0" in p:
        return False
    if os.path.basename(p).lower() != _CLI_BASENAME:
        return False
    return os.path.isfile(p)


def set_exe_override(path) -> None:
    global _override
    _override = path.strip() if valid_cli_path(path) else ""


def find_exe() -> str | None:
    """존재하는 실행파일 경로. 설정 지정 > MABI_CLI_EXE > 기본 설치 경로."""
    if DEMO:
        return DEMO_EXE
    if _override and os.path.exists(_override):
        return _override
    for c in EXE_CANDIDATES:
        if not c:
            continue
        if c and valid_cli_path(c):
            return c
    return None


def _decode(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return b.decode("mbcs")
        except Exception:
            return b.decode("utf-8", "replace")


def encode_body(body: str | dict | list | None) -> str | None:
    """JSON body 는 \\uXXXX 이스케이프로 순수 ASCII 화한다 — 콘솔 코드페이지와 무관하고 base64 도 필요 없다
    (실측: play_music_score 에 {"title": "\\uc545\\ubcf4…"} → Play started). raw 문자열의 비ASCII 만 base64."""
    if body is None:
        return None
    if isinstance(body, (dict, list)):
        return json.dumps(body, ensure_ascii=True)
    s = body
    if s == "":
        return ""
    if all(ord(ch) < 128 for ch in s):
        return s
    return "base64:" + base64.b64encode(s.encode("utf-8")).decode("ascii")


@dataclass
class CliResult:
    command: str
    code: int
    body: object = None            # dict | list | None
    ok: bool = False
    error: str | None = None       # cli_not_found / exit 의미 / body.error
    message: str = ""
    raw: str = ""
    elapsed: float = 0.0
    source: str = "stdout"         # stdout | last-response

    def to_dict(self) -> dict:
        return {"command": self.command, "code": self.code, "ok": self.ok, "error": self.error,
                "message": self.message, "body": self.body, "elapsed": round(self.elapsed, 3), "source": self.source}


def _read_last_response(since: float) -> object | None:
    """이번 호출(since) 이후에 갱신된 파일만 믿는다 — 예전 다른 명령의 응답이 이번 결과로 둔갑하지 않게."""
    try:
        if os.path.getmtime(LAST_RESPONSE) < since - 1.0:
            return None
        with open(LAST_RESPONSE, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def call(command: str, body: str | dict | list | None = None, timeout: float = 660.0) -> CliResult:
    if DEMO:
        import demo_cli
        t0 = time.time()
        code, parsed = demo_cli.respond(command, body)
        res = CliResult(command, code, parsed, False, None, "",
                        json.dumps(parsed, ensure_ascii=False), time.time() - t0, "demo")
        if code != 0:
            res.error = EXIT_MEANING.get(code, f"exit_{code}")
        elif isinstance(parsed, dict) and "error" in parsed:
            res.error, res.message = str(parsed["error"]), str(parsed.get("message", ""))
        else:
            res.ok = True
            res.message = str(parsed.get("message", "")) if isinstance(parsed, dict) else ""
        return res
    exe = find_exe()
    if exe is None:
        return CliResult(command, -1, None, False, "cli_not_found",
                         "MabinogiMobile_CLI.exe 를 찾지 못했습니다. 게임의 환경 설정 > 게임 > AI 제어에서 '마비노기 모바일 AI 커넥터' 를 켜면 설치됩니다.")
    args = [exe, command]
    enc = encode_body(body)
    if enc is not None:
        args.append(enc)
    t0 = time.time()
    try:
        p = subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return CliResult(command, -2, None, False, "timeout", f"{timeout:.0f}s 안에 응답이 없습니다.", elapsed=time.time() - t0)
    except OSError as e:
        return CliResult(command, -3, None, False, "spawn_failed", str(e), elapsed=time.time() - t0)
    raw = _decode(p.stdout).strip()
    parsed: object | None = None
    source = "stdout"
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
    if parsed is None and command not in LOCAL_COMMANDS:
        lr = _read_last_response(t0)
        if lr is not None:
            parsed, source = lr, "last-response"
    res = CliResult(command, p.returncode, parsed, False, None, "", raw, time.time() - t0, source)
    if p.returncode != 0:
        res.error = EXIT_MEANING.get(p.returncode, f"exit_{p.returncode}")
        if isinstance(parsed, dict):
            res.message = str(parsed.get("message") or parsed.get("reason") or "")
        else:
            res.message = _decode(p.stderr).strip() or raw
        return res
    if isinstance(parsed, dict) and "error" in parsed:
        res.error = str(parsed["error"])
        res.message = str(parsed.get("message", ""))
        return res
    res.ok = True
    if isinstance(parsed, dict):
        res.message = str(parsed.get("message", ""))
    return res


def probe() -> dict:
    """UI 상태표시용: 실행파일 유무 + status."""
    exe = find_exe()
    out = {"exe": exe, "found": exe is not None, "pipe": None, "reason": None,
           "override_invalid": bool(_override) and not os.path.exists(_override)}   # 설정 경로가 틀려 기본 경로로 폴백 중이면 알려 준다
    if exe:
        r = call("status", timeout=20)
        if isinstance(r.body, dict):
            out["pipe"] = r.body.get("pipe")
            out["reason"] = r.body.get("reason")
        out["status_error"] = r.error
    return out

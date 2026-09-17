"""MabinogiMobile_CLI 호출 계층. HANDOFF §1~§3 규칙을 그대로 코드로 옮겼다.

- 비ASCII body 는 통째로 UTF-8 → base64 → "base64:" 접두 (JSON body 도 통째로).
- stdout 은 바이트로 받아 utf-8 → mbcs 폴백으로 풀고 JSON 파서로 읽는다 (\\uXXXX 는 파서가 디코드).
- exit 0 이 성공이 아니다: ok = exit==0 and "error" not in body.
- status/capabilities 는 로컬 명령이라 last-response.json 을 갱신하지 않는다 → stdout 만 본다.
- 실행 명령은 최대 9분 블로킹 → 타임아웃 11분.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from dataclasses import dataclass, field

CREATE_NO_WINDOW = 0x08000000
EXE_CANDIDATES = [
    os.environ.get("MABI_CLI_EXE", ""),
    r"C:\Nexon\MabinogiMobile\MabinogiMobile_CLI.exe",
    "MabinogiMobile_CLI",   # PATH 등록분 (셸 갱신 전이면 못 찾을 수 있다)
]
LAST_RESPONSE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MabinogiMobileCLI", "last-response.json")
CAPABILITIES_FILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "MabinogiMobileCLI", "CAPABILITIES.json")
LOCAL_COMMANDS = {"status", "capabilities"}      # 게임까지 안 가는 명령: last-response.json 미갱신
EXIT_MEANING = {0: "ok", 2: "usage_error", 3: "canceled", 4: "unknown_command", 5: "disconnected"}


_override: str = ""   # 설정(data/settings.json)의 cli_exe — server 가 set_exe_override 로 넣는다


def set_exe_override(path) -> None:
    global _override
    _override = path.strip() if isinstance(path, str) else ""


def find_exe() -> str | None:
    """존재하는 실행파일 경로. 설정 지정 > 기본 설치 경로 > PATH(where)."""
    if _override and os.path.exists(_override):
        return _override
    for c in EXE_CANDIDATES:
        if not c:
            continue
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
            continue
        try:
            r = subprocess.run(["where", c], capture_output=True, stdin=subprocess.DEVNULL, timeout=5, creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0 and r.stdout.strip():
                return _decode(r.stdout).splitlines()[0].strip()
        except Exception:
            pass
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
    exe = find_exe()
    if exe is None:
        return CliResult(command, -1, None, False, "cli_not_found",
                         "MabinogiMobile_CLI.exe 를 찾지 못했습니다. 인게임 'MM AI 에이전트 활성화' 토글을 켜면 설치됩니다.")
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
    out = {"exe": exe, "found": exe is not None, "pipe": None, "reason": None}
    if exe:
        r = call("status", timeout=20)
        if isinstance(r.body, dict):
            out["pipe"] = r.body.get("pipe")
            out["reason"] = r.body.get("reason")
        out["status_error"] = r.error
    return out

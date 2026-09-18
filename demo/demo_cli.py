"""데모 모드 — CLI 대역 (`MABI_DEMO=1`).

게임도 `MabinogiMobile_CLI.exe` 도 없이 UI 를 그대로 띄우기 위한 가짜 응답. 시연·스크린샷용이다.
`cli_transport.call()` 이 이 모듈로 우회하며, **배포판(frozen)에서는 켜지지 않는다**.

여기 실린 아티스트·곡 이름은 **전부 이 데모를 위해 지어낸 것**이다. 홍보물 스크린샷에 그대로
실어도 되도록 실존 아티스트·음원은 한 건도 쓰지 않았다. 대신 실제 이용자 보관함처럼 보이도록
접두 태그(`A_`)·잡음어(`cover`)·변형 꼬리(`-6합`)·동명 곡·구분자 없는 제목을 섞어 두었다 —
정규화와 아티스트 추정이 화면에 그대로 드러난다.
"""
from __future__ import annotations

import os
import time

# 스크린샷용: 재생을 이 초만큼 지나간 상태로 시작한다 (진행 막대가 멈춰 있지 않게). 평소엔 0.
SEEK = float(os.environ.get("MABI_DEMO_SEEK") or 0)

# ── 악기 (게임 아이템명 대신 일반 악기명만) ──────────────────────────────
INSTRUMENTS = [
    "류트", "3화음 류트", "만돌린", "2화음 만돌린", "플루트", "휘슬",
    "바이올린", "첼로", "하프", "우쿨렐레", "피아노", "3화음 피아노",
    "실로폰", "아코디언", "오카리나", "리코더", "팬플루트", "드럼",
]

# ── 악보: (제목, 길이초). 길이는 한 번 재생된 곡만 아는 값이라 일부만 채워 둔다 ──
SCORES: list[tuple[str, float]] = [
    # 구분자로 아티스트가 잡히는 것
    ("네온파일럿 - 네온 드라이브", 214.6),
    ("네온파일럿 - 마지막 지하철", 248.3),
    ("네온파일럿 - Static Garden", 196.0),
    ("구름정거장 - 다음 정거장", 227.4),
    ("구름정거장 - 창문 열어 둘게", 261.9),
    ("구름정거장 - 여름의 끝", 0),
    ("별빛우체국 - 새벽 세 시", 243.1),
    ("별빛우체국 - 편지 대신", 209.8),
    ("별빛우체국 - 겨울 우편함", 0),
    ("민트필름 - 나른한 오후", 188.2),
    ("민트필름 - 미지근한 커피", 175.5),
    ("도시양말 - 옥상에서", 232.7),
    ("도시양말 - 우리 동네", 0),
    ("슬로우버스 - 슬로우 모션", 254.0),
    ("슬로우버스 - 첫차", 198.6),
    ("코코아상점 - 자몽소다", 167.3),
    ("코코아상점 - 초콜릿 상자", 0),
    ("유월의 방 - 창가 자리", 221.4),
    ("유월의 방 - 룸메이트", 236.8),
    ("소리상자 - Rooftop Radio", 273.5),
    ("소리상자 - Lofi Cat", 182.9),
    ("은하수공방 - 별 지도", 295.2),
    ("은하수공방 - 우주 정거장", 0),
    ("오후네시 - 텅 빈 교실", 205.7),
    ("오후네시 - 새 신발", 163.0),
    ("전자양 - 전자 양의 꿈", 288.1),
    ("전자양 - Concrete Beach", 0),
    ("밤산책 - 헤드폰", 191.3),
    ("밤산책 - 파도 소리", 246.9),
    ("루프탑라디오 - Soda Machine", 179.4),
    # 구분자가 없어 「기타 · 미분류」로 모이는 것
    ("서울 야경", 217.2),
    ("자전거 타고", 0),
    ("초록불", 158.8),
    ("눈사람 만들기", 0),
    ("Vinyl Room", 233.6),
    ("Midnight Bus Line", 0),
    ("잠 못 드는 밤", 264.1),
    ("겨울 창문", 0),
    # 파일명을 그대로 올린 것 — 접두 태그·잡음어·변형 꼬리·다른 구분자
    ("A_네온 드라이브", 0),
    ("네온 드라이브-6합", 0),
    ("초록불 cover", 0),
    ("!_서울 야경", 0),
    ("네온파일럿-마지막 지하철", 0),
    ("새벽 세 시 by 별빛우체국", 0),
    # 같은 악보를 두 장 들고 있는 경우 — 목록에 「동명 2」 배지가 붙는다 (CLI 가 어느 쪽을 고를지 모른다)
    ("네온파일럿 - 네온 드라이브", 214.6),
    ("서울 야경", 217.2),
    ("구름정거장 - 다음 정거장", 227.4),
]

PREFIX = "악보: "


def titles() -> list[str]:
    """`악보: ` 접두가 붙은 전체 제목 — 씨앗 데이터(재생목록·길이·최근)가 같은 제목을 쓰도록."""
    return [PREFIX + t for t, _ in SCORES]


def durations() -> dict[str, float]:
    return {PREFIX + t: d for t, d in SCORES if d}


# ── 연주 상태 (재생 버튼을 누르면 실제로 진행 막대가 돈다) ────────────────
_perf: dict = {"title": "", "inst": "", "total": 0.0, "started": 0.0, "playing": False}


def _elapsed() -> float:
    return max(0.0, time.time() - _perf["started"])


def _performance() -> dict:
    if _perf["playing"] and _elapsed() >= _perf["total"]:
        _perf["playing"] = False       # 곡이 끝났다 (게임에서 반복이 꺼져 있는 상태)
    el = min(_elapsed(), _perf["total"]) if _perf["total"] else 0.0
    return {
        "IsPlaying": _perf["playing"],
        "MusicTitle": _perf["title"].removeprefix(PREFIX) if _perf["playing"] else "",
        "InstrumentName": _perf["inst"],
        "TotalDurationSeconds": round(_perf["total"], 2) if _perf["playing"] else 0,
        "ElapsedSeconds": round(el, 2) if _perf["playing"] else 0,
        "RemainingSeconds": round(max(0.0, _perf["total"] - el), 2) if _perf["playing"] else 0,
        "IsLoop": False,
    }


def respond(command: str, body) -> tuple[int, object]:
    """(exit code, 파싱된 body). 실제 CLI 의 응답 모양을 그대로 흉내 낸다."""
    arg = body if isinstance(body, dict) else {}

    if command == "status":
        return 0, {"pipe": "connected", "reason": None, "demo": True}

    if command == "capabilities":
        return 0, {"commands": [
            {"Command": c, "Description": d, "BodyExample": b, "OutputExample": ""} for c, d, b in (
                ("get_music_scores", "List music scores in inventory", ""),
                ("get_instruments", "List owned instruments", ""),
                ("play_music_score", "Play a music score", '{"title": "..."}'),
                ("change_instrument", "Equip an instrument", '{"name": "..."}'),
                ("stop_action", "Stop the current action", ""),
                ("get_activity", "Query current activity", ""),
                ("get_near_pcs", "Query nearby players", ""),
            )]}

    if command == "get_instruments":
        return 0, [{"Name": n, "Durability": 2147483647, "IsEquipped": n == _perf["inst"]} for n in INSTRUMENTS]

    if command == "get_music_scores":
        return 0, [{"Location": "inventory", "DisplayTitle": PREFIX + t,
                    "IsCopyingAllowed": True, "IsLocked": False} for t, _ in SCORES]

    if command == "get_activity":
        return 0, {"Performance": _performance()}

    if command == "get_near_pcs":
        me = _performance()
        if not me["IsPlaying"]:
            return 0, []
        total = max(20.0, _perf["total"] * 0.6)   # 이웃(합주 리더)의 악보는 내 것보다 짧다 → 앱이 리더 곡 끝에 맞춰 멈추는지 확인용
        el = min(_elapsed(), total)
        return 0, [{"RealmName": "데모합주자", "Title": "", "Distance": 4.2, "Level": 60, "IsFriend": False, "IsInParty": True,
                    "Performance": {"IsPlaying": el < total, "MusicTitle": me["MusicTitle"], "IsCopyingAllowed": True, "IsLoop": False,
                                    "TotalDurationSeconds": round(total, 2), "ElapsedSeconds": round(el, 2),
                                    "RemainingSeconds": round(max(0.0, total - el), 2), "ChannelCount": 2}}]

    if command == "change_instrument":
        name = str(arg.get("name") or "")
        if name not in INSTRUMENTS:
            return 0, {"error": "not_found", "message": f"Instrument not found: {name}"}
        _perf["inst"] = name
        return 0, {"message": "Instrument changed."}

    if command == "play_music_score":
        title = str(arg.get("title") or "")
        known = {PREFIX + t: d for t, d in SCORES}
        if title not in known:
            return 0, {"error": "not_found", "message": f"Score not found: {title}"}
        total = known[title] or 210.0
        _perf.update(title=title, total=total, started=time.time() - min(SEEK, total * 0.7), playing=True)
        return 0, {"message": "Play started."}

    if command == "stop_action":
        if not _performance()["IsPlaying"]:
            return 0, {"error": "invalid_state", "message": "No stoppable action."}
        _perf["playing"] = False
        return 0, {"message": "Stop confirmed."}

    return 4, {"error": "unknown_command", "message": f"Unknown command: {command}"}

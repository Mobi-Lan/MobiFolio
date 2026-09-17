"""데모 데이터 씨앗 — 재생목록·곡 길이·최근 재생·아티스트 수동 지정을 데모 데이터 폴더에 깔아 둔다.

`demo.cmd` 가 서버를 띄우기 전에 한 번 실행한다. 악보·악기 목록은 씨앗이 필요 없다 —
데모 CLI(`demo_cli.py`)가 「갱신」 때 내려 준다.

반드시 `MABI_DATA_DIR` 을 데모 전용 폴더로 두고 실행한다 (실제 보관함을 덮어쓰지 않게).
"""
from __future__ import annotations

import os
import sys
import time

import demo_cli
import store

T = demo_cli.PREFIX   # "악보: "


def _items(*titles: str) -> list[dict]:
    return [{"title": T + t, "inst": ""} for t in titles]


PLAYLISTS = {
    "folders": [
        {"id": "f1a2b3c4d5", "name": "공연", "parent": None},
    ],
    "playlists": [
        {"id": "p100000001", "name": "광장 공연 세트", "folder": "f1a2b3c4d5", "memo": "토요일 저녁",
         "items": _items("네온파일럿 - 네온 드라이브", "구름정거장 - 여름의 끝",
                         "도시양말 - 옥상에서", "코코아상점 - 자몽소다",
                         "은하수공방 - 별 지도", "네온파일럿 - 마지막 지하철")},
        {"id": "p100000002", "name": "밤 산책", "folder": "f1a2b3c4d5", "memo": "",
         "items": _items("별빛우체국 - 새벽 세 시", "밤산책 - 헤드폰",
                         "밤산책 - 파도 소리", "소리상자 - Lofi Cat",
                         "민트필름 - 미지근한 커피")},
        {"id": "p100000003", "name": "출근길", "folder": None, "memo": "",
         "items": _items("슬로우버스 - 첫차", "구름정거장 - 다음 정거장",
                         "초록불", "오후네시 - 새 신발")},
        {"id": "p100000004", "name": "연습 중", "folder": None, "memo": "손이 안 따라감",
         "items": _items("네온파일럿 - Static Garden", "전자양 - 전자 양의 꿈",
                         "유월의 방 - 룸메이트")},
    ],
}

# 구분자가 없어 「기타」에 남던 곡을 손으로 한 아티스트에 묶어 둔 상태 (목록에 「수동」 배지가 뜬다)
ARTISTS = {
    "artists": [{"id": "a1000studio", "name": "모비공방 합주단", "aliases": []}],
    "assign": {T + t: "a1000studio" for t in ("서울 야경", "!_서울 야경",
                                              "자전거 타고", "초록불")},
    "noise": [],
}

RECENT_TITLES = [
    ("별빛우체국 - 새벽 세 시", "하프"),
    ("네온파일럿 - 네온 드라이브", "3화음 류트"),
    ("밤산책 - 헤드폰", "피아노"),
    ("소리상자 - Lofi Cat", "피아노"),
    ("은하수공방 - 별 지도", "첼로"),
    ("서울 야경", "플루트"),
    ("슬로우버스 - 슬로우 모션", "만돌린"),
    ("도시양말 - 옥상에서", "3화음 류트"),
]

SETTINGS = {
    "cli_exe": "",
    "gap_sec": 2,
    "advance_margin": 2.0,
    "default_inst": "3화음 류트",
    "auto_sync": True,
    "stop_before_play": True,
}


def main() -> None:
    if not os.environ.get("MABI_DATA_DIR"):
        print("[demo_seed] MABI_DATA_DIR 이 없습니다 — 데모 전용 폴더를 지정하고 실행하세요.", flush=True)
        sys.exit(1)

    os.makedirs(store.DATA_DIR, exist_ok=True)
    store.save("playlists.json", PLAYLISTS)
    store.save("artists.json", ARTISTS)
    store.save("durations.json", demo_cli.durations())
    store.save("settings.json", SETTINGS)

    now = time.time()
    store.save("recent.json", [{"title": T + t, "inst": i, "ts": now - 240 * n}
                               for n, (t, i) in enumerate(RECENT_TITLES)])

    print(f"[demo_seed] {store.DATA_DIR} 준비 완료 — 악보 {len(demo_cli.SCORES)}곡, "
          f"재생목록 {len(PLAYLISTS['playlists'])}개", flush=True)


if __name__ == "__main__":
    main()

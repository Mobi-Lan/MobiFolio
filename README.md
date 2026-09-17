# 악보함 (mabi-playlist)

마비노기 모바일 CLI(`MabinogiMobile_CLI.exe`)로 내 악기·악보를 받아 두고, 재생목록(폴더·검색·초성 색인)을 만들고, 고른 악보를 CLI 로 재생하는 로컬 도구.

## 실행
`run.cmd` — 브라우저에 http://127.0.0.1:19997 이 열린다. 인게임 「MM AI 에이전트 활성화」 토글이 켜져 있어야 CLI 가 동작한다(7일 만료).

- **갱신**: `get_instruments`, `get_music_scores` 를 CLI 로 받아 `data/` 에 저장(원본은 `fixtures/` 에도). 오프라인이면 마지막 저장분을 쓴다.
- **재생**: 악보 ▶ → (악기를 골랐으면 `change_instrument`) → `play_music_score`. 「전체 재생」은 `get_activity` 의 `IsAutoPlaying` 을 4초마다 보고 끝나면 다음 곡.
- **정지**: `get_activity` 확인 후 `stop_action`. `invalid_state` 면 상태 전이 중이라 짧게 재시도.

## 규칙 (HANDOFF + 2026-09-17 실측)
- JSON body 는 `\uXXXX` 이스케이프한 **순수 ASCII JSON** 으로 보낸다 — 콘솔 코드페이지와 무관하고 base64 가 필요 없다 (실측: `play_music_score {"title": "악보…"}` → `Play started`). raw 문자열(`write_chat`)의 비ASCII 만 `base64:`.
- 성공 = `exit 0` 이면서 body 에 `error` 없음. `capabilities` 도 게임이 꺼져 있으면 `game_off` — 로컬 명령이 아니다(핸드오프 추정 정정).
- `get_activity.Performance` 에 연주 상태가 온다: `IsPlaying` `MusicTitle` `InstrumentName` `TotalDurationSeconds` `ElapsedSeconds` `RemainingSeconds` `IsLoop`. 재생 감시·진행 막대·순차 재생은 이걸 쓴다 (`IsAutoPlaying` 은 자동 사냥용이라 연주와 무관). `IsLoop` 가 켜져 있으면 곡이 스스로 안 끝날 수 있어, 전체 재생은 끝 2초 전에 정지하고 다음 곡을 보낸다.
- `play_music_score` 는 즉시 반환(비블로킹). `stop_action` 은 연주 중이 아니면 `invalid_state` → 서버가 `Performance.IsPlaying` 을 먼저 보고 재시도 없이 성공 처리.
- 동명 악보는 CLI 가 임의 선택. 목록의 「동명 N」 표시로 알려 준다. 모든 제목이 `악보: ` 접두로 온다(정규화에서 제거, 재생엔 원본 사용).
- 실행 명령은 최대 9분 블로킹 가능 → 타임아웃 11분.

## 정규화·아티스트
`library.py` 가 제목을 정리(접두 태그 `A_` `!_` `1.` `@`, 카테고리어 동요/영화/OST/Jpop → 태그, 변형 꼬리 `-1` `-test` `-6합` → 변형)하고 구분자(` - ` `-` `_` `/` …)로 아티스트/곡을 나눈다. 숫자만인 쪽은 곡(`백예린 - 0310`)이되 2곡 이상 반복되는 숫자 이름은 밴드(`0018`)로 인정. 구분자로 뽑힌 아티스트는 사전이 되어 `아이유 밤편지` 처럼 구분자 없는 제목도 잡는다. 못 잡은 곡은 **기타** 에 모이고, 곡별 「인물」 버튼이나 체크박스 일괄 선택으로 아티스트를 지정·해제한다(「뒤집기」로 순서 뒤집힌 제목도 한 번에). 수동 지정·별칭·병합은 `data/artists.json` 에 남고 자동 추정보다 우선한다.

## 구조
`cli_transport.py`(호출·인코딩·exit코드) · `store.py`(data/·fixtures/) · `library.py`(정규화·아티스트·초성·중복·검색) · `server.py`(API + UI 서빙) · `ui/index.html`.

# 악보함 (mabi-playlist)

마비노기 모바일 CLI(`MabinogiMobile_CLI.exe`)로 내 악기·악보를 받아 두고, 재생목록(폴더·검색·초성 색인)을 만들고, 고른 악보를 CLI 로 재생하는 로컬 도구.

## 실행
`run.cmd` — 브라우저에 http://127.0.0.1:19997 이 열린다. 인게임 「MM AI 에이전트 활성화」 토글이 켜져 있어야 CLI 가 동작한다(7일 만료).

- **갱신**: `get_instruments`, `get_music_scores` 를 CLI 로 받아 `data/` 에 저장(원본은 `fixtures/` 에도). 오프라인이면 마지막 저장분을 쓴다.
- **재생**: 악보 ▶ → (악기를 골랐으면 `change_instrument`) → `play_music_score`. 「전체 재생」은 `get_activity` 의 `IsAutoPlaying` 을 4초마다 보고 끝나면 다음 곡.
- **정지**: `get_activity` 확인 후 `stop_action`. `invalid_state` 면 상태 전이 중이라 짧게 재시도.

## 규칙 (HANDOFF)
- 비ASCII body 는 통째로 `base64:`. 성공 = `exit 0` 이면서 body 에 `error` 없음.
- `status`/`capabilities` 는 로컬 명령이라 `last-response.json` 을 갱신하지 않음 → stdout 만.
- 동명 악보는 CLI 가 임의 선택. 목록의 「동명 N」 표시로 알려 준다.
- 실행 명령은 최대 9분 블로킹 → 타임아웃 11분.

## 구조
`cli_transport.py`(호출·인코딩·exit코드) · `store.py`(data/·fixtures/) · `library.py`(정규화·아티스트·초성·중복·검색) · `server.py`(API + UI 서빙) · `ui/index.html`.

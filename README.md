# 악보함 (mabi-playlist)

마비노기 모바일 CLI(`MabinogiMobile_CLI.exe`)로 내 악기·악보를 받아 두고, 재생목록(폴더·검색·초성 색인)을 만들고, 고른 악보를 CLI 로 재생하는 로컬 도구. 인터넷·외부 서비스·LLM 없이 동작한다 (파이썬 표준 라이브러리 + 일렉트론 셸).

## 실행
- **배포판**: `build.cmd` 로 만든 `dist-electron\ScoreBox-win32-x64\ScoreBox.exe` (창 제목·바로가기는 「악보함」; 폴더·실행파일명은 유니코드 정규화 문제를 피하려고 ASCII) (일렉트론 창 + 동봉된 파이썬 백엔드 `resources\MabiScoreBox.exe`). 창을 닫으면 백엔드에 정상 종료를 요청하고, 안 끝나면 강제 종료한다. 셸이 강제로 죽어도 백엔드는 부모 프로세스를 감시해 스스로 끝난다. **데이터·로그는 사용자 폴더 `%LOCALAPPDATA%\MabiScoreBox`** (`data/`, `fixtures/`, `mabi-scorebox.log`(2MB 넘으면 새로 시작), `electron.log`) — 배포판·개발 실행이 같은 저장소를 쓰고, 재빌드·폴더 교체에 영향받지 않는다. 예전 위치(앱 폴더·프로젝트 폴더의 `data/`)가 있으면 첫 실행 때 한 번 병합해 옮긴다 — 아티스트 지정·곡 길이·최근 재생·재생목록은 합집합, 설정·캐시는 최신본 (`data/migrated.json` 에 출처 기록). `MABI_DATA_DIR` 환경변수로 위치를 강제할 수 있다(테스트용).
- **개발**: `run.cmd`(파이썬 백엔드가 Edge/Chrome 앱 창을 직접 연다) 또는 `cd app && npm start`(일렉트론, 루트의 `MabiScoreBox.exe` 나 `server.py` 를 백엔드로 씀. 이미 떠 있는 백엔드가 있으면 거기에 붙는다). 인게임 「MM AI 에이전트 활성화」 토글이 켜져 있어야 CLI 가 동작한다(7일 만료).
- **빌드 요구**: Python 3.12 (pyinstaller 는 `build.cmd` 가 설치), Node.js/npm. 포트 `19997` 을 쓴다. `build.cmd --nopause` 로 멈춤 없이 실행. 빌드 전에 앱을 닫아야 한다(실행 중이면 중단).

## 기능
- **갱신**: `get_instruments`, `get_music_scores` 를 CLI 로 받아 `data/` 에 저장(원본은 `fixtures/` 에도). 오프라인이면 마지막 저장분을 쓴다. 0건이나 모양이 이상한 응답은 기존 캐시를 덮어쓰지 않는다.
- **재생**: 악보 ▶ 또는 행 더블클릭 → (보관함에 있는 악보·악기인지 확인) → (설정) 연주 중이면 정지 → (악기를 골랐으면 `change_instrument`) → `play_music_score` (`invalid_state` 면 짧게 재시도). 「전체 재생」·재생목록 재생은 `get_activity.Performance` 를 1초마다 보고 진행 막대를 그리며, 끝 N초 전(설정 「전환 여유」) 또는 총길이 경과에 정지하고 다음 곡을 보낸다(연주가 반복 설정이라 스스로 안 끝날 수 있음). 곡 사이 대기(설정 「대기」) 중 정지 버튼은 정지로 동작한다. 게임에서 직접 멈추면 「게임에서 정지됨」으로 멈춘다.
- **이전/다음**: 다음 = 현재 곡 정지 후 재생목록의 다음 곡. 이전 = 4초 이내면 이전 곡, 그 뒤면 현재 곡 처음부터. 반복(끔/전체/한 곡)·셔플(전체 반복 시 한 바퀴마다 다시 섞음). 재생 중인 재생목록을 편집하면 큐도 따라간다.
- **정지**: `get_activity.Performance.IsPlaying` 을 먼저 보고, 연주 중일 때만 `stop_action`(`invalid_state` 면 상태 전이 중이라 짧게 재시도).
- **곡 길이**: 별도 API 가 없어, 한 번 재생된 곡의 `TotalDurationSeconds` 를 `data/durations.json` 에 남겨 목록·큐에 표시한다(게임 쪽 제목과 같을 때만 기록). 최근 재생은 `data/recent.json`(100곡), CLI 응답 로그는 `data/cli_log.json`(60건).
- **설정**(톱니): CLI 경로(비우면 자동 탐색; 존재하는 `.exe` 절대 경로만 허용), 곡 사이 대기(0~60초), 전환 여유(0~30초), 기본 악기, 시작 시 자동 갱신, 재생 전 현재 연주 정지. `data/settings.json`.
- **일괄 처리**: 체크한 곡을 선택 재생 / 재생목록에 담기 / 새 목록으로 / 아티스트 지정·해제 / 목록에서 제거.

## 규칙 (HANDOFF + 2026-09-17 실측)
- JSON body 는 `\uXXXX` 이스케이프한 **순수 ASCII JSON** 으로 보낸다 — 콘솔 코드페이지와 무관하고 base64 가 필요 없다 (실측: `play_music_score {"title": "악보…"}` → `Play started`). raw 문자열(`write_chat`)의 비ASCII 만 `base64:`.
- 성공 = `exit 0` 이면서 body 에 `error` 없음. `capabilities` 도 게임이 꺼져 있으면 `game_off` — 로컬 명령이 아니다(핸드오프 추정 정정).
- `get_activity.Performance` 에 연주 상태가 온다: `IsPlaying` `MusicTitle`(「악보: 」접두 없음) `InstrumentName` `TotalDurationSeconds` `ElapsedSeconds` `RemainingSeconds` `IsLoop`. 재생 감시·진행 막대·순차 재생은 이걸 쓴다 (`IsAutoPlaying` 은 자동 사냥용이라 연주와 무관).
- `play_music_score` 는 즉시 반환(비블로킹). `stop_action` 은 연주 중이 아니면 `invalid_state`.
- `last-response.json` 폴백은 이번 호출 이후에 갱신된 파일만 믿는다(예전 응답이 이번 결과로 둔갑하지 않게).
- 동명 악보는 CLI 가 임의 선택. 목록의 「동명 N」 표시로 알려 준다. 모든 제목이 `악보: ` 접두로 온다(정규화에서 제거, 재생엔 원본 사용).
- 실행 명령은 최대 9분 블로킹 가능 → 타임아웃 11분.

## 로컬 API 보안
POST 는 `Host` 가 127.0.0.1/localhost 이고 `X-Requested-With: scorebox` 헤더가 있을 때만 받는다(브라우저의 다른 사이트가 보내는 폼/스크립트 요청 차단). 본문은 1MB 까지, 객체(JSON object)만. 잘못된 입력은 연결을 끊지 않고 400/500 JSON 으로 답한다.

## 정규화·아티스트
`library.py` 가 제목을 정리(접두 태그 `A_` `!_` `1.` `@`, 카테고리어 동요/영화/OST/Jpop → 태그(맨 앞 `_`/`-`/`:` 연결 또는 맨 끝 토큰만), 잡음어 cover/ver/완성본 … 은 꼬리 토큰만, 변형 꼬리 `-1` `-test` `-6합` → 변형)하고 구분자(` - ` `-` `/` `_` `by` …)로 아티스트/곡을 나눈다(`by` 는 오른쪽이 아티스트). 숫자만인 쪽은 곡(`백예린 - 0310`)이되 서로 다른 2곡 이상에 반복되는 숫자 이름은 밴드(`0018`)로 인정. 구분자로 뽑힌 아티스트는 사전이 되어 `아이유 밤편지` 처럼 구분자 없는 제목도 잡는다 — 이름은 토큰 경계로 통째로 맞아야 한다(`IU` 가 `Aquarium` 에 걸리지 않음). 이름을 떼고 곡이 남지 않으면 지정하지 않는다. 등록된 이름이 오른쪽에만 있는 `곡 - 아티스트` 는 뒤집어 준다. 못 잡은 곡은 **기타** 에 모이고, 곡별 「아티스트」 버튼이나 체크박스 일괄 선택으로 아티스트를 지정·해제한다. 수동 지정·별칭·병합은 `data/artists.json` 에 남고 자동 추정보다 우선한다(공백·기호만 다른 제목도 같은 지정).

## 저장소
`data/*.json` 은 UTF-8, 원자적 쓰기(스레드 잠금, 고유 임시파일, 공유 위반 재시도). 못 읽는 파일은 `<name>.corrupt-<ts>` 로 옮겨 두고 기본값으로 시작한다(원본 보존). 모양이 틀린 항목은 버린다.

## 구조
`cli_transport.py`(호출·인코딩·exit코드) · `store.py`(data/·fixtures/·설정 검증·잠금) · `library.py`(정규화·아티스트·초성·중복·검색) · `server.py`(API + UI 서빙 + 종료/헬스) · `ui/index.html` · `app/main.js`(일렉트론 셸) · `build.cmd`(exe + 패키지) · `run.cmd`(개발 실행).

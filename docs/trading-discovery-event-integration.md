# Trading Console → Relay 후보 게시 이벤트 계약

## 목적과 호출 시점

Trading Console이 21시 `장종료후 단타후보군 탐색` 조건검색을 성공적으로
마치고 날짜별 불변 스냅샷을 확정한 직후 한 번 호출한다. 수동 `전일후보군`
동기화는 mutable 후보와 런타임 구독만 갱신하며 공식 스냅샷과 이 게시 이벤트를
생성하지 않는다. 동기화 실패, 스냅샷 미생성, 미확정
상태에서는 호출하지 않는다. 이 인터페이스는 키움 API를 호출하거나 후보,
거래일, 원본 스냅샷을 변경하지 않는다.

21시 공식 자동화만 아래 명령을 성공 후처리로 사용한다. cron, LaunchAgent,
GitHub Actions 또는 디렉터리 감시기를 추가하지 않는다.

허용 출처는 `KIWOOM_WATCHLIST`/`전일후보군`과
`KIWOOM_CONDITION_SEARCH`/`장종료후 단타후보군 탐색` 두 쌍뿐이다. 후자는
`group_id=CONDITION:<조건식 번호>`와 KST `synced_at`을 요구한다. 출처가 다른
동일 날짜 자료는 후보 집합이 같더라도 해시가 다르므로 기존 날짜별 파일을
덮어쓰지 않고 충돌로 중단한다.

## 실행 명령

```bash
python3 /Users/hanull/Projects/naver-krx-universe-relay/tools/on_trading_discovery_snapshot.py \
  /Users/hanull/KIS_Trading/data/candidate-snapshots/YYYY-MM-DD.json \
  --calendar /Users/hanull/KIS_Trading/data/intraday/trading-days.json \
  --repository /Users/hanull/Projects/naver-krx-universe-relay \
  --remote origin \
  --branch main
```

`YYYY-MM-DD.json`은 방금 성공한 동기화가 반환한 정확한 확정 스냅샷 경로로
치환한다. 디렉터리에서 최신 파일을 추정하거나 파일명 날짜를 다시 계산하지
않는다.

## 필수 입력과 환경

- 위치 인수 `snapshot`: 존재하는 확정 스냅샷의 절대 경로
- `--calendar`: Trading Console이 사용한 `KRX_OFFICIAL` 캘린더 파일의 절대 경로
- `--repository`: 배포된 Relay 저장소의 절대 경로
- `--remote`: 기본값 `origin`
- `--branch`: 기본값 `main`
- 필수 애플리케이션 환경변수: 없음
- 운영 전제: `python3`와 `git` 실행 가능, 저장소 읽기·임시 worktree 생성 권한,
  원격 저장소 fetch 및 non-force push 인증
- 브로커 키, 토큰, 계좌번호, 주문 권한은 필요하지 않으며 전달하지 않는다.

## 성공 결과

프로세스 종료코드 `0`과 JSON 한 줄을 반환한다.

- `publishStatus: "PUSHED"`: 날짜별 파일과 `latest.json`을 새 commit으로 게시하고
  원격 SHA 및 `sourceSha256` 검증까지 완료
- `publishStatus: "NOOP"`: 같은 데이터가 이미 게시되어 원격 변경 없음
- `commitSha`: 실제 게시 commit; NO-OP이면 `null`
- `remoteSha`: 확인된 원격 main SHA
- `stagedFiles`: 게시된 후보 데이터 파일만 포함

두 상태 모두 호출자 관점에서는 성공이다.

## 실패 결과와 재시도

검증, 충돌, 인증, 네트워크 또는 원격 경쟁 오류는 종료코드 `0`이 아닌 실패로
처리한다. 후보 원본과 기존 원격 유효 파일은 보존된다. 호출자는 실패 내용을
기록하되 후보 목록을 다시 동기화하거나 날짜를 보정하지 않는다.

동일한 확정 스냅샷 경로로 재시도한다. 성공한 게시를 다시 호출하면 NO-OP이다.
`REMOTE_ADVANCED_BEFORE_PUSH`는 최신 main 기준으로 명령을 다시 실행한다.
`PUBLISH_ALREADY_RUNNING`은 선행 호출이 끝난 뒤 동일 입력으로 한 번 재시도한다.
스키마, 해시, 날짜 관계 또는 확정 파일 충돌 오류는 자동 재시도하지 않고 운영자
확인 대상으로 남긴다.

## 중복·경쟁 처리

- 프로세스 잠금으로 동시에 실행되는 중복 호출을 거부한다.
- 날짜별 기존 파일의 `sourceSha256`이 다르면 덮어쓰지 않는다.
- 같은 스냅샷은 멱등 처리한다.
- 게시 직전 원격 main이 바뀌면 중단한다.
- force push를 사용하지 않는다.
- 후보 데이터 push는 기존 시장 refresh workflow 경로와 일치하지 않으며
  `MARKET_DATA_READY` 이메일을 추가 발송하지 않는다.

## Trading Console 연결 확인 기준

1. 동기화 성공 시 생성된 스냅샷 경로를 그대로 전달한다.
2. 프로세스 종료코드와 JSON을 운영 로그에 기록한다.
3. `PUSHED` 또는 `NOOP`만 성공으로 처리한다.
4. 게시 실패가 Trading Console 후보 저장 성공을 되돌리거나 기존 시장 데이터
   수집을 중단시키지 않게 한다.
5. 실제 연결 후 테스트에서는 검증용 저장소 또는 명시적으로 승인된 스냅샷을
   사용한다. 후보 JSON을 수작업으로 만들거나 수정하지 않는다.

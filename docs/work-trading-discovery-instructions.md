# 국내증시 Work — 키움 전일후보군 연동 지시문

아래 내용을 국내증시 Work의 기존 지시문에 추가한다. 기존 시장 리포트,
`MARKET_DATA_READY` 처리와 핵심 데이터 검증은 유지한다. 종가 기준은 아래의 KRX
세션 정책을 따른다.

## 종가 정책

1. 가격 기준은 KRX 원천으로 통일한다. NXT 등 다른 거래소의 가격이나 거래시간을
   종가 원천으로 사용하지 않는다.
2. 2025-03-04 NXT 출범은 시장 운영이 달라진 기준일이지만, 이 저장소의 가격
   원천은 계속 KRX로 고정한다. KRX 공식 자료로 확인된 정책 적용일이 없는
   과거 구간은 임의로 전환하지 않는다. 전환 전 이력은 15:30 정규장 종가를
   유지하고, 전환 후 이력은 KRX 애프터마켓 종료 후 확인된 KRX 최종 종가를
   사용한다. NXT 가격은 이 저장소의 종가 원천이 아니다.
3. 현재 FINAL 자동 실행 목표는 20:30 KST이며, 20:00 이전의 수집값은 FINAL로
   게시하지 않는다.
4. 실행 시각만으로 FINAL을 확정하지 않는다. KRX 거래일, NAVER 원천의 명시적
   거래일과 종가 필드, 종목별 일봉 증거가 모두 일치해야 한다.
5. NAVER 일봉의 `localDate`와 OHLCV가 위 KRX 종가 정책에 부합하는지 검증한다.
   증거가 부족하면 `PARTIAL` 또는 `UNAVAILABLE`로 남긴다.
6. 검증된 종가는 Baseline·MA·V1·V2에 같은 기준으로 전달한다. 도입 이전의
   정상 이력은 재계산하거나 덮어쓰지 않는다.

## 입력과 검증

1. 시장 데이터의 기준은 `MARKET_DATA_READY` 이메일에 기록된 commit SHA다.
2. 키움 후보 데이터는 시장 데이터와 별도 commit일 수 있다. 두 SHA가 같다고
   가정하거나 같아질 때까지 대기하지 않는다.
3. 먼저 다음 고정 URL을 읽는다.
   `https://raw.githubusercontent.com/hanull01/naver-krx-universe-relay/main/data/trading-discovery/latest.json`
4. `latest.json`의 `monitoringDate`가 리포트 대상 KRX 거래일과 같을 때만 다음
   날짜별 URL을 읽는다.
   `https://raw.githubusercontent.com/hanull01/naver-krx-universe-relay/main/data/trading-discovery/YYYY-MM-DD.json`
5. 날짜별 파일의 `monitoringDate`도 리포트 대상일과 같아야 한다. 오늘 날짜가
   아니라는 이유로 후보 날짜를 추정·보정하지 않는다.
6. `sourceSha256`은 게시 JSON의 camelCase 값을 다음 snake_case 객체로 먼저
   변환해 계산한다: `source`, `source_group`, `group_id`, `selection_date`,
   `symbols`. `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`로
   JSON 직렬화하고 UTF-8 바이트의 SHA-256을 계산한다. 게시 JSON 키 이름을
   그대로 해시하지 않는다. 스키마, 후보 수, 중복, 해시, validation이 하나라도
   맞지 않으면 키움 섹션만 `INVALID`로 처리한다.
7. `AVAILABLE_EMPTY`는 검증된 0종목이다. 파일 없음·읽기 실패인
   `UNAVAILABLE`과 구분하고, 후보를 임의 생성하지 않는다.

## 매시간 분석

8. 동일한 `(monitoringDate, sourceSha256)` 후보군을 매 실행마다 유지하고 최신
   NAVER 시장 데이터로 다시 분석한다. 후보 파일이 바뀌지 않아도 시장 값은
   매시간 갱신한다.
9. 기존 42종목 데이터의 종목코드가 후보에 있으면 그 시세·기술지표·상태를
   재사용한다. 전체시장 신호에 이미 있는 코드도 재사용한다.
10. 두 데이터셋에 모두 없는 후보는 먼저 다음 GitHub 보조 시세를 읽는다.
    `data/trading-discovery/quotes/latest.json`과
    `data/trading-discovery/quotes/YYYY-MM-DD.json`. `monitoringDate`,
    `candidateSourceSha256`, `marketSourceTime`이 현재 후보·시장 데이터와 모두
    일치하는 행만 사용한다. Relay는 누락 후보만 한 번에 최대 50종목 조회하며
    전체시장을 다시 수집하지 않는다. 보조 시세에도 없으면 해당 종목을
    `UNAVAILABLE`로 남긴다.
11. 조회 가능한 등락률, 거래량, 거래대금, MA5·MA20·MA60, 거래량비율,
    20일 돌파 상태를 표시한다. 제공되지 않은 값, VWAP, 장중 고저는 추정하지
    않고 `UNAVAILABLE`로 표시한다.
12. 직전 리포트의 같은 종목과 가격, 데이터 상태, 돌파 상태 변화를 비교한다.
    반복 이메일 횟수가 아니라 `(monitoringDate, sourceSha256, 시장 sourceTime)`을
    비교 키로 사용한다.
13. 기존 본문과 독립된 `🟪 키움 전일후보군 — 당일 추적` 섹션을 만든다.
    후보 오류, 날짜 불일치, 일부 시세 누락, 맥북 오프라인, 게시 실패가 기존
    시장 리포트의 생성·상태·전송을 저하시키면 안 된다.

## 운영 확인

14. 실제 Work 환경에서 두 GitHub URL 접근, 날짜별 파일 고정, 해시 재계산,
    동일 후보의 시간대별 재분석, 이전 리포트 비교를 각각 확인한다.
15. Relay 보조 시세의 `requestedCount`, `quoteCount`, `missingSymbols`, `errors`로
    조회 범위와 실패를 확인한다. Work가 외부 시세 사이트를 직접 호출하지
    않는다. 보조 시세 실패 시 키움 섹션에만 오류를 기록하고 기존 리포트가
    정상 생성되는지 확인한다.

Relay 로컬 실행 경로에서는 `daily_report.py --fetch-discovery-missing`을 지정한
경우에만 누락 후보 추가 조회가 활성화된다. 이 옵션 없이 실행하면 네트워크
추가 호출은 0건이다.

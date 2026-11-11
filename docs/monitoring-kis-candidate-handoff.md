# 모니터링 소유 KIS 후보 handoff

KIS 21:00 producer가 생성한 공식 날짜별 snapshot은 모니터링 저장소가 읽기
전용으로 소비한다. 이 경로는 KIS source code나 snapshot을 import·수정하지
않으며, 고정 21:30 scheduler를 추가하지 않는다.

## 실행 조건

handoff 호출자는 KIS producer 성공 후 반환된 정확한 snapshot 경로를 전달해야
한다. 다음 조건을 모두 만족하지 않으면 게시하지 않는다.

- snapshot 파일명이 `YYYY-MM-DD.json`이고 `--monitoring-date`와 일치
- `selectionDate`, `monitoringDate`, source, source hash가 검증됨
- candidate symbol 집합이 중복 없이 유효함
- KRX 공식 calendar에서 selection date 다음 거래일이 monitoring date임
- snapshot 원본이 존재하며 읽기 전용으로 유지됨

예시:

```bash
python3 /Users/hanull/MigrationFrozen_20261107/naver-krx-universe-relay/tools/publish_kis_candidate_delivery.py \
  --snapshot /Users/hanull/MigrationFrozen_20261107/KIS_Trading/data/candidate-snapshots/YYYY-MM-DD.json \
  --monitoring-date YYYY-MM-DD \
  --calendar /Users/hanull/MigrationFrozen_20261107/KIS_Trading/data/intraday/trading-days.json \
  --quotes /Users/hanull/MigrationFrozen_20261107/naver-krx-universe-relay/data/quotes.json \
  --repository /Users/hanull/MigrationFrozen_20261107/naver-krx-universe-relay \
  --allow-network
```

`--allow-network` 없이는 `--dry-run`만 허용된다. dry-run은 quote 응답을
임의로 만들지 않고 0/N `PARTIAL` fixture로만 검증한다.

## 소유권과 게시

publisher는 격리 worktree에서 다음 네 파일만 생성·검증·non-force push한다.

- `data/trading-discovery/YYYY-MM-DD.json`
- `data/trading-discovery/latest.json`
- `data/trading-discovery/quotes/YYYY-MM-DD.json`
- `data/trading-discovery/quotes/latest.json`

dated/latest candidate와 quote는 같은 monitoring date, source hash, symbol 집합을
가져야 한다. identity가 다르면 게시를 중단하고 기존 파일을 보존한다. KIS
snapshot 자체는 GitHub에 게시하지 않는다.

기존 `tools/on_trading_discovery_snapshot.py`와
`tools/publish_trading_discovery.py`는 KIS LaunchAgent가 사용하는 구형 후보
publisher로 유지하며 이 handoff에서 호출하지 않는다. 새 publisher는 LaunchAgent
또는 Cloudflare에 연결되지 않으며, 21:00 이후 producer가 넘긴 정확한 파일을
승인된 호출자가 한 번 실행하는 구조다.

`refresh.yml`은 로컬 snapshot을 찾지 않는다. GitHub에 이미 게시된
`data/trading-discovery` artifact를 report contract 입력으로 소비한다.

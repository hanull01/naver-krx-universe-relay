# 외부 예약 연동 초안

이 디렉터리는 GitHub Actions 예약 장애를 독립적으로 감시·보완하기 위한 Cloudflare Workers Cron 초안이다. 현재는 배포하지 않았으며, Secret 등록·실제 Cron 활성화는 별도 승인 후 수행한다.

## 안전 계약

- `GITHUB_TOKEN`은 Worker Secret으로만 주입하며 코드와 로그에 출력하지 않는다.
- `GITHUB_REPOSITORY`는 Worker 환경 변수로 주입한다.
- Durable Object `SLOT_GATE`가 슬롯별 원자적 claim을 수행한다. 상태는 `CLAIMED → DISPATCHED → RUN_VERIFIED → PUBLISHED` 또는 `FAILED`로 기록한다.
- 외부 슬롯 판정 유예시간은 예정시각 전 5분, 후 15분이다. 이 범위에서 `schedule` 또는 `workflow_dispatch` Run이 있으면 동일 슬롯으로 간주해 dispatch하지 않는다. 범위 밖은 누락으로만 판정한다.
- dispatch 성공은 Run 생성 성공이 아니다. Run ID, 실행 HEAD, 완료 결론을 별도로 확인한다.
- 게시가 필요한 workflow는 게시 커밋 SHA를 확인한 뒤에만 게시 검증을 성공으로 기록한다.
- Gmail 수신과 Work 전달은 이 Worker만으로 확인하지 않으며 `UNVERIFIED`로 남긴다.
- GitHub 기존 schedule과 동시 운영할 때도 기존 workflow의 `generated-data-write` 동시성 보호를 유지한다.
- `SCHEDULE_OBSERVE_ONLY=true`인 실행은 GitHub 실행 이력만 확인하고 dispatch하지 않는다. 관찰 결과는 `RUN_OBSERVED` 또는 `NO_RUN`으로 기록한다.
- Run 생성 확인과 Run 완료 확인은 분리한다. 60초 안에 완료되지 않아도 Run ID와 HEAD를 `RUN_STARTED`로 보존하고, 후속 조회에서 완료·게시를 검증한다.
- 게시 커밋은 workflow가 제공한 `production_commit_sha`를 우선 사용하고, 해당 SHA의 커밋 메시지·자동화 주체·허용 경로·Run 구간을 모두 검증한다. Run 출력에 SHA가 없으면 `PUBLISH_UNVERIFIED`로 남긴다. 단순 시간 범위의 임의 커밋은 게시 증거로 사용하지 않는다.
- 현재 GitHub Actions의 `production_commit_sha`는 step output이므로 Run API의 일반 응답에 자동 노출된다고 가정하지 않는다. 별도 Run 출력·artifact·API 연결이 없으면 Worker는 보수적으로 게시를 미확인 처리한다.
- `refresh.yml`과 `daily-report.yml`의 `generated-data-write` concurrency가 저장소 수준의 동시 게시를 직렬화한다. Worker는 슬롯별 사전 Run 확인과 Durable Object claim으로 외부 중복을 차단한다.
- `wrangler.toml`에는 배포에 필요한 Cron 목록, `SLOT_GATE` Durable Object 바인딩, `SlotGate` SQLite migration을 선언한다. 실제 Cron 활성화와 Secret 등록은 별도 승인 없이는 수행하지 않는다.
- Free 플랜의 5개 Cron 한도를 위해 장중·FINAL·리포트·보조 슬롯을 5개 표현식으로 통합한다. 보조 통합 Cron은 KST 시각 필터로 Research(07:20/12:20/19:20), Snapshots(08:10~19:50의 10/30/50분), 진단(09:17)만 실행한다.
- 보조 통합 Cron은 월요일 KST 이른 슬롯을 포함하도록 UTC 일요일~금요일에 발생시키고, Worker가 KST 토·일을 필터링한다. 따라서 UTC 보정으로 인한 주말 실행은 dispatch하지 않는다.
- Cloudflare Cron은 요일 표기가 `1=일요일`인 규칙을 사용하므로 `SUN-THU`, `MON-FRI`, `SUN-FRI` 명칭을 사용한다. GitHub Actions의 숫자 요일 표기와 혼동하지 않는다.
- `production-publication-<runId>-<sha>-<changed>` artifact가 Run ID·게시 SHA·변경 여부를 연결한다. artifact가 없거나 SHA 검증이 실패하면 게시 성공으로 승격하지 않는다.
- `daily-report.yml`은 하나의 보고서 모델에서 `data/reports/latest.json`, `data/reports/latest.md`, `data/reports/latest.html`을 함께 생성한다. Worker의 리포트 게시 검증은 세 파일이 같은 게시 커밋에 모두 포함될 때만 `PUBLISHED`로 기록한다.
- 이 Worker는 GitHub 게시만 검증하며, 외부 Work의 실제 HTML 다운로드·첨부·사용은 확인하지 않는다. 해당 결과는 별도로 `UNVERIFIED`로 남긴다.

## 슬롯

| KST | 대상 | 입력 |
|---|---|---|
| 08:00 | Refresh market data | `run_mode=intraday` |
| 09:07~19:07 | Refresh market data | `run_mode=intraday` |
| 20:30 | Refresh market data | `run_mode=final_recovery` |
| 09:17 | Schedule diagnostic | 없음 |
| 23:00 | Build daily market report | 없음 |
| 기존 Research/Snapshots 슬롯 | 각 workflow | 없음 |

20:30 슬롯은 `asOfDate`, KOSPI/KOSDAQ FINAL, Baseline, trading-session diagnostic, 게시 커밋 SHA가 서로 일치하는 경우에만 `FINAL_PUBLICATION_VERIFIED`로 기록한다. `RUN_SUCCESS`만으로 FINAL 또는 사용자 전달 성공을 선언하지 않는다.

실제 배포 전에는 동일 슬롯의 GitHub Run 존재 여부, 게시 SHA, Gmail 수신, Work 결과를 별도 시험해야 한다.

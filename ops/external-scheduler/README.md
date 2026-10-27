# 외부 예약 연동 초안

이 디렉터리는 GitHub Actions 예약 장애를 독립적으로 감시·보완하기 위한 Cloudflare Workers Cron 초안이다. 현재는 배포하지 않았으며, Secret 등록·실제 Cron 활성화는 별도 승인 후 수행한다.

## 안전 계약

- `GITHUB_TOKEN`은 Worker Secret으로만 주입하며 코드와 로그에 출력하지 않는다.
- `GITHUB_REPOSITORY`는 Worker 환경 변수로 주입한다.
- Durable Object `SLOT_GATE`가 슬롯별 원자적 claim을 수행한다. 이미 claim된 슬롯은 workflow dispatch하지 않는다.
- dispatch 성공은 Run 생성 성공이 아니다. Run ID, 실행 HEAD, 완료 결론을 별도로 확인한다.
- 게시가 필요한 workflow는 게시 커밋 SHA를 확인한 뒤에만 게시 검증을 성공으로 기록한다.
- Gmail 수신과 Work 전달은 이 Worker만으로 확인하지 않으며 `UNVERIFIED`로 남긴다.
- GitHub 기존 schedule과 동시 운영할 때도 기존 workflow의 `generated-data-write` 동시성 보호를 유지한다.

## 슬롯

| KST | 대상 | 입력 |
|---|---|---|
| 08:00 | Refresh market data | `run_mode=intraday` |
| 09:07~19:07 | Refresh market data | `run_mode=intraday` |
| 20:30 | Refresh market data | `run_mode=final_recovery` |
| 09:17 | Schedule diagnostic | 없음 |
| 23:00 | Build daily market report | 없음 |
| 기존 Research/Snapshots 슬롯 | 각 workflow | 없음 |

20:30 슬롯은 거래일·지수·원천 일봉·게시 기준일 검증이 workflow 내부에서 통과한 경우에만 정상 결과로 취급한다. `RUN_SUCCESS`만으로 FINAL 또는 사용자 전달 성공을 선언하지 않는다.

실제 배포 전에는 동일 슬롯의 GitHub Run 존재 여부, 게시 SHA, Gmail 수신, Work 결과를 별도 시험해야 한다.

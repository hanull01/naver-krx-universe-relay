"""User-facing availability vocabulary for report contracts and renderers.

Raw producer/status codes stay in the contract for lineage and diagnostics.
Only this module's labels and explanations are suitable for Markdown/HTML.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


USER_STATUS_LABELS = {
    "AVAILABLE": "수신됨",
    "AVAILABLE_EMPTY": "수신됨(대상 없음)",
    "OK": "정상 수신",
    "SUCCESS": "정상 수신",
    "PARTIAL": "일부 수신",
    "UNAVAILABLE": "미수신",
    "UNUSABLE": "사용 불가",
    "VERIFIED": "원천 검증됨",
    "SOURCE_VERIFIED": "원천 검증됨",
    "SOURCE_OBSERVED": "원천 수신",
    "DISCOVERED": "수신됨",
    "REMOVED": "제외됨",
    "MAINTAINED": "유지됨",
    "ACTIVE": "활성",
    "WATCH": "관찰",
    "UNVERIFIED": "확인 필요",
    "PENDING": "다음 확인 예정",
    "WAIT": "다음 확인 예정",
    "STALE": "최신 자료 아님",
    "BASELINE_STALE": "이전 자료 최신성 확인 실패",
    "FINAL": "정규장 확정",
    "FAILED": "수집 실패",
    "NOT_DUE": "아직 수집 시각 전",
    "DELAYED": "예정 시각이 지났지만 산출물 미수신",
    "PRODUCER_FAILED": "공급 실행 실패",
    "ARTIFACT_MISSING": "산출물 미수신",
    "ARTIFACT_INVALID": "산출물 검증 실패",
    "CONSUMED": "리포트 반영됨",
    "REPORT_LINK_FAILED": "리포트 연결 실패",
    "INPUT_UNAVAILABLE": "입력 자료 미수신",
    "NO_ELIGIBLE_TARGETS": "선정 가능한 대상 없음",
    "GENERATION_MISMATCH": "입력 세대 불일치",
    "GENERATION_UNAVAILABLE": "입력 세대 확인 불가",
    "INVALID": "입력 검증 실패",
    "INELIGIBLE": "대상 제외",
    "RENDERABLE": "표시 가능",
    "DISABLED_PENDING_RESEARCH_GATE": "현재 자동 산출 기능 없음",
    "MONITORED_UNIVERSE_EMPTY": "관심종목 설정 없음",
    "MONITORED_UNIVERSE_CONFIGURED": "관심종목 설정됨",
    "UPTREND": "상승 추세",
    "DOWNTREND": "하락 추세",
    "SIDEWAYS": "횡보",
    "STRENGTHENING": "강화",
    "WEAKENING": "약화",
    "PERSISTING": "유지",
    "NEW": "신규 진입",
    "NEW_ENTRY": "신규 진입",
    "RANK_UP": "순위 상승",
    "RANK_DOWN": "순위 하락",
    "EXITED": "이탈",
    "COMPARISON_UNAVAILABLE": "비교 불가",
}

REASON_LABELS = {
    "BASELINE_STALE": "이전 날짜 자료가 최신 기준을 충족하지 못했습니다.",
    "FINAL": "정규장 확정 자료가 필요합니다.",
    "FINAL_PENDING": "정규장 확정 자료를 아직 받지 못했습니다.",
    "FINAL_NOT_CONFIRMED": "정규장 확정 여부를 확인할 수 없습니다.",
    "SOURCE_OBSERVED": "원천은 수신됐지만 필요한 검증 정보가 부족합니다.",
    "SOURCE_VERIFIED": "원천 검증은 완료됐습니다.",
    "SOURCE_MISSING": "원천 자료가 수신되지 않았습니다.",
    "NO_CURRENT_SNAPSHOT": "현재 관측 스냅샷이 수신되지 않았습니다.",
    "NO_CURRENT_LEADER_CANDIDATES": "현재 관측에서 대표 종목을 찾지 못했습니다.",
    "LEADER_DATE_MISMATCH": "대표 종목의 관측일이 현재 관측일과 다릅니다.",
    "SOURCE_DATE_MISMATCH": "원천 기준일이 현재 관측일과 다릅니다.",
    "GROUP_MEMBERSHIP_INCOMPLETE": "구성종목 목록이 완전하지 않습니다.",
    "INDUSTRY_MEMBERSHIP_MISSING": "업종 구성종목 자료가 수신되지 않았습니다.",
    "THEME_MEMBERSHIP_MISSING": "테마 구성종목 자료가 수신되지 않았습니다.",
    "RELATIVE_STRENGTH_UNAVAILABLE": "상대강도 계산에 필요한 값이 수신되지 않았습니다.",
    "DERIVED_FEATURES_UNAVAILABLE": "파생 지표 계산 결과가 수신되지 않았습니다.",
    "TRADING_DATE_UNAVAILABLE": "거래일 자료가 수신되지 않았습니다.",
    "TRADING_DATE_UNAVAILABLE_EVIDENCE_INVALID": "거래일 증적을 검증하지 못했습니다.",
    "RESEARCH_SLOT_REQUIRED": "이 리포트에 연결된 리서치 회차가 없습니다.",
    "RESEARCH_SLOT_ARTIFACT_MISSING": "해당 리서치 회차 산출물이 아직 도착하지 않았습니다.",
    "RESEARCH_SLOT_PRODUCER_FAILED": "해당 리서치 회차 수집 단계가 실패했습니다.",
    "RESEARCH_SLOT_ARTIFACT_FAILED": "해당 리서치 회차 산출물 생성이 실패했습니다.",
    "RESEARCH_SLOT_NOT_FRESH": "해당 리서치 회차 산출물이 최신성 검증을 통과하지 못했습니다.",
    "RESEARCH_SLOT_DATE_MISMATCH": "해당 리서치 회차의 기준일이 리포트와 다릅니다.",
    "RESEARCH_SLOT_LINEAGE_MISSING": "해당 리서치 회차의 출처 연결 정보가 없습니다.",
    "RESEARCH_SLOT_NOT_DUE": "해당 리서치 회차의 예정 시각 전입니다.",
    "RESEARCH_SLOT_ARTIFACT_INVALID": "해당 회차 산출물의 형식 또는 lineage를 검증하지 못했습니다.",
    "RESEARCH_REPORT_LINK_FAILED": "산출물은 수신됐지만 리포트 연결에 실패했습니다.",
    "RESEARCH_DATE_MISMATCH": "리서치 자료의 기준일이 리포트와 다릅니다.",
    "RESEARCH_GENERATION_MISMATCH": "리서치 자료의 생성 세대가 리포트와 다릅니다.",
    "DISABLED_PENDING_RESEARCH_GATE": "현재 자동 산출 기능이 제공되지 않습니다.",
    "V2_NOT_PRODUCED": "현재 자동 산출 기능이 없습니다.",
    "SOURCE_UNVERIFIED": "추가 관찰 자료를 생성하는 원천이 연결되지 않았습니다.",
    "MISSING_EVIDENCE": "필요한 원천 증적이 수신되지 않았습니다.",
    "DERIVED_PROJECTION_MISSING": "파생 지표 산출물이 수신되지 않았습니다.",
    "DERIVED_PROJECTION_DATE_MISMATCH": "파생 지표 기준일이 리포트와 다릅니다.",
    "PRODUCER_NOT_CONNECTED": "공급 기능이 연결되지 않았습니다.",
    "INPUT_ARTIFACT_MISSING": "필요한 입력 자료가 없습니다.",
    "PRODUCER_FAILED": "공급 실행이 실패했습니다.",
    "INTEREST_GROUP_NOT_CONFIGURED": "현재 관심 대상이 설정되지 않았습니다.",
    "NO_CURRENT_MEMBERSHIP": "현재 관측 구성종목 자료가 없습니다.",
    "CURRENT_MEMBERSHIP_MISSING": "현재 관측 구성종목 자료가 없습니다.",
    "SYMBOL_MATCH_FAILED": "구성종목과 현재 관측 종목을 연결하지 못했습니다.",
    "LEADER_CALCULATION_FAILED": "대표 주도주 계산에 실패했습니다.",
    "NO_SCHEDULED_COLLECTION": "자료 수집 예정이 없습니다.",
    "DERIVED_ARTIFACT_MISSING": "산출 자료가 없습니다.",
    "RESEARCH_ANALYSIS_MISSING": "리서치 분석 산출물이 없습니다.",
    "CURRENT_BREADTH_UNAVAILABLE": "현재 시장 확산 자료가 없습니다.",
    "CURRENT_SNAPSHOT_MISSING": "현재 관측값이 수신되지 않았습니다.",
    "TECHNICAL_DATA_MISSING": "이동평균 계산 자료가 수신되지 않았습니다.",
    "TREND_DATA_MISSING": "추세 계산 자료가 수신되지 않았습니다.",
    "UNAVAILABLE_TREND_INPUT": "현재가와 이동평균 중 필요한 자료가 모두 수신되지 않아 추세를 계산할 수 없습니다.",
    "AUTO_SELECTION_INPUT_UNAVAILABLE": "추가 관찰 대상을 선정할 현재 관측 입력이 수신되지 않았습니다.",
    "NO_QUALIFIED_ADDITIONAL_TARGETS": "현재 관측에서 품질 기준을 충족한 추가 대상이 없습니다.",
    "ADDITIONAL_OBSERVATION_CONTRACT_LINK_FAILED": "추가 관찰 산출물은 수신됐지만 리포트 연결에 실패했습니다.",
    "CANDIDATE_DETAILS_UNAVAILABLE": "후보 상세 자료가 수신되지 않았습니다.",
    "SELECTION_VALUE_UNAVAILABLE": "후보 선정 기준값이 수신되지 않았습니다.",
    "TECHNICAL_INDICATORS_UNAVAILABLE": "후보 기술지표가 수신되지 않았습니다.",
    "CONDITION_DETAILS_UNAVAILABLE": "조건 상세가 수신되지 않았습니다.",
    "QUOTE_NOT_REQUESTED": "현재가 요청이 실행되지 않았습니다.",
    "QUOTE_UNAVAILABLE": "현재가를 수신하지 못했습니다.",
}


def _key(value: Any) -> str:
    return str(value or "").strip().upper()


def user_status_label(value: Any, fallback: str = "상태 확인 필요") -> str:
    """Map raw lifecycle/status codes to reader-facing wording."""
    if value in (None, ""):
        return fallback
    return USER_STATUS_LABELS.get(_key(value), fallback)


def user_reason_label(value: Any, fallback: str = "원천 자료 확인 필요") -> str:
    """Map a raw reason code without exposing implementation vocabulary."""
    if value in (None, ""):
        return fallback
    text = str(value).strip()
    return REASON_LABELS.get(_key(text), fallback)


def next_check_label(value: Any, fallback: str = "다음 정상 수집 후") -> str:
    if value in (None, ""):
        return fallback
    return str(value)


def availability(
    *,
    status: Any,
    required_data: str,
    received: bool | None,
    producer: str,
    reason: Any = None,
    next_check_at: Any = None,
    producer_run_id: Any = None,
    artifact_path: Any = None,
    freshness_status: Any = None,
    report_slot: Any = None,
    research_slot: Any = None,
    expected_at: Any = None,
    checked_at: Any = None,
    generated_at: Any = None,
    source_date: Any = None,
    observation_date: Any = None,
    scope: Any = None,
    input_count: Any = None,
    matched_count: Any = None,
    output_count: Any = None,
    next_expected_at: Any = None,
) -> dict[str, Any]:
    """Build a stable user-facing availability block.

    ``statusCode`` and ``reasonCode`` retain raw evidence for diagnostics;
    rendered views should use the other fields.
    """
    raw_status = _key(status) or "UNAVAILABLE"
    if received is None:
        received = raw_status in {"AVAILABLE", "OK", "SUCCESS", "VERIFIED", "SOURCE_VERIFIED"}
    if received:
        display_status = "RECEIVED"
        display_label = "수신됨"
    elif raw_status == "PARTIAL":
        display_status = "PARTIAL"
        display_label = "일부 수신"
    else:
        display_status = "NOT_RECEIVED"
        display_label = "미수신"
    result = {
        "status": display_status,
        "label": display_label,
        "requiredData": required_data,
        "received": bool(received),
        "producer": producer,
        "missingReason": user_reason_label(reason),
        "reasonCode": reason,
        "nextCheckAt": next_check_label(next_check_at),
        "statusCode": status,
    }
    # Keep lineage facts available to the contract without rendering raw
    # lifecycle vocabulary in user-facing views.
    for key, value in (
        ("producerRunId", producer_run_id),
        ("artifactPath", artifact_path),
        ("freshnessStatus", freshness_status),
        ("reportSlot", report_slot),
        ("researchSlot", research_slot),
        ("expectedAt", expected_at),
        ("checkedAt", checked_at),
        ("generatedAt", generated_at),
        ("sourceDate", source_date),
        ("observationDate", observation_date),
        ("scope", scope),
        ("inputCount", input_count),
        ("matchedCount", matched_count),
        ("outputCount", output_count),
        ("nextExpectedAt", next_expected_at),
    ):
        if value not in (None, "", [], {}):
            result[key] = value
    return result


def first_reason(row: Mapping[str, Any] | None, *keys: str) -> Any:
    if not isinstance(row, Mapping):
        return None
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return value
    return None

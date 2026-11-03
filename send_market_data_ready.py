"""Send a guarded Gmail signal after production market data reaches GitHub."""
import json
import hashlib
import os
import socket
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
ROOT = Path(__file__).resolve().parent
QUOTES_LITE_PATH = ROOT / 'data/quotes-lite.json'
FULL_MARKET_SUMMARY_PATH = ROOT / 'data/monitoring/latest-summary.json'
FULL_MARKET_QUALITY_PATH = ROOT / 'data/monitoring/latest-quality.json'
SUBJECT_PREFIX = 'MARKET_DATA_READY'
VERIFIED_TRADING_DATE_REASONS = {
    'VERIFIED_CANONICAL_SESSION',
    'VERIFIED_MONITORING_DAILY_FINAL_SESSION',
}
INTRADAY_TRADING_DATE_REASON = 'VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT'
SOURCE_OBSERVED_TRADING_DATE_REASON = 'SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE'

class ReadyValidationError(ValueError):
    """The just-produced quote payload is not safe to signal downstream."""


def _validated_full_market_trading_date(full_market, quality, generated_at):
    """Return verified date metadata without turning an intraday snapshot into a final bar."""
    trading_date = full_market.get('tradingDate')
    summary_evidence = full_market.get('tradingDateEvidence')
    quality_evidence = quality.get('tradingDateEvidence')
    raw_candidates = [e for e in (quality_evidence, summary_evidence)
                      if isinstance(e, dict)]
    candidates = [e for e in raw_candidates if e.get('tradingDate')]
    reason = next((e.get('reason') for e in raw_candidates if e.get('reason')),
                  'TRADING_DATE_EVIDENCE_MISSING')
    if not isinstance(trading_date, str) or not trading_date:
        return None, 'UNAVAILABLE', reason
    if not candidates or any(str(e.get('tradingDate')) != trading_date for e in candidates):
        return None, 'UNAVAILABLE', 'TRADING_DATE_EVIDENCE_MISMATCH'
    reasons = {e.get('reason') for e in candidates}
    statuses = {e.get('status') for e in candidates}
    if reasons == {SOURCE_OBSERVED_TRADING_DATE_REASON}:
        if statuses != {'SOURCE_OBSERVED'} or any(
                e.get('evidenceClass') != 'SOURCE_OBSERVED' for e in candidates
        ):
            return None, 'UNAVAILABLE', 'TRADING_DATE_EVIDENCE_INVALID'
        if quality.get('generatedAt') != generated_at:
            return None, 'UNAVAILABLE', 'TRADING_DATE_EVIDENCE_STALE'
        return trading_date, 'SOURCE_OBSERVED', SOURCE_OBSERVED_TRADING_DATE_REASON
    if any(e.get('status') != 'VERIFIED' for e in candidates):
        return None, 'UNAVAILABLE', reason
    if INTRADAY_TRADING_DATE_REASON in reasons:
        if reasons != {INTRADAY_TRADING_DATE_REASON}:
            return None, 'UNAVAILABLE', 'TRADING_DATE_EVIDENCE_INVALID'
        if quality.get('generatedAt') != generated_at:
            return None, 'UNAVAILABLE', 'TRADING_DATE_EVIDENCE_STALE'
        for evidence in candidates:
            if evidence.get('collectionGeneratedAt') != generated_at:
                return None, 'UNAVAILABLE', 'TRADING_DATE_EVIDENCE_STALE'
            if (evidence.get('evidenceClass') not in {'INTRADAY_SESSION', 'POST_MARKET_SNAPSHOT'}
                    or evidence.get('finalConfirmed') is not False):
                return None, 'UNAVAILABLE', 'TRADING_DATE_EVIDENCE_INVALID'
        return trading_date, 'VERIFIED', INTRADAY_TRADING_DATE_REASON
    if not reasons or not reasons.issubset(VERIFIED_TRADING_DATE_REASONS):
        return None, 'UNAVAILABLE', 'TRADING_DATE_EVIDENCE_INVALID'
    return trading_date, 'VERIFIED', reason

def validate_full_market_publication(summary_path=FULL_MARKET_SUMMARY_PATH,
                                     quality_path=FULL_MARKET_QUALITY_PATH,
                                     collection_started_at=None, current=None):
    """Require the current full-market publication before signaling Work."""
    try:
        summary = json.loads(Path(summary_path).read_text(encoding='utf-8'))
        quality = json.loads(Path(quality_path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadyValidationError('full-market publication metadata is unavailable') from exc
    publication_status = summary.get('publicationStatus')
    if publication_status != quality.get('publicationStatus'):
        raise ReadyValidationError('full-market publication statuses differ')
    if publication_status not in ('SUCCESS', 'COMPLETE_RECONCILIATION_PENDING'):
        raise ReadyValidationError('full-market publication is not successful')
    if publication_status == 'COMPLETE_RECONCILIATION_PENDING':
        full = summary.get('fullMarket', {})
        if (full.get('qualityStatus') != 'WARNING'
                or 'UNIVERSE_COMPOSITION_CHANGED' not in
                (full.get('qualityWarnings') or [])):
            raise ReadyValidationError('pending composition is not validated')
    if summary.get('fullMarket', {}).get('scope') != 'AUTHORITATIVE_FULL_MARKET':
        raise ReadyValidationError('full-market scope is not authoritative')
    full_market = summary.get('fullMarket', {})
    total = full_market.get('markets', {}).get('TOTAL', {})
    count = total.get('stockCount')
    expected = full_market.get('expectedCount', full_market.get('authoritativeCount', count))
    coverage = full_market.get('coverageCount', count)
    quality_status = full_market.get('qualityStatus', 'SUCCESS')
    warning = quality_status in ('WARNING', 'SUCCESS_WITH_WARNING')
    if (not isinstance(count, int) or count <= 0 or total.get('validCount') != count
            or not isinstance(expected, int) or expected <= 0
            or not isinstance(coverage, int) or coverage <= 0
            or coverage * 100 < expected * 99
            or (not warning and coverage != expected)):
        raise ReadyValidationError('full-market coverage is incomplete')
    generated = parse_timestamp(summary.get('generatedAt'), 'fullMarketGeneratedAt')
    if current and generated.date() != current.astimezone(KST).date():
        raise ReadyValidationError('full-market publication is not from today')
    if collection_started_at and generated < parse_timestamp(collection_started_at, 'collectionStartedAt'):
        raise ReadyValidationError('full-market publication predates this collection run')
    trading_date, trading_date_status, trading_reason = _validated_full_market_trading_date(
        full_market, quality, summary.get('generatedAt'))
    return {'mode': 'FULL_MARKET', 'status': 'SUCCESS',
            'publicationStatus': publication_status,
            'scope': 'AUTHORITATIVE_FULL_MARKET',
            'generatedAt': generated.isoformat(), 'count': count,
            'expectedCount': expected, 'coverageCount': coverage,
            'qualityStatus': quality_status,
            'missingCodes': full_market.get('missingCodes', []),
            'unconfirmedMissingCodes': full_market.get('unconfirmedMissingCodes', full_market.get('missingCodes', [])),
            'newCodes': full_market.get('newCodes', []),
            'qualityWarnings': full_market.get('qualityWarnings', []),
            # A Gmail signal remains eligible for downstream diagnostics, but
            # its trading date must never be synthesized from generatedAt.
            'tradingDate': trading_date,
            'tradingDateStatus': trading_date_status,
            'tradingDateEvidenceReason': trading_reason,
            'marketSession': 'REGULAR_MARKET'}

def parse_timestamp(value, field):
    if not isinstance(value, str) or not value:
        raise ReadyValidationError(f'{field} is missing')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReadyValidationError(f'{field} is invalid') from exc
    return parsed.replace(tzinfo=KST) if parsed.tzinfo is None else parsed.astimezone(KST)

def quote_rows(payload):
    """Return the first supported quote-row container from a payload."""
    for key in ('datas', 'quotes', 'rows', 'items'):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    raise ReadyValidationError('quotes rows are incomplete')


def _resolve_manifest_artifact(manifest_path, relative):
    path = Path(manifest_path)
    candidate = Path(str(relative))
    if candidate.is_absolute() or '..' in candidate.parts:
        raise ReadyValidationError('current observation path is unsafe')
    candidates = [ROOT / candidate, path.parent / candidate]
    # Older manifests recorded the contract basename only.  Resolve that
    # declared basename to the current published contract, then verify its
    # manifest SHA below; never select a prior report artifact by filename.
    if candidate.name == 'latest-report-contract.json':
        candidates.append(ROOT / 'data' / 'monitoring' / 'latest-report-contract.json')
    for artifact in candidates:
        if artifact.is_file():
            return artifact
    raise ReadyValidationError('current observation artifact is unavailable')


def validate_current_observation_manifest(manifest_path, expected_commit_sha=None,
                                          expected_run_id=None):
    """Validate and resolve the same-generation Markdown/HTML files."""
    path = Path(manifest_path)
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadyValidationError('current observation manifest is unavailable') from exc
    required = (
        'reportType', 'session', 'reportContractGeneratedAt',
        'tradingDate', 'sourceTime', 'workflowRunId', 'executionHead', 'markdownPath', 'htmlPath',
        'reportContractPath', 'reportContractSha256',
        'markdownSha256', 'htmlSha256', 'reportStatus',
    )
    if manifest.get('reportType') != 'current_observation' or any(
            not manifest.get(key) for key in required):
        raise ReadyValidationError('current observation manifest is incomplete')
    if manifest.get('currentObservationAllowed') is not True:
        raise ReadyValidationError('current observation is not allowed')
    resolved = {}
    for field, digest_field in (('reportContractPath', 'reportContractSha256'),
                                ('markdownPath', 'markdownSha256'),
                                ('htmlPath', 'htmlSha256')):
        artifact = _resolve_manifest_artifact(path, manifest[field])
        try:
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        except OSError as exc:
            raise ReadyValidationError(f'current observation {field} is unavailable') from exc
        if digest != manifest[digest_field]:
            raise ReadyValidationError(f'current observation {field} hash mismatch')
        resolved[field] = artifact
    try:
        contract_payload = json.loads(resolved['reportContractPath'].read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadyValidationError('current observation report contract is unavailable') from exc
    contract = contract_payload.get('reportContract') if isinstance(contract_payload, dict) else None
    contract = contract if isinstance(contract, dict) else contract_payload
    contract_date = str(contract.get('asOf') or contract.get('tradingDate') or '').replace('-', '')
    manifest_date = str(manifest.get('tradingDate') or '').replace('-', '')
    if not manifest_date or contract_date != manifest_date:
        raise ReadyValidationError('current observation report contract trading date mismatch')
    contract_generated = contract.get('observedAt') or contract.get('generatedAt')
    if contract_generated and str(contract_generated) != str(manifest.get('reportContractGeneratedAt')):
        raise ReadyValidationError('current observation report contract generation mismatch')
    if expected_commit_sha and manifest.get('executionHead') in (None, ''):
        raise ReadyValidationError('current observation execution head is missing')
    if expected_run_id and str(manifest.get('workflowRunId')) != str(expected_run_id):
        raise ReadyValidationError('current observation workflow run mismatch')
    if not manifest.get('manifestPath'):
        try:
            manifest['manifestPath'] = path.relative_to(ROOT).as_posix()
        except ValueError:
            manifest['manifestPath'] = path.name
    manifest['_resolvedPaths'] = resolved
    return manifest


def attach_current_observation(message, manifest):
    """Attach only the validated HTML artifact; Markdown is the mail body."""
    resolved = manifest.get('_resolvedPaths') or {}
    attachments = []
    for field, digest_field, filename, maintype, subtype in (
        ('htmlPath', 'htmlSha256', 'report.html', 'text', 'html'),
    ):
        path = resolved.get(field)
        if not isinstance(path, Path) or not path.is_file():
            raise ReadyValidationError(f'current observation {field} is unavailable')
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != manifest[digest_field] or not content:
            raise ReadyValidationError(f'current observation {field} attachment hash mismatch')
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
        attachments.append({'filename': filename, 'sha256': digest, 'bytes': len(content)})
    return attachments


def current_observation_attachment_metadata(manifest):
    """Recheck the HTML attachment and return MIME metadata before sending."""
    resolved = manifest.get('_resolvedPaths') or {}
    attachments = []
    for field, digest_field, filename in (
        ('htmlPath', 'htmlSha256', 'report.html'),
    ):
        path = resolved.get(field)
        if not isinstance(path, Path) or not path.is_file():
            raise ReadyValidationError(f'current observation {field} is unavailable')
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != manifest[digest_field] or not content:
            raise ReadyValidationError(f'current observation {field} attachment hash mismatch')
        attachments.append({'filename': filename, 'sha256': digest, 'bytes': len(content)})
    return attachments

def representative_source_time(payload, rows):
    """Prefer producer metadata; strictly validate legacy row-only payloads."""
    top_level = payload.get('sourceTime')
    if top_level:
        return parse_timestamp(top_level, 'sourceTime')
    row_values = {row.get('sourceTime') for row in rows if isinstance(row, dict) and row.get('sourceTime')}
    if not row_values:
        raise ReadyValidationError('sourceTime is missing')
    if len(row_values) != 1:
        raise ReadyValidationError('row sourceTime values differ')
    return parse_timestamp(row_values.pop(), 'sourceTime')

def validate_quotes(payload, current=None, collection_started_at=None):
    """Return signal metadata only for a complete, fresh current-run payload."""
    if not isinstance(payload, dict):
        raise ReadyValidationError('quotes payload is invalid')
    expected = payload.get('expectedCount'); count = payload.get('count')
    if payload.get('status') != 'ok' or not isinstance(expected, int) or expected <= 0:
        raise ReadyValidationError('quotes payload is not ok')
    coverage = payload.get('coverageCount', count)
    if count != expected or coverage != expected:
        raise ReadyValidationError('quote coverage is incomplete')
    if payload.get('missingCodes') != []:
        raise ReadyValidationError('quotes payload has missing codes')
    generated = parse_timestamp(payload.get('generatedAt'), 'generatedAt')
    current = current or datetime.now(KST)
    if generated.date() != current.astimezone(KST).date():
        raise ReadyValidationError('quotes payload is not from today')
    if collection_started_at:
        started = parse_timestamp(collection_started_at, 'collectionStartedAt')
        if generated < started:
            raise ReadyValidationError('quotes payload predates this collection run')
    rows = quote_rows(payload)
    if len(rows) != expected:
        raise ReadyValidationError('quotes rows are incomplete')
    if any(not isinstance(row, dict) for row in rows):
        raise ReadyValidationError('quotes rows are invalid')
    source_time = representative_source_time(payload, rows)
    if any(row.get('delayTime') != 0 or not row.get('sourceTime') for row in rows):
        raise ReadyValidationError('quotes contain delayed or incomplete rows')
    sessions = {row.get('session') for row in rows}; bases = {row.get('priceBasis') for row in rows}
    if sessions == {'PRE'} and bases == {'PREVIOUS_KRX_CLOSE'}:
        if source_time.date() >= generated.date():
            raise ReadyValidationError('PRE quote does not reference a prior close')
    elif source_time.date() != generated.date():
        raise ReadyValidationError('quotes source time is not today')
    return {'generatedAt': generated.isoformat(), 'sourceTime': source_time.isoformat(),
            'count': count, 'expectedCount': expected, 'coverageCount': coverage,
            'freshCount': payload.get('freshCount'), 'liveCount': payload.get('liveCount'),
            'noAfterTradeCount': payload.get('noAfterTradeCount'),
            'session': next(iter(sessions)) if len(sessions) == 1 else 'MIXED',
            'priceBasis': next(iter(bases)) if len(bases) == 1 else 'MIXED',
            'collectionStartedAt': payload.get('collectionStartedAt')}

def ready_message(metadata, env):
    observation = metadata.get('currentObservation') or {}
    markdown_body = observation.get("markdownBody")
    if not isinstance(markdown_body, str) or not markdown_body.strip():
        raise ReadyValidationError('current observation Markdown body is unavailable')
    generated = parse_timestamp(metadata['generatedAt'], 'generatedAt')
    message = EmailMessage()
    message['Subject'] = f'{SUBJECT_PREFIX} {generated:%Y-%m-%d %H:%M}'
    message['From'] = env.get('MARKET_GMAIL_USERNAME', '')
    message['To'] = env.get('MARKET_GMAIL_TO', '')
    message.set_content(markdown_body.rstrip() + '\n')
    return message

def send_ready_email(message, env, smtp_factory=smtplib.SMTP_SSL):
    username = env.get('MARKET_GMAIL_USERNAME'); password = env.get('MARKET_GMAIL_APP_PASSWORD'); recipient = env.get('MARKET_GMAIL_TO')
    if not all((username, password, recipient)):
        raise RuntimeError('Gmail delivery credentials are unavailable')
    try:
        with smtp_factory('smtp.gmail.com', 465, timeout=20) as smtp:
            smtp.login(username, password); smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        # The SMTP response text may contain sensitive server details.  A
        # numeric status code is sufficient for operational diagnosis.
        code = getattr(exc, 'smtp_code', None)
        suffix = f' (status={code})' if isinstance(code, int) else ''
        raise RuntimeError(f'Gmail SMTP authentication failed{suffix}') from exc
    except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected) as exc:
        raise RuntimeError('Gmail SMTP connection failed') from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise RuntimeError('Gmail recipient rejected') from exc
    except smtplib.SMTPSenderRefused as exc:
        raise RuntimeError('Gmail sender rejected') from exc
    except (socket.timeout, TimeoutError) as exc:
        raise RuntimeError('Gmail SMTP timeout') from exc
    except Exception as exc:
        raise RuntimeError(f'Gmail SMTP unexpected error: {type(exc).__name__}') from exc

def main(env=None, quotes_path=QUOTES_LITE_PATH, smtp_factory=smtplib.SMTP_SSL, current=None,
         summary_path=FULL_MARKET_SUMMARY_PATH, quality_path=FULL_MARKET_QUALITY_PATH):
    env = os.environ if env is None else env
    try:
        if env.get('MARKET_DATA_READY_ALREADY_SENT') == env.get('MARKET_DATA_COMMIT_SHA'):
            raise ReadyValidationError('same publication commit was already signaled')
        full_market = None
        if env.get('FULL_MARKET_REQUIRED') == 'true':
            full_market = validate_full_market_publication(
                summary_path, quality_path, env.get('MARKET_DATA_COLLECTION_STARTED_AT'), current=current)
            metadata = full_market
        else:
            payload = json.loads(Path(quotes_path).read_text(encoding='utf-8'))
            metadata = validate_quotes(payload, current=current, collection_started_at=env.get('MARKET_DATA_COLLECTION_STARTED_AT'))
        manifest_path = env.get('CURRENT_OBSERVATION_MANIFEST_PATH')
        if not manifest_path:
            if env.get('FULL_MARKET_REQUIRED') == 'true':
                raise ReadyValidationError('CURRENT_OBSERVATION_MANIFEST_MISSING')
            observation = {'status': 'UNAVAILABLE', 'reason': 'CURRENT_OBSERVATION_MANIFEST_MISSING', 'attachments': []}
        else:
            observation = validate_current_observation_manifest(
                manifest_path,
                env.get('MARKET_DATA_COMMIT_SHA'),
                env.get('GITHUB_RUN_ID'),
            )
            observation['status'] = observation.get('reportStatus', 'AVAILABLE')
            observation['attachments'] = current_observation_attachment_metadata(observation)
            markdown_path = observation['_resolvedPaths']['markdownPath']
            observation['markdownBody'] = markdown_path.read_text(encoding='utf-8')
        observation['attachmentCount'] = len(observation.get('attachments') or [])
        metadata['currentObservation'] = observation
        message = ready_message(metadata, env)
        if observation.get('attachments'):
            attach_current_observation(message, observation)
        send_ready_email(message, env, smtp_factory=smtp_factory)
    except (OSError, json.JSONDecodeError, ReadyValidationError, RuntimeError) as exc:
        print(f'MARKET_DATA_READY not sent: {exc}'); return 1
    print('MARKET_DATA_READY email sent.'); return 0

if __name__ == '__main__':
    raise SystemExit(main())

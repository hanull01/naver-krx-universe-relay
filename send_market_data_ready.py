"""Send a guarded Gmail signal after production market data reaches GitHub."""
import json
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
SUBJECT_PREFIX = '[MARKET_DATA_READY]'

class ReadyValidationError(ValueError):
    """The just-produced quote payload is not safe to signal downstream."""

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
    if count != expected or payload.get('freshCount') != expected:
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
            'count': count, 'expectedCount': expected, 'freshCount': expected,
            'session': next(iter(sessions)) if len(sessions) == 1 else 'MIXED',
            'priceBasis': next(iter(bases)) if len(bases) == 1 else 'MIXED',
            'collectionStartedAt': payload.get('collectionStartedAt')}

def ready_message(metadata, env):
    commit_sha = env.get('MARKET_DATA_COMMIT_SHA'); run_id = env.get('GITHUB_RUN_ID')
    repository = env.get('GITHUB_REPOSITORY'); server = env.get('GITHUB_SERVER_URL', 'https://github.com').rstrip('/')
    if not all((commit_sha, run_id, repository)):
        raise ReadyValidationError('GitHub run metadata is missing')
    generated = parse_timestamp(metadata['generatedAt'], 'generatedAt')
    message = EmailMessage(); message['Subject'] = f'{SUBJECT_PREFIX} {generated:%Y-%m-%d %H:%M}'
    message['From'] = env.get('MARKET_GMAIL_USERNAME', ''); message['To'] = env.get('MARKET_GMAIL_TO', '')
    lines = [f'repository={repository}', 'workflow=Refresh market data', f'run_id={run_id}',
             f'run_url={server}/{repository}/actions/runs/{run_id}', f'commit_sha={commit_sha}',
             f'generatedAt={metadata["generatedAt"]}', f'sourceTime={metadata["sourceTime"]}',
             f'count={metadata["count"]}', f'expectedCount={metadata["expectedCount"]}',
             f'freshCount={metadata["freshCount"]}', 'missingCodes=[]', 'delayTime=0', 'status=ok',
             f'session={metadata["session"]}', f'priceBasis={metadata["priceBasis"]}']
    if metadata.get('collectionStartedAt'): lines.append(f'collectionStartedAt={metadata["collectionStartedAt"]}')
    if env.get('MARKET_DATA_COLLECTION_FINISHED_AT'): lines.append(f'collectionFinishedAt={env["MARKET_DATA_COLLECTION_FINISHED_AT"]}')
    message.set_content('\n'.join(lines) + '\n'); return message

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

def main(env=None, quotes_path=QUOTES_LITE_PATH, smtp_factory=smtplib.SMTP_SSL, current=None):
    env = os.environ if env is None else env
    try:
        payload = json.loads(Path(quotes_path).read_text(encoding='utf-8'))
        metadata = validate_quotes(payload, current=current, collection_started_at=env.get('MARKET_DATA_COLLECTION_STARTED_AT'))
        send_ready_email(ready_message(metadata, env), env, smtp_factory=smtp_factory)
    except (OSError, json.JSONDecodeError, ReadyValidationError, RuntimeError) as exc:
        print(f'MARKET_DATA_READY not sent: {exc}'); return 1
    print('MARKET_DATA_READY email sent.'); return 0

if __name__ == '__main__':
    raise SystemExit(main())

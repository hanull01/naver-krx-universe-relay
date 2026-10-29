import json
import socket
import smtplib
import tempfile
import unittest
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import send_market_data_ready as ready

class FakeSMTP:
    messages = []; login_args = None
    def __init__(self, *args, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def login(self, username, password): type(self).login_args = (username, password)
    def send_message(self, message): type(self).messages.append(message)

class ReadyMailTests(unittest.TestCase):
    current = datetime(2026, 9, 29, 10, 40, tzinfo=ready.KST)
    def payload(self):
        timestamp = '2026-09-29T10:39:00+09:00'
        return {'status':'ok','count':2,'expectedCount':2,'coverageCount':2,'freshCount':2,'missingCodes':[], 'generatedAt':timestamp,'sourceTime':timestamp,'collectionStartedAt':'2026-09-29T10:32:00+09:00','datas':[{'delayTime':0,'sourceTime':timestamp,'session':'REGULAR','priceBasis':'KRX_REGULAR'},{'delayTime':0,'sourceTime':timestamp,'session':'REGULAR','priceBasis':'KRX_REGULAR'}]}
    def env(self):
        return {'MARKET_GMAIL_USERNAME':'sender@example.com','MARKET_GMAIL_APP_PASSWORD':'secret','MARKET_GMAIL_TO':'trigger@example.com','GITHUB_RUN_ID':'123','GITHUB_REPOSITORY':'hanull01/naver-krx-universe-relay','GITHUB_SERVER_URL':'https://github.com','MARKET_DATA_COMMIT_SHA':'abc123','MARKET_DATA_COLLECTION_STARTED_AT':'2026-09-29T10:32:00+09:00','FULL_MARKET_STATUS':'UNAVAILABLE','FULL_MARKET_FAILURE_REASON':'CURRENT_SNAPSHOT_INCOMPLETE','FULL_MARKET_LAST_PUBLISHED_AT':'2026-10-12T08:00:00+09:00'}
    def test_valid_payload_builds_exact_prefix_and_required_lines(self):
        metadata=ready.validate_quotes(self.payload(),current=self.current,collection_started_at=self.env()['MARKET_DATA_COLLECTION_STARTED_AT']); message=ready.ready_message(metadata,self.env()); self.assertTrue(message['Subject'].startswith('MARKET_DATA_READY ')); self.assertNotIn('[MARKET_DATA_READY]', message['Subject'])
        for line in ('run_id=123','generatedAt=','sourceTime=','count=2','expectedCount=2','freshCount=2','missingCodes=[]','delayTime=0','status=ok'): self.assertIn(line,message.get_content())
        for line in ('fullMarketStatus=UNAVAILABLE','fullMarketFailureReason=CURRENT_SNAPSHOT_INCOMPLETE','fullMarketLastPublishedAt=2026-10-12T08:00:00+09:00','fullMarketDataWarning=Do not use prior full-market data as today data'): self.assertIn(line,message.get_content())
    def test_invalid_coverage_status_delay_and_times_are_blocked(self):
        for changes in ({'count':1},{'missingCodes':['005930']},{'status':'error'},{'generatedAt':None}):
            payload=self.payload(); payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ready.ReadyValidationError): ready.validate_quotes(payload,current=self.current)
        payload=self.payload(); payload['datas'][0]['delayTime']=20
        with self.assertRaises(ready.ReadyValidationError): ready.validate_quotes(payload,current=self.current)
    def test_source_time_prefers_top_level_then_accepts_uniform_row_fallback(self):
        payload=self.payload(); payload['sourceTime']='2026-09-29T10:38:00+09:00'
        self.assertEqual(ready.validate_quotes(payload,current=self.current)['sourceTime'],'2026-09-29T10:38:00+09:00')
        payload=self.payload(); payload.pop('sourceTime')
        self.assertEqual(ready.validate_quotes(payload,current=self.current)['sourceTime'],'2026-09-29T10:39:00+09:00')
    def test_source_time_row_fallback_rejects_differing_or_missing_values(self):
        payload=self.payload(); payload.pop('sourceTime'); payload['datas'][1]['sourceTime']='2026-09-29T10:39:01+09:00'
        with self.assertRaisesRegex(ready.ReadyValidationError,'row sourceTime values differ'): ready.validate_quotes(payload,current=self.current)
        payload=self.payload(); payload.pop('sourceTime')
        for row in payload['datas']: row.pop('sourceTime')
        with self.assertRaisesRegex(ready.ReadyValidationError,'sourceTime is missing'): ready.validate_quotes(payload,current=self.current)
    def test_preclose_prior_business_day_is_allowed_but_non_pre_old_source_is_blocked(self):
        payload=self.payload()
        for row in payload['datas']: row.update(session='PRE',priceBasis='PREVIOUS_KRX_CLOSE',sourceTime='2026-09-26T15:30:00+09:00')
        payload['sourceTime']='2026-09-26T15:30:00+09:00'; self.assertEqual(ready.validate_quotes(payload,current=self.current)['session'],'PRE')
        payload['datas'][0]['session']='REGULAR'
        with self.assertRaises(ready.ReadyValidationError): ready.validate_quotes(payload,current=self.current)
    def test_after_no_trade_uses_coverage_not_fresh_count_for_ready(self):
        payload = self.payload(); payload.update(freshCount=1, liveCount=1, noAfterTradeCount=1)
        for row in payload['datas']:
            row.update(session='AFTER', priceBasis='AFTER_MARKET')
        metadata = ready.validate_quotes(payload, current=self.current)
        self.assertEqual(metadata['coverageCount'], 2)
        self.assertEqual(metadata['noAfterTradeCount'], 1)
        self.assertIn('coverageCount=2', ready.ready_message(metadata, self.env()).get_content())
    def test_main_sends_with_mock_and_never_exposes_secret_on_failure(self):
        FakeSMTP.messages=[]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quotes-lite.json'; path.write_text(json.dumps(self.payload()),encoding='utf-8'); self.assertEqual(ready.main(self.env(),path,FakeSMTP,current=self.current),0)
        self.assertEqual(len(FakeSMTP.messages),1); self.assertEqual(FakeSMTP.login_args,('sender@example.com','secret')); self.assertNotIn('secret',FakeSMTP.messages[0].as_string())
    def test_smtp_failure_hides_exception_and_secret_values(self):
        class FailingSMTP(FakeSMTP):
            def login(self, username, password): raise RuntimeError(f'upstream rejected {password}')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quotes-lite.json'; path.write_text(json.dumps(self.payload()),encoding='utf-8'); output=StringIO()
            with patch('sys.stdout',output): self.assertEqual(ready.main(self.env(),path,FailingSMTP,current=self.current),1)
        self.assertIn('MARKET_DATA_READY not sent: Gmail SMTP unexpected error: RuntimeError',output.getvalue()); self.assertNotIn('secret',output.getvalue())
    def assert_smtp_failure(self, error, expected):
        class FailingSMTP(FakeSMTP):
            def login(self, username, password): raise error
        with self.assertRaisesRegex(RuntimeError, expected) as caught:
            ready.send_ready_email(ready.ready_message(ready.validate_quotes(self.payload(), current=self.current), self.env()),
                                   self.env(), FailingSMTP)
        self.assertNotIn('secret', str(caught.exception))
    def test_smtp_authentication_failure_is_safe_and_includes_only_status_code(self):
        self.assert_smtp_failure(smtplib.SMTPAuthenticationError(535, b'bad password secret'),
                                 r'Gmail SMTP authentication failed \(status=535\)')
    def test_smtp_connection_failure_is_safe(self):
        self.assert_smtp_failure(smtplib.SMTPConnectError(421, b'connection secret'),
                                 'Gmail SMTP connection failed')
    def test_smtp_recipient_rejection_is_safe(self):
        self.assert_smtp_failure(smtplib.SMTPRecipientsRefused({'target@example.com': (550, b'secret')}),
                                 'Gmail recipient rejected')
    def test_smtp_timeout_is_safe(self):
        self.assert_smtp_failure(socket.timeout('secret'), 'Gmail SMTP timeout')
    def test_workflow_sends_only_after_successful_publish(self):
        workflow=(Path(__file__).parent/'.github/workflows/refresh.yml').read_text(encoding='utf-8'); publish=workflow.index('id: publish'); mail=workflow.index('id: ready_email'); self.assertLess(publish,mail); self.assertIn("steps.publish.outputs.published == 'true'",workflow[mail:mail+900]); self.assertIn("steps.full_market.outputs.exit_code == '0'", workflow[mail:mail+900]); self.assertIn("FULL_MARKET_REQUIRED: 'true'", workflow[mail:mail+1400])

    def test_full_market_publication_requires_success_and_authoritative_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); summary = root / 'summary.json'; quality = root / 'quality.json'
            payload = {'publicationStatus': 'SUCCESS', 'generatedAt': '2026-09-29T10:39:00+09:00',
                       'fullMarket': {'scope': 'AUTHORITATIVE_FULL_MARKET', 'markets': {'TOTAL': {'stockCount': 2, 'validCount': 2}}}}
            summary.write_text(json.dumps(payload), encoding='utf-8'); quality.write_text(json.dumps({'publicationStatus': 'SUCCESS'}), encoding='utf-8')
            result = ready.validate_full_market_publication(summary, quality, current=self.current)
            self.assertEqual(result['count'], 2)
            payload['publicationStatus'] = 'COMPLETE_RECONCILIATION_PENDING'; summary.write_text(json.dumps(payload), encoding='utf-8')
            with self.assertRaisesRegex(ready.ReadyValidationError, 'statuses differ'):
                ready.validate_full_market_publication(summary, quality, current=self.current)

    def _write_full_market_evidence(self, root, *, quality_evidence, summary_evidence=None,
                                     trading_date='2026-09-29', generated_at='2026-09-29T10:39:00+09:00'):
        summary = root / 'summary.json'; quality = root / 'quality.json'
        summary.write_text(json.dumps({
            'publicationStatus': 'SUCCESS', 'generatedAt': generated_at,
            'fullMarket': {
                'scope': 'AUTHORITATIVE_FULL_MARKET', 'tradingDate': trading_date,
                'tradingDateEvidence': summary_evidence if summary_evidence is not None else quality_evidence,
                'markets': {'TOTAL': {'stockCount': 2, 'validCount': 2}},
            },
        }), encoding='utf-8')
        quality.write_text(json.dumps({
            'publicationStatus': 'SUCCESS', 'generatedAt': generated_at,
            'tradingDateEvidence': quality_evidence,
        }), encoding='utf-8')
        return summary, quality

    def test_full_market_intraday_evidence_is_verified(self):
        evidence = {
            'tradingDate': '2026-09-29', 'status': 'VERIFIED',
            'reason': 'VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT',
            'evidenceClass': 'INTRADAY_SESSION', 'finalConfirmed': False,
            'collectionGeneratedAt': '2026-09-29T10:39:00+09:00',
        }
        with tempfile.TemporaryDirectory() as directory:
            summary, quality = self._write_full_market_evidence(Path(directory), quality_evidence=evidence)
            result = ready.validate_full_market_publication(summary, quality, current=self.current)
        self.assertEqual(result['tradingDate'], '2026-09-29')
        self.assertEqual(result['tradingDateStatus'], 'VERIFIED')
        self.assertEqual(result['tradingDateEvidenceReason'], evidence['reason'])

    def test_full_market_missing_intraday_evidence_stays_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, quality = self._write_full_market_evidence(
                root, quality_evidence={}, summary_evidence={})
            result = ready.validate_full_market_publication(summary, quality, current=self.current)
        self.assertIsNone(result['tradingDate'])
        self.assertEqual(result['tradingDateStatus'], 'UNAVAILABLE')

    def test_full_market_intraday_date_mismatch_stays_unavailable(self):
        evidence = {
            'tradingDate': '2026-09-28', 'status': 'VERIFIED',
            'reason': 'VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT',
            'evidenceClass': 'INTRADAY_SESSION', 'finalConfirmed': False,
            'collectionGeneratedAt': '2026-09-29T10:39:00+09:00',
        }
        with tempfile.TemporaryDirectory() as directory:
            summary, quality = self._write_full_market_evidence(
                Path(directory), quality_evidence=evidence, trading_date='2026-09-29')
            result = ready.validate_full_market_publication(summary, quality, current=self.current)
        self.assertIsNone(result['tradingDate'])
        self.assertEqual(result['tradingDateEvidenceReason'], 'TRADING_DATE_EVIDENCE_MISMATCH')

    def test_full_market_intraday_stale_collection_evidence_stays_unavailable(self):
        evidence = {
            'tradingDate': '2026-09-29', 'status': 'VERIFIED',
            'reason': 'VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT',
            'evidenceClass': 'INTRADAY_SESSION', 'finalConfirmed': False,
            'collectionGeneratedAt': '2026-09-29T10:38:00+09:00',
        }
        with tempfile.TemporaryDirectory() as directory:
            summary, quality = self._write_full_market_evidence(Path(directory), quality_evidence=evidence)
            result = ready.validate_full_market_publication(summary, quality, current=self.current)
        self.assertIsNone(result['tradingDate'])
        self.assertEqual(result['tradingDateEvidenceReason'], 'TRADING_DATE_EVIDENCE_STALE')

    def test_full_market_wrong_intraday_evidence_is_not_finalized(self):
        evidence = {
            'tradingDate': '2026-09-29', 'status': 'VERIFIED',
            'reason': 'VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT',
            'evidenceClass': 'POST_MARKET_SNAPSHOT', 'finalConfirmed': False,
            'collectionGeneratedAt': '2026-09-29T10:39:00+09:00',
        }
        with tempfile.TemporaryDirectory() as directory:
            summary, quality = self._write_full_market_evidence(Path(directory), quality_evidence=evidence)
            result = ready.validate_full_market_publication(summary, quality, current=self.current)
        self.assertIsNone(result['tradingDate'])
        self.assertEqual(result['tradingDateEvidenceReason'], 'TRADING_DATE_EVIDENCE_INVALID')

    def test_same_commit_is_not_signaled_twice(self):
        FakeSMTP.messages=[]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'quotes-lite.json'; path.write_text(json.dumps(self.payload()),encoding='utf-8')
            env = dict(self.env(), MARKET_DATA_READY_ALREADY_SENT='abc123'); output=StringIO()
            with patch('sys.stdout', output): self.assertEqual(ready.main(env, path, FakeSMTP, current=self.current), 1)
            self.assertIn('already signaled', output.getvalue())

    def test_full_market_primary_ignores_missing_legacy_quotes(self):
        FakeSMTP.messages=[]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / 'summary.json'; quality = root / 'quality.json'
            summary.write_text(json.dumps({'publicationStatus': 'SUCCESS', 'generatedAt': '2026-09-29T10:39:00+09:00',
                'fullMarket': {'scope': 'AUTHORITATIVE_FULL_MARKET', 'markets': {'TOTAL': {'stockCount': 2764, 'validCount': 2764}}}}), encoding='utf-8')
            quality.write_text(json.dumps({'publicationStatus': 'SUCCESS'}), encoding='utf-8')
            env = dict(self.env(), FULL_MARKET_REQUIRED='true')
            missing_quotes = root / 'missing-quotes.json'
            self.assertEqual(ready.main(env, missing_quotes, FakeSMTP, current=self.current,
                                        summary_path=summary, quality_path=quality), 0)
            content = FakeSMTP.messages[-1].get_content()
            self.assertIn('fullMarket.count=2764', content)
            self.assertIn('legacyQuotes.validation=SKIPPED_FULL_MARKET_PRIMARY', content)

    def test_full_market_signal_does_not_infer_trading_date_from_generated_at(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / 'summary.json'; quality = root / 'quality.json'
            summary.write_text(json.dumps({
                'publicationStatus': 'SUCCESS', 'generatedAt': '2026-09-29T10:39:00+09:00',
                'fullMarket': {'scope': 'AUTHORITATIVE_FULL_MARKET',
                    'markets': {'TOTAL': {'stockCount': 2, 'validCount': 2}},
                    'tradingDate': None,
                    'tradingDateEvidence': {'reason': 'CANONICAL_COVERAGE_INCOMPLETE'},
                }}), encoding='utf-8')
            quality.write_text(json.dumps({'publicationStatus': 'SUCCESS'}), encoding='utf-8')
            metadata = ready.validate_full_market_publication(summary, quality, current=self.current)
            self.assertIsNone(metadata['tradingDate'])
            self.assertEqual(metadata['tradingDateStatus'], 'UNAVAILABLE')
            message = ready.ready_message(metadata, dict(self.env(), MARKET_DATA_COMMIT_SHA='abc123'))
            content = message.get_content()
            self.assertIn('fullMarket.tradingDate=UNAVAILABLE', content)
            self.assertIn('fullMarket.tradingDateEvidenceReason=CANONICAL_COVERAGE_INCOMPLETE', content)

    def test_full_market_primary_rejects_stale_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); summary = root / 'summary.json'; quality = root / 'quality.json'
            summary.write_text(json.dumps({'publicationStatus': 'SUCCESS', 'generatedAt': '2026-09-28T10:39:00+09:00',
                'fullMarket': {'scope': 'AUTHORITATIVE_FULL_MARKET', 'markets': {'TOTAL': {'stockCount': 2764, 'validCount': 2764}}}}), encoding='utf-8')
            quality.write_text(json.dumps({'publicationStatus': 'SUCCESS'}), encoding='utf-8')
            env = dict(self.env(), FULL_MARKET_REQUIRED='true')
            with self.assertRaisesRegex(ready.ReadyValidationError, 'not from today'):
                ready.validate_full_market_publication(summary, quality, current=self.current)

    def test_full_market_primary_rejects_incomplete_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); summary = root / 'summary.json'; quality = root / 'quality.json'
            summary.write_text(json.dumps({'publicationStatus': 'SUCCESS',
                'generatedAt': '2026-09-29T10:39:00+09:00',
                'fullMarket': {'scope': 'AUTHORITATIVE_FULL_MARKET',
                    'qualityStatus': 'SUCCESS', 'expectedCount': 2, 'coverageCount': 1,
                    'markets': {'TOTAL': {'stockCount': 1, 'validCount': 1}}}}), encoding='utf-8')
            quality.write_text(json.dumps({'publicationStatus': 'SUCCESS'}), encoding='utf-8')
            with self.assertRaisesRegex(ready.ReadyValidationError, 'coverage is incomplete'):
                ready.validate_full_market_publication(summary, quality, current=self.current)

    def test_small_composition_warning_is_signaled_with_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); summary = root / 'summary.json'; quality = root / 'quality.json'
            summary.write_text(json.dumps({'publicationStatus': 'SUCCESS', 'generatedAt': '2026-09-29T10:39:00+09:00',
                'fullMarket': {'scope': 'AUTHORITATIVE_FULL_MARKET', 'qualityStatus': 'WARNING',
                    'expectedCount': 100, 'coverageCount': 99, 'missingCodes': ['028080'], 'newCodes': [],
                    'qualityWarnings': ['UNIVERSE_COMPOSITION_CHANGED'],
                    'markets': {'TOTAL': {'stockCount': 99, 'validCount': 99}}}}), encoding='utf-8')
            quality.write_text(json.dumps({'publicationStatus': 'SUCCESS'}), encoding='utf-8')
            metadata = ready.validate_full_market_publication(summary, quality, current=self.current)
            self.assertEqual(metadata['qualityStatus'], 'WARNING')
            message = ready.ready_message(metadata, dict(self.env(), MARKET_DATA_COMMIT_SHA='abc123'))
            content = message.get_content()
            self.assertIn('fullMarket.missingCodes=028080', content)
            self.assertIn('fullMarket.qualityStatus=WARNING', content)

    def test_october_19_scale_warning_keeps_ready_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); summary = root / 'summary.json'; quality = root / 'quality.json'
            summary.write_text(json.dumps({'publicationStatus': 'SUCCESS', 'generatedAt': '2026-09-29T10:39:00+09:00',
                'fullMarket': {'scope': 'AUTHORITATIVE_FULL_MARKET', 'qualityStatus': 'WARNING',
                    'expectedCount': 2764, 'coverageCount': 2763, 'missingCodes': ['028080'],
                    'unconfirmedMissingCodes': ['028080'], 'newCodes': [],
                    'qualityWarnings': ['UNIVERSE_COMPOSITION_CHANGED'],
                    'markets': {'TOTAL': {'stockCount': 2763, 'validCount': 2763}}}}), encoding='utf-8')
            quality.write_text(json.dumps({'publicationStatus': 'SUCCESS'}), encoding='utf-8')
            metadata = ready.validate_full_market_publication(summary, quality, current=self.current)
            self.assertEqual(metadata['coverageCount'], 2763)
            self.assertEqual(metadata['unconfirmedMissingCodes'], ['028080'])
            self.assertEqual(metadata['qualityStatus'], 'WARNING')


    def test_pending_composition_publication_is_eligible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "summary.json"
            quality = root / "quality.json"
            payload = {
                "publicationStatus": "COMPLETE_RECONCILIATION_PENDING",
                "generatedAt": "2026-09-29T10:39:00+09:00",
                "fullMarket": {
                    "scope": "AUTHORITATIVE_FULL_MARKET",
                    "qualityStatus": "WARNING",
                    "qualityWarnings": ["UNIVERSE_COMPOSITION_CHANGED"],
                    "expectedCount": 2764,
                    "coverageCount": 2763,
                    "missingCodes": ["028080"],
                    "newCodes": ["0013Y0", "0015M0", "0158S0"],
                    "markets": {"TOTAL": {"stockCount": 2766, "validCount": 2766}}
                }
            }
            summary.write_text(json.dumps(payload), encoding="utf-8")
            quality.write_text(json.dumps({
                "publicationStatus": "COMPLETE_RECONCILIATION_PENDING"
            }), encoding="utf-8")

            result = ready.validate_full_market_publication(
                summary, quality, current=self.current
            )
            self.assertEqual(
                result["publicationStatus"],
                "COMPLETE_RECONCILIATION_PENDING"
            )
            self.assertEqual(result["coverageCount"], 2763)

            content = ready.ready_message(result, self.env()).get_content()
            self.assertIn(
                "fullMarket.publicationStatus=COMPLETE_RECONCILIATION_PENDING",
                content
            )
            self.assertIn("fullMarket.newCodes=0013Y0,0015M0,0158S0", content)

    def test_pending_composition_rejects_failed_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "summary.json"
            quality = root / "quality.json"
            payload = {
                "publicationStatus": "COMPLETE_RECONCILIATION_PENDING",
                "generatedAt": "2026-09-29T10:39:00+09:00",
                "fullMarket": {
                    "scope": "AUTHORITATIVE_FULL_MARKET",
                    "qualityStatus": "FAILURE",
                    "qualityWarnings": ["UNIVERSE_COMPOSITION_CHANGED"],
                    "expectedCount": 2764,
                    "coverageCount": 2763,
                    "markets": {"TOTAL": {"stockCount": 2766, "validCount": 2766}}
                }
            }
            summary.write_text(json.dumps(payload), encoding="utf-8")
            quality.write_text(json.dumps({
                "publicationStatus": "COMPLETE_RECONCILIATION_PENDING"
            }), encoding="utf-8")
            with self.assertRaises(ready.ReadyValidationError):
                ready.validate_full_market_publication(
                    summary, quality, current=self.current
                )

    def test_pending_composition_rejects_missing_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "summary.json"
            quality = root / "quality.json"
            summary.write_text(json.dumps({
                "publicationStatus": "COMPLETE_RECONCILIATION_PENDING",
                "generatedAt": "2026-09-29T10:39:00+09:00",
                "fullMarket": {
                    "scope": "AUTHORITATIVE_FULL_MARKET",
                    "qualityStatus": "WARNING",
                    "expectedCount": 2764,
                    "coverageCount": 2763,
                    "markets": {"TOTAL": {"stockCount": 2763, "validCount": 2763}}
                }
            }), encoding="utf-8")
            quality.write_text(json.dumps({
                "publicationStatus": "COMPLETE_RECONCILIATION_PENDING"
            }), encoding="utf-8")
            with self.assertRaisesRegex(ready.ReadyValidationError, "pending composition is not validated"):
                ready.validate_full_market_publication(summary, quality, current=self.current)

    def test_pending_composition_rejects_status_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "summary.json"
            quality = root / "quality.json"
            summary.write_text(json.dumps({
                "publicationStatus": "COMPLETE_RECONCILIATION_PENDING"
            }), encoding="utf-8")
            quality.write_text(json.dumps({
                "publicationStatus": "SUCCESS"
            }), encoding="utf-8")
            with self.assertRaisesRegex(
                ready.ReadyValidationError, "statuses differ"
            ):
                ready.validate_full_market_publication(summary, quality)

if __name__ == '__main__': unittest.main()

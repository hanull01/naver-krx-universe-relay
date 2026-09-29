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
        return {'MARKET_GMAIL_USERNAME':'sender@example.com','MARKET_GMAIL_APP_PASSWORD':'secret','MARKET_GMAIL_TO':'trigger@example.com','GITHUB_RUN_ID':'123','GITHUB_REPOSITORY':'hanull01/naver-krx-universe-relay','GITHUB_SERVER_URL':'https://github.com','MARKET_DATA_COMMIT_SHA':'abc123','MARKET_DATA_COLLECTION_STARTED_AT':'2026-09-29T10:32:00+09:00'}
    def test_valid_payload_builds_exact_prefix_and_required_lines(self):
        metadata=ready.validate_quotes(self.payload(),current=self.current,collection_started_at=self.env()['MARKET_DATA_COLLECTION_STARTED_AT']); message=ready.ready_message(metadata,self.env()); self.assertTrue(message['Subject'].startswith('[MARKET_DATA_READY]'))
        for line in ('run_id=123','generatedAt=','sourceTime=','count=2','expectedCount=2','freshCount=2','missingCodes=[]','delayTime=0','status=ok'): self.assertIn(line,message.get_content())
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
        workflow=(Path(__file__).parent/'.github/workflows/refresh.yml').read_text(encoding='utf-8'); publish=workflow.index('id: publish'); mail=workflow.index('id: ready_email'); self.assertLess(publish,mail); self.assertIn("steps.publish.outputs.published == 'true'",workflow[mail:mail+500])

if __name__ == '__main__': unittest.main()

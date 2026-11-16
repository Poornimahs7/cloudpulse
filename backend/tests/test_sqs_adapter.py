"""Offline contract tests for the SQS worker path (AWS itself is not contacted)."""
import json
from app import main, sqs_queue


def test_sqs_publish_and_worker_success(monkeypatch):
    sent=[]; deleted=[]
    monkeypatch.setattr(sqs_queue, '_backend', 'sqs')
    monkeypatch.setattr(sqs_queue, 'send', lambda payload, dead_letter=False: sent.append((payload, dead_letter)) or 'sqs-id')
    payload={'device_id':'TEMP-SQS','metric':'temperature','value':22.5,'timestamp':'2026-11-16T10:00:00Z'}
    mid=main.enqueue(payload)
    assert sent and sent[0][0]['message_id']==mid and sent[0][1] is False
    body=json.dumps(sent[0][0])
    monkeypatch.setattr(sqs_queue, 'receive', lambda **kwargs: {'Body':body,'ReceiptHandle':'receipt-1','MessageId':'sqs-id','Attributes':{'ApproximateReceiveCount':'1'}})
    monkeypatch.setattr(sqs_queue, 'delete', lambda receipt, dead_letter=False: deleted.append((receipt,dead_letter)))
    assert main.process_one() is True
    assert deleted == [('receipt-1',False)]
    assert any(r['device_id']=='TEMP-SQS' for r in main.readings())


def test_sqs_invalid_message_is_sent_to_dlq(monkeypatch):
    sent=[]; deleted=[]
    monkeypatch.setattr(sqs_queue, '_backend', 'sqs')
    payload={'message_id':'sqs-invalid-1','device_id':'VIB-SQS','metric':'vibration','value':999,'timestamp':'2026-11-16T10:00:00Z'}
    monkeypatch.setattr(sqs_queue, 'receive', lambda **kwargs: {'Body':json.dumps(payload),'ReceiptHandle':'receipt-2','MessageId':'sqs-id-2','Attributes':{'ApproximateReceiveCount':'1'}})
    monkeypatch.setattr(sqs_queue, 'send', lambda p, dead_letter=False: sent.append((p,dead_letter)) or 'dlq-id')
    monkeypatch.setattr(sqs_queue, 'delete', lambda receipt, dead_letter=False: deleted.append((receipt,dead_letter)))
    assert main.process_one() is True
    assert sent and sent[0][1] is True
    assert 'outside' in sent[0][0]['_cloudpulse_failure']
    assert deleted == [('receipt-2',False)]

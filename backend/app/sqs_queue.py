"""AWS SQS adapter for CloudPulse standard queues.

Credentials are resolved by boto3's default credential chain (environment, shared
credentials file, container/task role, or EC2 instance role). Never hard-code keys.
"""
from __future__ import annotations
import json
import os
import threading
from typing import Any

_backend = os.getenv('CLOUDPULSE_QUEUE_BACKEND', 'sqlite').strip().lower()
_region = os.getenv('AWS_REGION', 'ap-south-1')
_queue_url = os.getenv('AWS_SQS_QUEUE_URL', '').strip()
_dlq_url = os.getenv('AWS_SQS_DLQ_URL', '').strip()
_client = None
_lock = threading.Lock()
_dlq_receipts: dict[str, str] = {}


def enabled() -> bool:
    return _backend == 'sqs'


def client():
    global _client
    if not enabled():
        raise RuntimeError('SQS adapter is not enabled')
    if not _queue_url or not _dlq_url:
        raise RuntimeError('CLOUDPULSE_QUEUE_BACKEND=sqs requires AWS_SQS_QUEUE_URL and AWS_SQS_DLQ_URL')
    if _client is None:
        with _lock:
            if _client is None:
                import boto3
                kwargs: dict[str, Any] = {'region_name': _region}
                endpoint = os.getenv('AWS_SQS_ENDPOINT_URL', '').strip()
                if endpoint:
                    kwargs['endpoint_url'] = endpoint
                _client = boto3.client('sqs', **kwargs)
    return _client


def send(payload: dict[str, Any], *, dead_letter: bool = False) -> str:
    target = _dlq_url if dead_letter else _queue_url
    result = client().send_message(QueueUrl=target, MessageBody=json.dumps(payload, separators=(',', ':')))
    return result['MessageId']


def receive(*, dead_letter: bool = False, wait: int = 1, visibility: int = 60):
    target = _dlq_url if dead_letter else _queue_url
    result = client().receive_message(
        QueueUrl=target, MaxNumberOfMessages=1, WaitTimeSeconds=wait,
        VisibilityTimeout=visibility, AttributeNames=['ApproximateReceiveCount'],
    )
    return (result.get('Messages') or [None])[0]


def delete(receipt_handle: str, *, dead_letter: bool = False) -> None:
    client().delete_message(QueueUrl=_dlq_url if dead_letter else _queue_url, ReceiptHandle=receipt_handle)


def change_visibility(receipt_handle: str, seconds: int) -> None:
    client().change_message_visibility(QueueUrl=_queue_url, ReceiptHandle=receipt_handle, VisibilityTimeout=max(0, min(43200, seconds)))


def approximate_counts() -> dict[str, int]:
    c = client()
    result = {}
    for key, url in (('queued', _queue_url), ('dlq', _dlq_url)):
        attrs = c.get_queue_attributes(QueueUrl=url, AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible'])['Attributes']
        result[key] = int(attrs.get('ApproximateNumberOfMessages', 0))
        if key == 'queued':
            result['processing'] = int(attrs.get('ApproximateNumberOfMessagesNotVisible', 0))
    return result


def list_dlq(limit: int = 10) -> list[dict[str, Any]]:
    c = client()
    result = c.receive_message(QueueUrl=_dlq_url, MaxNumberOfMessages=max(1, min(10, limit)),
                               WaitTimeSeconds=0, VisibilityTimeout=30,
                               AttributeNames=['ApproximateReceiveCount'])
    items = []
    for msg in result.get('Messages', []):
        try:
            payload = json.loads(msg['Body'])
        except Exception:
            payload = {'raw': msg.get('Body', '')}
        mid = str(payload.get('message_id') or msg['MessageId'])
        _dlq_receipts[mid] = msg['ReceiptHandle']
        items.append({'id': mid, 'payload': payload, 'failure': payload.get('_cloudpulse_failure', 'Message quarantined in AWS SQS DLQ'),
                      'retries': int(msg.get('Attributes', {}).get('ApproximateReceiveCount', 1)),
                      'created_at': payload.get('_cloudpulse_created_at'), 'sqs_message_id': msg['MessageId']})
    return items


def take_dlq_receipt(message_id: str) -> str | None:
    return _dlq_receipts.pop(message_id, None)


def queue_urls() -> dict[str, str]:
    return {'queue_url': _queue_url, 'dlq_url': _dlq_url, 'region': _region}

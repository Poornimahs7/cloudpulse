import os, tempfile, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ['CLOUDPULSE_DB'] = tempfile.mktemp(suffix='.db')
os.environ['CLOUDPULSE_ROLE'] = 'api'
from fastapi.testclient import TestClient
from app.main import app, process_one
client=TestClient(app)

def drain():
    for _ in range(20):
        if not process_one(): break

def test_health():
    assert client.get('/health').json()['status']=='ok'

def test_valid_ingestion_and_reading():
    r=client.post('/api/ingest',json={'device_id':'TEMP-001','metric':'temperature','value':24.5,'timestamp':'2026-11-16T10:00:00Z'})
    assert r.status_code==200
    drain()
    assert any(x['device_id']=='TEMP-001' for x in client.get('/api/readings').json())

def test_invalid_message_goes_to_dlq():
    client.post('/api/ingest',json={'device_id':'VIB-003','metric':'vibration','value':999,'timestamp':'2026-11-16T10:00:00Z'})
    drain()
    assert any(x['failure'] for x in client.get('/api/dlq').json())

def test_threshold_validation():
    bad={'backlog_up':5,'cpu_up':80,'backlog_down':2,'min_workers':8,'max_workers':2,'cooldown':10,'auto_scale':True}
    assert client.put('/api/control/thresholds',json=bad).status_code==400

def test_manual_worker_target_is_persisted():
    r=client.post('/api/control/workers',json={'workers':2})
    assert r.status_code==200 and r.json()['desired_workers']==2
    assert client.get('/api/control/thresholds').json()['desired_workers']==2

def test_dlq_retry_requeues_original_message():
    client.post('/api/ingest',json={'device_id':'VIB-003','metric':'vibration','value':999,'timestamp':'2026-11-16T10:00:00Z'})
    drain()
    item=client.get('/api/dlq').json()[0]
    assert client.post(f"/api/dlq/{item['id']}/retry").status_code==200
    assert client.get('/api/queue').json()['queued'] >= 1

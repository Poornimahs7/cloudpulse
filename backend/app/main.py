from __future__ import annotations
import json, os, sqlite3, threading, time, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from app import sqs_queue

DB_PATH = Path(os.getenv('CLOUDPULSE_DB', str(Path(__file__).resolve().parent.parent / 'data' / 'cloudpulse.db')))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LOCK = threading.RLock()
app = FastAPI(title='CloudPulse API', version='1.0.0', description='IoT ingestion, persistent local queue, DLQ and dynamic feedback controller')
app.add_middleware(CORSMiddleware, allow_origins=os.getenv('CORS_ORIGINS','http://localhost:3000').split(','), allow_credentials=True, allow_methods=['*'], allow_headers=['*'])


def connect():
    c=sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False); c.row_factory=sqlite3.Row; return c

@contextmanager
def db():
    c = connect()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def init_db():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',failure TEXT,retries INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,processed_at TEXT);
        CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY AUTOINCREMENT,message_id TEXT UNIQUE,device_id TEXT,metric TEXT,value REAL,measured_at TEXT,stored_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY CHECK(id=1),backlog_up INTEGER NOT NULL,cpu_up REAL NOT NULL,backlog_down INTEGER NOT NULL,min_workers INTEGER NOT NULL,max_workers INTEGER NOT NULL,cooldown INTEGER NOT NULL,auto_scale INTEGER NOT NULL);
        INSERT OR IGNORE INTO settings(id,backlog_up,cpu_up,backlog_down,min_workers,max_workers,cooldown,auto_scale) VALUES(1,30,80,5,1,8,15,1);
        CREATE TABLE IF NOT EXISTS scaling_events(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT,old_workers INTEGER,new_workers INTEGER,reason TEXT,backlog INTEGER);
        ''')
        cols={r['name'] for r in c.execute('PRAGMA table_info(settings)')}
        if 'desired_workers' not in cols: c.execute('ALTER TABLE settings ADD COLUMN desired_workers INTEGER NOT NULL DEFAULT 2')
        if 'max_attempts' not in cols: c.execute('ALTER TABLE settings ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3')
        mcols={r['name'] for r in c.execute('PRAGMA table_info(messages)')}
        if 'claimed_at' not in mcols: c.execute('ALTER TABLE messages ADD COLUMN claimed_at TEXT')
init_db()
STATE={'workers':2,'last_scale':0.0,'simulator':False,'generated':0,'processed':0,'failed':0}

class SensorMessage(BaseModel):
    device_id: str = Field(min_length=3,max_length=40)
    metric: str = Field(min_length=2,max_length=40)
    value: float
    timestamp: datetime
    message_id: str | None = None

class Thresholds(BaseModel):
    backlog_up: int = Field(ge=1,le=100000)
    cpu_up: float = Field(ge=1,le=100)
    backlog_down: int = Field(ge=0)
    min_workers: int = Field(ge=1,le=16)
    max_workers: int = Field(ge=1,le=32)
    cooldown: int = Field(ge=1,le=3600)
    auto_scale: bool = True

class WorkerChange(BaseModel):
    workers: int = Field(ge=1,le=32)


def now(): return datetime.now(timezone.utc).isoformat()
def counts():
    with db() as c:
        return {r['status']:r['n'] for r in c.execute('SELECT status,count(*) n FROM messages GROUP BY status')}
def backlog():
    if sqs_queue.enabled(): return sqs_queue.approximate_counts().get('queued', 0)
    return counts().get('queued',0)
def settings():
    with db() as c: return dict(c.execute('SELECT * FROM settings WHERE id=1').fetchone())
def valid(p: dict[str,Any]):
    required={'device_id','metric','value','timestamp'}
    if not required.issubset(p): return 'Missing required field'
    limits={'temperature':(-40,125),'humidity':(0,100),'vibration':(0,100),'pressure':(0,2000)}
    if p['metric'] not in limits: return 'Unsupported metric type'
    lo,hi=limits[p['metric']]
    if not isinstance(p['value'],(int,float)) or not lo <= p['value'] <= hi: return f"Value outside {p['metric']} range ({lo}..{hi})"
    try: datetime.fromisoformat(str(p['timestamp']).replace('Z','+00:00'))
    except Exception: return 'Invalid timestamp'
    return None

def enqueue(p):
    mid=p.get('message_id') or f"msg_{uuid.uuid4().hex[:12]}"; p['message_id']=mid
    created=now()
    with db() as c:
        cur=c.execute('INSERT OR IGNORE INTO messages(id,payload,status,created_at) VALUES(?,?,?,?)',(mid,json.dumps(p), 'queued',created))
        inserted=cur.rowcount
    if sqs_queue.enabled() and inserted:
        try: sqs_queue.send({**p, '_cloudpulse_created_at': created})
        except Exception as exc:
            with db() as c: c.execute("DELETE FROM messages WHERE id=? AND status='queued'", (mid,))
            raise HTTPException(503, f'AWS SQS publish failed: {exc}')
    STATE['generated']+=1
    return mid


def process_one_sqs():
    msg=sqs_queue.receive(wait=1, visibility=60)
    if not msg: return False
    receipt=msg['ReceiptHandle']
    received=int(msg.get('Attributes',{}).get('ApproximateReceiveCount','1'))
    try:
        p=json.loads(msg['Body'])
        if not isinstance(p, dict): raise ValueError('Message body must be a JSON object')
        mid=str(p.get('message_id') or msg['MessageId'])
        clean={k:v for k,v in p.items() if not k.startswith('_cloudpulse_')}
        with db() as c:
            c.execute('INSERT OR IGNORE INTO messages(id,payload,status,created_at) VALUES(?,?,?,?)', (mid,json.dumps(p),'processing',p.get('_cloudpulse_created_at') or now()))
            c.execute("UPDATE messages SET status='processing',claimed_at=? WHERE id=?", (now(),mid))
        reason=valid(clean)
        cfg=settings()
        if reason or received >= int(cfg.get('max_attempts',3)):
            failure=reason or f'Processing failed after {received} attempts'
            sqs_queue.send({**clean,'_cloudpulse_failure':failure,'_cloudpulse_created_at':p.get('_cloudpulse_created_at') or now()}, dead_letter=True)
            with db() as c: c.execute("UPDATE messages SET status='dlq',failure=?,retries=?,claimed_at=NULL,processed_at=? WHERE id=?", (failure,max(0,received-1),now(),mid))
            sqs_queue.delete(receipt); STATE['failed']+=1; return True
        with db() as c:
            c.execute('INSERT OR IGNORE INTO readings(message_id,device_id,metric,value,measured_at,stored_at) VALUES(?,?,?,?,?,?)', (mid,clean['device_id'],clean['metric'],clean['value'],str(clean['timestamp']),now()))
            c.execute("UPDATE messages SET status='processed',processed_at=?,claimed_at=NULL WHERE id=?", (now(),mid))
        sqs_queue.delete(receipt); STATE['processed']+=1; return True
    except Exception as exc:
        try:
            p=json.loads(msg.get('Body','{}'))
            mid=str(p.get('message_id') or msg.get('MessageId')) if isinstance(p,dict) else str(msg.get('MessageId'))
            with db() as c: c.execute("UPDATE messages SET status='queued',retries=?,claimed_at=NULL,failure=? WHERE id=?", (max(0,received-1),str(exc)[:500],mid))
            sqs_queue.change_visibility(receipt,min(60,2**min(received,6)))
        except Exception:
            # Leave the SQS message undeleted; visibility timeout makes it available again.
            pass
        return False

def process_one():
    if sqs_queue.enabled(): return process_one_sqs()
    # Claim inside a write transaction so multiple worker threads cannot take the same row.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        stale=(datetime.now(timezone.utc).timestamp()-60)
        stale_iso=datetime.fromtimestamp(stale,timezone.utc).isoformat()
        c.execute("UPDATE messages SET status='queued',claimed_at=NULL WHERE status='processing' AND claimed_at < ?",(stale_iso,))
        row=c.execute("SELECT * FROM messages WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not row: return False
        claimed=now()
        cur=c.execute("UPDATE messages SET status='processing',claimed_at=? WHERE id=? AND status='queued'",(claimed,row['id']))
        if not cur.rowcount: return False
    try:
        p=json.loads(row['payload']); reason=valid(p)
        with db() as c:
            if reason:
                c.execute("UPDATE messages SET status='dlq',failure=?,retries=retries+1,claimed_at=NULL,processed_at=? WHERE id=?",(reason,now(),row['id']))
                STATE['failed']+=1
            else:
                c.execute("INSERT OR IGNORE INTO readings(message_id,device_id,metric,value,measured_at,stored_at) VALUES(?,?,?,?,?,?)",(row['id'],p['device_id'],p['metric'],p['value'],str(p['timestamp']),now()))
                c.execute("UPDATE messages SET status='processed',processed_at=?,claimed_at=NULL WHERE id=?",(now(),row['id']))
                STATE['processed']+=1
        return True
    except Exception:
        with db() as c:
            c.execute("UPDATE messages SET status='queued',retries=retries+1,claimed_at=NULL,failure='Transient worker error' WHERE id=?",(row['id'],))
        return False

def worker_loop(stop):
    while not stop.is_set():
        worked=False
        try:
            for _ in range(4):
                if process_one(): worked=True
                else: break
        except Exception as exc:
            print(f'queue consumer error: {exc}', flush=True)
            stop.wait(2)
        if not worked: stop.wait(.25)

WORKER_STOPS=[]
def resize_workers(n):
    n=max(1,min(32,n))
    while len(WORKER_STOPS)<n:
        ev=threading.Event(); t=threading.Thread(target=worker_loop,args=(ev,),daemon=True); WORKER_STOPS.append((ev,t)); t.start()
    while len(WORKER_STOPS)>n:
        ev,t=WORKER_STOPS.pop(); ev.set()
    STATE['workers']=n
if os.getenv('CLOUDPULSE_ROLE', 'api') == 'api':
    STATE['workers'] = 0
else:
    resize_workers(STATE['workers'])

def control_loop():
    while True:
        try:
            cfg=settings(); q=backlog(); cur=STATE['workers']; target=max(cfg['min_workers'], min(cfg['max_workers'], cfg.get('desired_workers', cur))); reason='Manual target applied' if target != cur else ''
            try:
                import psutil
                cpu_now=psutil.cpu_percent(interval=0.05)
            except Exception:
                cpu_now=0.0
            if cfg['auto_scale'] and time.time()-STATE['last_scale']>=cfg['cooldown']:
                if (q>=cfg['backlog_up'] or cpu_now>=cfg['cpu_up']) and target<cfg['max_workers']:
                    target=target+1; reason='Queue backlog threshold exceeded' if q>=cfg['backlog_up'] else 'CPU utilization threshold exceeded'
                elif q<=cfg['backlog_down'] and cpu_now<cfg['cpu_up']*0.6 and target>cfg['min_workers']:
                    target=target-1; reason='Backlog and CPU below scale-down thresholds'
            if target!=cur:
                resize_workers(target); STATE['last_scale']=time.time()
                with db() as c:
                    c.execute('UPDATE settings SET desired_workers=? WHERE id=1',(target,))
                    c.execute('INSERT INTO scaling_events(at,old_workers,new_workers,reason,backlog) VALUES(?,?,?,?,?)',(now(),cur,target,reason or 'Manual target applied',q))
        except Exception: pass
        time.sleep(2)
if os.getenv('CLOUDPULSE_ROLE', 'api') == 'worker':
    threading.Thread(target=control_loop,daemon=True).start()

@app.get('/health')
def health():
    result={'status':'ok','service':'cloudpulse-api','database':'sqlite-persistent','desired_workers':settings()['desired_workers'],'queue_backend':'aws-sqs' if sqs_queue.enabled() else 'sqlite'}
    if sqs_queue.enabled():
        try: sqs_queue.approximate_counts()
        except Exception as exc: raise HTTPException(503, f'AWS SQS health check failed: {exc}')
    return result
@app.get('/api/status')
def status():
    c=counts();
    try:
        import psutil
        cpu=psutil.cpu_percent(interval=.05); mem=psutil.virtual_memory().percent
    except Exception: cpu=0; mem=0
    live=sqs_queue.approximate_counts() if sqs_queue.enabled() else c
    return {'queue_backlog':live.get('queued',0),'processing':live.get('processing',0),'processed':c.get('processed',0),'dlq':live.get('dlq',c.get('dlq',0)),'workers':settings()['desired_workers'],'cpu_percent':cpu,'memory_percent':mem,'generated_total':sum(c.values()),'processed_total':c.get('processed',0),'failed_total':c.get('dlq',0),'mode':'aws-sqs' if sqs_queue.enabled() else 'persistent-local'}
@app.post('/api/ingest')
def ingest(m:SensorMessage):
    p=m.model_dump(mode='json'); mid=enqueue(p); return {'accepted':True,'message_id':mid,'queue_backlog':backlog()}
@app.post('/api/ingest/batch')
def ingest_batch(items:list[SensorMessage]):
    if len(items)>500: raise HTTPException(413,'Batch limit is 500')
    ids=[enqueue(x.model_dump(mode='json')) for x in items]; return {'accepted':len(ids),'message_ids':ids}
@app.get('/api/queue')
def queue_status():
    c=counts()
    if sqs_queue.enabled():
        live=sqs_queue.approximate_counts()
        return {'queued':live.get('queued',0),'processing':live.get('processing',0),'processed':c.get('processed',0),'dlq':live.get('dlq',0),'adapter':'aws-sqs'}
    return {'queued':c.get('queued',0),'processing':c.get('processing',0),'processed':c.get('processed',0),'dlq':c.get('dlq',0),'adapter':'sqlite-persistent'}

@app.get('/api/readings')
def readings(limit:int=100):
    with db() as c: rows=c.execute('SELECT * FROM readings ORDER BY id DESC LIMIT ?', (max(1,min(limit,1000)),)).fetchall()
    return [dict(r) for r in rows]
@app.get('/api/dlq')
def dlq():
    if sqs_queue.enabled():
        try: return sqs_queue.list_dlq(10)
        except Exception as exc: raise HTTPException(503, f'AWS SQS DLQ read failed: {exc}')
    with db() as c: rows=c.execute("SELECT * FROM messages WHERE status='dlq' ORDER BY created_at DESC").fetchall()
    return [{'id':r['id'],'payload':json.loads(r['payload']),'failure':r['failure'],'retries':r['retries'],'created_at':r['created_at']} for r in rows]
@app.post('/api/dlq/{message_id}/retry')
def retry(message_id:str):
    if sqs_queue.enabled():
        with db() as c: r=c.execute('SELECT payload FROM messages WHERE id=?', (message_id,)).fetchone()
        receipt=sqs_queue.take_dlq_receipt(message_id)
        if not receipt: raise HTTPException(404, 'Refresh the DLQ list before retrying this message')
        if not r: raise HTTPException(404, 'Original message metadata not found')
        payload={k:v for k,v in json.loads(r['payload']).items() if not k.startswith('_cloudpulse_')}
        sqs_queue.send(payload); sqs_queue.delete(receipt, dead_letter=True)
        with db() as c: c.execute("UPDATE messages SET status='queued',failure=NULL,claimed_at=NULL WHERE id=?", (message_id,))
        return {'requeued':True,'message_id':message_id,'queue_backlog':backlog()}
    with db() as c:
        r=c.execute("SELECT * FROM messages WHERE id=? AND status='dlq'",(message_id,)).fetchone()
        if not r: raise HTTPException(404,'DLQ message not found')
        c.execute("UPDATE messages SET status='queued',failure=NULL WHERE id=?",(message_id,))
    return {'requeued':True,'message_id':message_id,'queue_backlog':backlog()}
@app.delete('/api/dlq/{message_id}')
def discard(message_id:str):
    if sqs_queue.enabled():
        receipt=sqs_queue.take_dlq_receipt(message_id)
        if not receipt: raise HTTPException(404, 'Refresh the DLQ list before discarding this message')
        sqs_queue.delete(receipt, dead_letter=True)
        with db() as c: c.execute('DELETE FROM messages WHERE id=?', (message_id,))
        return {'discarded':True,'message_id':message_id}
    with db() as c:
        cur=c.execute("DELETE FROM messages WHERE id=? AND status='dlq'",(message_id,))
        if not cur.rowcount: raise HTTPException(404,'DLQ message not found')
    return {'discarded':True,'message_id':message_id}
@app.get('/api/control/thresholds')
def get_thresholds(): return settings()
@app.put('/api/control/thresholds')
def put_thresholds(v:Thresholds):
    if v.min_workers>v.max_workers: raise HTTPException(400,'min_workers cannot exceed max_workers')
    with db() as c: c.execute('UPDATE settings SET backlog_up=?,cpu_up=?,backlog_down=?,min_workers=?,max_workers=?,cooldown=?,auto_scale=? WHERE id=1',(v.backlog_up,v.cpu_up,v.backlog_down,v.min_workers,v.max_workers,v.cooldown,int(v.auto_scale)))
    with db() as c: c.execute('UPDATE settings SET desired_workers=MIN(MAX(desired_workers,?),?) WHERE id=1',(v.min_workers,v.max_workers))
    return settings()
@app.post('/api/control/workers')
def set_workers(v:WorkerChange):
    cfg=settings()
    if not cfg['min_workers']<=v.workers<=cfg['max_workers']: raise HTTPException(400,'Requested workers outside configured bounds')
    old=cfg['desired_workers']
    with db() as c:
        c.execute('UPDATE settings SET desired_workers=? WHERE id=1',(v.workers,))
        c.execute('INSERT INTO scaling_events(at,old_workers,new_workers,reason,backlog) VALUES(?,?,?,?,?)',(now(),old,v.workers,'Manual operator action',backlog()))
    return {'workers':v.workers,'desired_workers':v.workers}
@app.get('/api/control/events')
def scaling_events():
    with db() as c: return [dict(r) for r in c.execute('SELECT * FROM scaling_events ORDER BY id DESC LIMIT 100')]
@app.post('/api/simulator/start')
def simulator_start():
    STATE['simulator']=True
    return {'running':True}
@app.post('/api/simulator/stop')
def simulator_stop():
    STATE['simulator']=False
    return {'running':False}
@app.get('/api/simulator/status')
def simulator_status():
    return {'running': STATE['simulator']}

@app.post('/api/simulator/tick')
def simulator_tick(burst:bool=False):
    import random
    devs=[('TEMP-001','temperature',round(random.uniform(20,29),2)),('HUM-002','humidity',round(random.uniform(40,65),2)),('VIB-003','vibration',round(random.uniform(.01,.2),3))]
    if burst: devs=devs*20
    ids=[]
    for d,m,v in devs: ids.append(enqueue({'device_id':d,'metric':m,'value':v,'timestamp':now()}))
    return {'generated':len(ids),'message_ids':ids,'queue_backlog':backlog()}
@app.get('/api/worker/health')
def worker_health():
    return {'role': os.getenv('CLOUDPULSE_ROLE', 'api'), 'workers': STATE['workers'], 'status': 'ok'}

@app.get('/api/containers')
def containers():
    # Deliberately does not expose a Docker socket or arbitrary command execution.
    return {'mode':'compose-observed','note':'Container lifecycle is managed by docker compose from the host; this API does not expose privileged Docker control.','services':[{'name':'cloudpulse-api','role':'FastAPI ingestion and control plane'},{'name':'cloudpulse-worker','role':'Queue consumer pool'},{'name':'cloudpulse-simulator','role':'IoT load generator'}]}

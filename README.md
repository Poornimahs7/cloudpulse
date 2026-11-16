# CloudPulse

CloudPulse is an IoT ingestion and observability project demonstrating **Module 3: OS-level virtualization with Docker containers** and **Module 4: feedback control based on dynamic thresholds**.

## Services and architecture

- `cloudpulse-web`: existing Next.js dashboard.
- `cloudpulse-api`: FastAPI ingestion, queue, DLQ and control API.
- `cloudpulse-worker`: separate Python consumer container. It runs a bounded worker thread pool and polls the configured queue adapter.
- `cloudpulse-simulator`: separate producer container, controlled by API start/stop endpoints.
- `cloudpulse-data`: named Docker volume containing the SQLite WAL database.

The local queue uses SQLite WAL with atomic `BEGIN IMMEDIATE` claims so concurrent consumer threads cannot claim the same message. The consumer marks a message as processing before validating it, writes valid readings idempotently, routes invalid readings to the DLQ, and recovers claims older than 60 seconds after a worker crash. Transient processing failures are retried up to `max_attempts`. DLQ retry returns the original payload to the main queue.

Worker scaling is applied to the consumer pool inside the dedicated worker container. The API writes the desired worker count to persistent settings; the worker-side feedback controller applies the target and performs threshold-based scale-up/down. This is not Docker replica scaling.

## Run with Docker Desktop (recommended)

Requires Docker Desktop with Linux containers enabled. From the project root:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f cloudpulse-api cloudpulse-worker cloudpulse-simulator
```

Open `http://localhost:3000` and API docs at `http://localhost:8000/docs`. Stop with `docker compose down`; data remains in the named volume. To erase the database, use `docker compose down -v` (destructive).

## Enable AWS SQS (cloud queue mode)

1. In the AWS Console, choose the same AWS region you will use in `.env` (default `ap-south-1`). Create two **Standard** queues: `cloudpulse-events` and `cloudpulse-events-dlq`. FIFO queues are not supported by this adapter.
2. Copy both queue URLs. Create a local `.env` in the project root (copy `.env.example`) and set:

```dotenv
CLOUDPULSE_QUEUE_BACKEND=sqs
AWS_REGION=ap-south-1
AWS_SQS_QUEUE_URL=https://sqs.ap-south-1.amazonaws.com/YOUR_ACCOUNT_ID/cloudpulse-events
AWS_SQS_DLQ_URL=https://sqs.ap-south-1.amazonaws.com/YOUR_ACCOUNT_ID/cloudpulse-events-dlq
AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY=YOUR_SECRET_ACCESS_KEY
# AWS_SESSION_TOKEN=...  # only for temporary credentials
```

Use a dedicated IAM identity with least-privilege access to these two queue ARNs. It needs `sqs:SendMessage`, `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:ChangeMessageVisibility`, and `sqs:GetQueueAttributes` on the main queue and DLQ. Do not commit `.env` or paste AWS keys into source code. On AWS-hosted deployments, prefer an instance/task role instead of long-lived access keys.

3. Rebuild and start the services:

```powershell
docker compose down
docker compose up --build -d
docker compose ps
docker compose logs -f cloudpulse-api cloudpulse-worker
```

4. Confirm `GET http://localhost:8000/health` reports `queue_backend: aws-sqs`, then post a sensor message to `POST /api/ingest`. `GET /api/queue` reports approximate SQS queue counts; `GET /api/readings` shows processed readings. Invalid messages and messages that exhaust `max_attempts` are sent to the configured DLQ.

The adapter explicitly publishes exhausted messages to the configured DLQ and deletes them from the main queue; it does not require an SQS redrive policy. SQS queue counts are approximate by AWS design. AWS resources and credentials are not created by Docker Compose; you must create/configure them in your AWS account.

## Run locally without Docker

Terminal 1 (API):

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Terminal 2 (worker):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.worker_service
```

Terminal 3 (simulator service):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:CLOUDPULSE_INTERNAL_API = "http://127.0.0.1:8000"
python -m app.simulator_service
```

Terminal 4 (frontend, project root): `corepack pnpm install` then `corepack pnpm dev`.

## Queue and simulator operations

- `POST /api/ingest` and `POST /api/ingest/batch` enqueue messages.
- `GET /api/queue` returns queued, processing, processed and DLQ counts.
- `GET /api/readings` returns successfully validated readings.
- `GET /api/dlq` lists quarantined messages.
- `POST /api/dlq/{id}/retry` requeues the original payload.
- `DELETE /api/dlq/{id}` discards a quarantined message.
- `POST /api/simulator/start` and `/api/simulator/stop` control the continuous simulator.
- `POST /api/simulator/tick?burst=true` generates a one-off burst.
- `GET/PUT /api/control/thresholds` reads/updates dynamic thresholds.
- `POST /api/control/workers` sets the desired consumer count.
- `GET /api/control/events` returns scaling history.

## Tests

```powershell
cd backend
pip install -r requirements-dev.txt
pytest -q
```

The test suite covers health, valid ingestion, invalid-message DLQ routing, threshold validation, persistent worker target changes and DLQ retry. Python syntax checks and an API-to-separate-worker process integration check were also run while preparing this package. Docker itself was not available in the build environment, so run `docker compose up --build` on a machine with Docker Desktop to verify image builds and container startup.

## Important boundaries

- SQLite is the default local queue; AWS SQS is available when explicitly enabled and configured as described above. SQLite metadata/readings storage is intended for a single-host demo and should be replaced with a managed database for multi-host production.
- Supabase schema is provided separately but the local backend currently uses SQLite; cloud credentials are not included.
- Docker socket access is not mounted into the API or frontend. Container lifecycle operations must be managed by an authorized host operator.
- Docker containers provide OS-level virtualization and share the host kernel; they are not full virtual machines.

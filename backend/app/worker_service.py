"""Dedicated queue-consumer container. Scaling is a bounded thread pool inside this container."""
import os, time
os.environ['CLOUDPULSE_ROLE'] = 'worker'
from app.main import STATE, resize_workers, settings

def run():
    # main.py starts the feedback controller and the initial consumer pool for this role.
    print('CloudPulse dedicated worker service started', flush=True)
    while True:
        try:
            cfg = settings()
            target = max(1, min(int(cfg['max_workers']), int(os.getenv('WORKER_POOL_LIMIT', '16'))))
            # Controller owns actual resize decisions; this loop provides liveness and reports current state.
            if STATE['workers'] < 1:
                resize_workers(max(1, min(int(cfg['min_workers']), target)))
            print(f"worker_pool={STATE['workers']} backlog={__import__('app.main', fromlist=['backlog']).backlog()}", flush=True)
        except Exception as exc:
            print(f'worker service monitor error: {exc}', flush=True)
        time.sleep(30)

if __name__ == '__main__':
    run()

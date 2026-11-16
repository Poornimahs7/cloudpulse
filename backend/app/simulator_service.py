"""Continuous sensor producer controlled by API start/stop endpoints."""
import json, os, random, time, urllib.request
API = os.getenv('CLOUDPULSE_INTERNAL_API', 'http://cloudpulse-api:8000')

def request(path):
    with urllib.request.urlopen(API + path, timeout=5) as response:
        return json.loads(response.read().decode())

def run():
    print('CloudPulse sensor simulator service started', flush=True)
    while True:
        try:
            state = request('/api/simulator/status')
            if state.get('running'):
                request('/api/simulator/tick?burst=false')
                time.sleep(float(os.getenv('SIMULATOR_INTERVAL_SECONDS', '1')))
            else:
                time.sleep(.5)
        except Exception as exc:
            print(f'simulator waiting for API: {exc}', flush=True)
            time.sleep(2)

if __name__ == '__main__':
    run()

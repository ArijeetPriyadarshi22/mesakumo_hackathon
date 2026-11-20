"""Optional real-loopback integration check; uses synthetic data and no live LLM."""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parent.parent

def main():
    client = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(dir=ROOT, prefix='smoke-') as data_dir:
        env = dict(os.environ, MESAKUMO_DATA_DIR=data_dir, MESAKUMO_PORT='18502', MESAKUMO_LLM_PROVIDER='disabled')
        app = None
        def start():
            return subprocess.Popen([str(ROOT / '.venv/bin/python'), '-m', 'mesakumo'], cwd=ROOT, env=env, stdout=subprocess.DEVNULL)
        def get(path='/'):
            return client.open('http://127.0.0.1:18502'+path, timeout=5)
        def ready():
            for _ in range(40):
                try:
                    with get() as response:
                        return response.read().decode()
                except OSError:
                    time.sleep(.1)
            raise RuntimeError('Server did not become ready')
        try:
            app = start()
            home = ready()
            token = re.search(r'name="token" value="([^"]+)"', home)[1]
            def post(fields, multipart=False):
                if multipart:
                    boundary = 'mesakumo-synthetic-test'
                    chunks = []
                    for key, value in fields.items():
                        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
                    chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic_tender.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode() + (ROOT / 'tests/fixtures/synthetic_tender.txt').read_bytes() + b'\r\n')
                    chunks.append(f'--{boundary}--\r\n'.encode())
                    data, content_type = b''.join(chunks), f'multipart/form-data; boundary={boundary}'
                else:
                    data, content_type = urlencode(fields, doseq=True).encode(), 'application/x-www-form-urlencoded'
                return client.open(Request('http://127.0.0.1:18502/', data=data, headers={'Content-Type': content_type}), timeout=5)
            with post(dict(token=token, action='new_project', name='SYNTHETIC HTTP fixture', scope='Known synthetic tender only')) as response:
                key = parse_qs(urlsplit(response.url).query)['project'][0]
            fields = dict(token=token, project=key, revision=1, action='upload', stage=0, role='tender')
            with post(fields, multipart=True) as response:
                page = response.read().decode()
                assert 'Lines 1–5' in page and 'must deliver two discovery workshops' in page
                source = re.search(r'Passage ID: ([^<]+)', page)[1]
            with post(dict(token=token, project=key, revision=2, stage=1, action='save_requirement', text='Must deliver two discovery workshops', kind='deliverable', sources=source, constraints='two', reviewer='Synthetic tester', note='Manual fixture addition')) as response:
                page = response.read().decode()
                assert 'Manual fixture addition' in page and 'Unreviewed' in page
            with post(dict(token=token, project=key, revision=3, stage=1, action='review_checklist', reviewer='Synthetic tester')) as response:
                assert 'Reviewed' in response.read().decode()
            try:
                post(dict(token=token, project=key, revision=4, stage=1, action='extract'))
                raise AssertionError('Disabled model should reject extraction')
            except HTTPError as error:
                assert error.code == 400 and b'LLM access is disabled' in error.read()
            app.terminate(); app.wait(timeout=5)
            app = start(); ready()
            with get(f'/?project={key}&stage=1') as response:
                page = response.read().decode()
                assert 'Manual fixture addition' in page and 'Reviewed' in page
            print('PASS: real HTTP create, multipart upload, inspect source, manual requirement, checklist review, disabled-call failure, process restart and reopen.')
        finally:
            if app:
                app.terminate(); app.wait(timeout=5)

if __name__ == '__main__':
    main()

"""Exercise actual HTTP handlers without requiring sandbox socket permission."""
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode
from mesakumo.persistence import ProjectStore
from mesakumo.ui.server import serve

class Socket:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.output = io.BytesIO()
    def makefile(self, *args, **kwargs):
        return self.request
    def sendall(self, value):
        self.output.write(value)

class UITests(unittest.TestCase):
    def test_create_navigate_and_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(Path(folder) / 'state.sqlite3')
            outer = self
            class Server:
                server_port = 8501
                def __init__(self, address, handler):
                    self.handler = handler
                def request(self, path='/', data=None):
                    payload = urlencode(data).encode() if data else b''
                    method = 'POST' if data else 'GET'
                    request = (f'{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Length: {len(payload)}\r\n\r\n').encode() + payload
                    socket = Socket(request)
                    self.handler(socket, ('127.0.0.1', 1234), self)
                    return socket.output.getvalue().decode()
                def serve_forever(self):
                    home = self.request()
                    token = re.search(r'name="token" value="([^"]+)"', home)[1]
                    response = self.request(data=dict(token=token, action='create', stage=0))
                    outer.assertIn('303', response)
                    key = store.list_projects()[0][0]
                    for stage in range(4):
                        response = self.request(f'/?project={key}&stage={stage}')
                        outer.assertIn('200 OK', response)
                        outer.assertIn('SYNTHETIC EXERCISE', response)
                        outer.assertIn('unavailable', response)
                    reopened = ProjectStore(store.path).load(key)
                    outer.assertTrue(reopened.synthetic)
                    denied = self.request(data=dict(token='invalid', action='create'))
                    outer.assertIn('400 Bad Request', denied)
                def server_close(self):
                    pass
            with patch('mesakumo.ui.server.HTTPServer', Server):
                serve(store)

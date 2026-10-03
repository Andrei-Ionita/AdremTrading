"""Open a loopback-only, SSH-protected human verification page for the reader."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser


HTML = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>FusionSolar Verification</title><style>
body{font:16px system-ui;margin:32px auto;padding:0 20px;max-width:1080px;color:#202429;background:#f6f8fa}
h1{font-size:24px} img{width:100%;height:auto;background:white;border:1px solid #d8dee4}
[hidden]{display:none!important}
form{display:flex;gap:12px;margin-top:18px;align-items:center}input,button{font:inherit;padding:10px;border:1px solid #b5bec8;border-radius:4px}
button{background:#087f8c;color:white;cursor:pointer}#error{color:#b32222;min-height:24px}#done{color:#187440}
</style></head><body><h1>FusionSolar Verification</h1><p id="asset">Connecting to reader...</p>
<p id="done"></p><img id="image" hidden alt="FusionSolar verification challenge">
<form id="form" hidden><label for="code">Verification code</label><input id="code" autocomplete="off" maxlength="16" required>
<button type="submit">Verify</button></form><p id="error"></p>
<script>const csrf=CSRF_VALUE;let busy=false;
async function update(command,code){if(busy)return;busy=true;document.querySelector('button').disabled=true;
try{const r=await fetch('/action',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF':csrf},body:JSON.stringify({command,code})});
const d=await r.json();document.querySelector('#asset').textContent=d.asset?d.asset.toUpperCase():d.status==='complete'?'Both accounts authenticated':'Reader status';
document.querySelector('#done').textContent=(d.completed||[]).map(x=>x.toUpperCase()+' authenticated').join(' | ');
document.querySelector('#error').textContent=d.error||'';document.querySelector('#image').hidden=!d.image;
if(d.image)document.querySelector('#image').src='data:image/png;base64,'+d.image;
document.querySelector('#form').hidden=d.status!=='verification_required';document.querySelector('#code').value='';document.querySelector('#code').focus();
}catch(e){document.querySelector('#error').textContent='Connection to the reader failed.'}finally{busy=false;document.querySelector('button').disabled=false}}
document.querySelector('#form').onsubmit=e=>{e.preventDefault();update('submit',document.querySelector('#code').value)};update('start');</script></body></html>"""


VERIFY_SOURCE = """
import json, math, tempfile
from pathlib import Path
from power_reading.service import _ASSETS, _build_scraper
from power_reading.fusionsolar_session import FusionSolarSessionStore
failed = False
for asset in ('elnet', 'horeco'):
    stage = 'build_reader'
    try:
        scraper = _build_scraper(_ASSETS[asset], headless=True)
        scraper.session_store = FusionSolarSessionStore(
            asset, scraper.username, scraper.password, scraper.user_data_dir)
        stage = 'load_saved_session'
        stored_state = scraper.session_store.load()
        if stored_state is None:
            raise RuntimeError('No saved session')
        scraper.use_saved_session_only = True
        stage = 'read_power'
        with tempfile.TemporaryDirectory(prefix='fusion-verify-') as directory:
            scraper.user_data_dir = Path(directory)
            snapshot = scraper.scrape_once()
        stage = 'validate_power'
        if snapshot.pv_kw is None or not math.isfinite(snapshot.pv_kw) or snapshot.pv_kw < 0:
            raise RuntimeError('No power value')
        print(json.dumps({'asset': asset, 'status': 'verified',
            'power_mw': snapshot.pv_kw / 1000,
            'timestamp_utc': snapshot.timestamp_utc, 'source': snapshot.source}), flush=True)
    except Exception as exc:
        failed = True
        print(json.dumps({'asset': asset, 'status': 'failed',
            'error': type(exc).__name__, 'stage': stage}), flush=True)
raise SystemExit(1 if failed else 0)
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ssh-target', required=True)
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--verify-only', action='store_true',
                        help='Read both plants using saved sessions without submitting credentials.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    name = 'adrem-fusion-human-' + secrets.token_hex(5)
    key = Path.home() / '.ssh' / ('id_ed25519_' + name)
    railway = shutil.which('railway')
    registered = False
    process = None
    server = None
    try:
        subprocess.run(['ssh-keygen', '-t', 'ed25519', '-f', str(key), '-N', '', '-C', name], check=True, capture_output=True)
        fingerprint = subprocess.check_output(['ssh-keygen', '-lf', str(key)+'.pub'], text=True).split()[1]
        subprocess.run([railway, 'ssh', 'keys', 'add', '--key', name, '--name', name], check=True, capture_output=True, timeout=45)
        registered = True
        sources = []
        for module in ('power_reading.fusionsolar_session', 'power_reading.scrapers.fusionsolar_scraper', 'power_reading.fusionsolar_auth_bridge'):
            sources.append((module, (root / (module.replace('.', '/')+'.py')).read_text(encoding='utf-8-sig')))
        bootstrap = "import os,sys,types,importlib\nos.chdir('/app')\nsys.path.insert(0,'/app')\n"
        bootstrap += 'sources=' + repr(sources) + '\n'
        bootstrap += "for name,source in sources:\n parent=name.rsplit('.',1)[0]\n importlib.import_module(parent)\n m=types.ModuleType(name)\n m.__package__=parent\n m.__file__='/app/'+name.replace('.','/')+'.py'\n sys.modules[name]=m\n exec(compile(source,m.__file__,'exec'),m.__dict__)\n"
        bootstrap += VERIFY_SOURCE if args.verify_only else "sys.modules['power_reading.fusionsolar_auth_bridge'].main()\n"
        process = subprocess.Popen(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'ConnectTimeout=15', '-i', str(key), args.ssh_target,
            "python -B -u -c 'import sys;exec(sys.stdin.readline())'"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
        process.stdin.write('exec(__import__("base64").b64decode('+repr(base64.b64encode(bootstrap.encode()).decode())+'))\n')
        process.stdin.flush()
        if args.verify_only:
            output, _ = process.communicate(timeout=min(args.timeout, 300))
            for line in output.splitlines():
                result = json.loads(line)
                print(json.dumps({key: result[key] for key in (
                    'asset', 'status', 'power_mw', 'timestamp_utc', 'source', 'error', 'stage'
                ) if key in result}), flush=True)
            if process.returncode:
                raise RuntimeError('Saved-session live verification failed.')
            return
        lock = threading.Lock()
        completed = threading.Event()
        csrf = secrets.token_urlsafe(32)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def allowed(self):
                origin = 'http://127.0.0.1:' + str(self.server.server_port)
                return (self.headers.get('Host') == origin.removeprefix('http://') and
                        self.headers.get('Origin', origin) == origin and
                        self.headers.get('Sec-Fetch-Site') != 'cross-site')

            def reply(self, status, payload, kind):
                self.send_response(status)
                self.send_header('Content-Type', kind)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Frame-Options', 'DENY')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if not self.allowed() or self.path != '/':
                    return self.reply(403, b'Forbidden', 'text/plain')
                self.reply(200, HTML.replace('CSRF_VALUE', json.dumps(csrf)).encode(), 'text/html; charset=utf-8')

            def do_POST(self):
                if not self.allowed() or self.path != '/action' or not secrets.compare_digest(self.headers.get('X-CSRF', ''), csrf):
                    return self.reply(403, b'Forbidden', 'text/plain')
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 2048:
                        raise ValueError()
                    request = json.loads(self.rfile.read(size))
                    if request.get('command') not in ('start', 'submit', 'status'):
                        raise ValueError()
                    with lock:
                        process.stdin.write(json.dumps(request)+'\n')
                        process.stdin.flush()
                        response = json.loads(process.stdout.readline())
                    print(json.dumps({k: response[k] for k in ('asset', 'status', 'completed', 'error') if k in response}), flush=True)
                    self.reply(200, json.dumps(response).encode(), 'application/json')
                    if response.get('status') == 'complete':
                        completed.set()
                except Exception:
                    self.reply(502, b'{"status":"error","error":"Reader connection failed."}', 'application/json')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = 'http://127.0.0.1:' + str(server.server_port)
        print('Verification page: ' + url, flush=True)
        webbrowser.open(url)
        if not completed.wait(args.timeout):
            raise RuntimeError('Interactive verification timed out; no credentials were printed.')
    finally:
        if server:
            server.shutdown()
            server.server_close()
        if process and process.poll() is None:
            try:
                process.stdin.write('{"command":"stop"}\n')
                process.stdin.flush()
                process.wait(timeout=20)
            except Exception:
                process.terminate()
                process.wait(timeout=10)
        if registered:
            subprocess.run([railway, 'ssh', 'keys', 'remove', fingerprint], check=True, capture_output=True, timeout=45)
        key.unlink(missing_ok=True)
        Path(str(key)+'.pub').unlink(missing_ok=True)


if __name__ == '__main__':
    main()

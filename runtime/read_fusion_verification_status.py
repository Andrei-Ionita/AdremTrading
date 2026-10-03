import base64
import json
from pathlib import Path
import re
import urllib.request

url = 'http://127.0.0.1:64185'
with urllib.request.urlopen(url, timeout=10) as response:
    html = response.read().decode()
csrf = json.loads(re.search(r'const csrf=("[^"]+")', html).group(1))
request = urllib.request.Request(url + '/action', data=b'{"command":"status"}',
    headers={'Content-Type': 'application/json', 'Origin': url, 'X-CSRF': csrf})
with urllib.request.urlopen(request, timeout=60) as response:
    result = json.load(response)
print(json.dumps({key: result[key] for key in ('asset', 'status', 'completed', 'error') if key in result}))
if result.get('image'):
    Path(__file__).with_suffix('.png').write_bytes(base64.b64decode(result['image']))

"""Read-only, secret-free Railway checks using the secure recovery transport."""
import runpy
import sys
from pathlib import Path


namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/fusionsolar_reauthenticate.py'))
namespace['main'].__globals__['VERIFY_SOURCE'] = """
import json
from datetime import datetime, timedelta, timezone
from power_reading.database import _connection_scope
from power_reading.service import _ASSETS, _credentials
left, right = (_credentials(_ASSETS[asset]) for asset in ('elnet', 'horeco'))
print(json.dumps({'status': 'shared_account' if left == right else 'distinct_accounts'}))
now = datetime.now(timezone.utc)
end = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
start = end - timedelta(minutes=15)
with _connection_scope() as conn:
    with conn.cursor() as cursor:
        for asset in ('elnet', 'horeco'):
            cursor.execute('SELECT observed_at, pv_mw, source FROM power_readings WHERE asset=%s ORDER BY observed_at DESC LIMIT 1', (asset,))
            row = cursor.fetchone()
            print(json.dumps({'asset': asset, 'status': 'latest_sample',
                'timestamp_utc': row[0].isoformat() if row else None,
                'power_mw': row[1] if row else None, 'source': row[2] if row else None}))
            cursor.execute('SELECT observed_at FROM power_readings WHERE asset=%s AND observed_at >= %s AND observed_at <= %s ORDER BY observed_at', (asset, start, end))
            times = [row[0] for row in cursor.fetchall()]
            gap = max(((b-a).total_seconds() / 60 for a,b in zip(times,times[1:])), default=0)
            print(json.dumps({'asset': asset, 'status': 'completed_interval',
                'timestamp_utc': start.isoformat(), 'source': 'samples=%s max_gap_minutes=%.2f' % (len(times), gap)}))
            cursor.execute('SELECT collected_at, error FROM power_reading_errors WHERE asset=%s ORDER BY collected_at DESC LIMIT 1', (asset,))
            error = cursor.fetchone()
            if error:
                category = next((word for word in ('FusionSolarVerificationRequired', 'FusionSolarAuthenticationError', 'No numeric power', 'TimeoutError', 'RuntimeError') if word in error[1]), 'unclassified')
                print(json.dumps({'asset': asset, 'status': 'last_error', 'timestamp_utc': error[0].isoformat(), 'error': category}))
from elnet_intraday import get_latest_elnet_forecast_origin
from horeco_intraday import get_latest_horeco_forecast_origin
for asset, getter in (('elnet', get_latest_elnet_forecast_origin), ('horeco', get_latest_horeco_forecast_origin)):
    try:
        origin, energy = getter()
        print(json.dumps({'asset': asset, 'status': 'correction_interval_valid',
            'timestamp_utc': origin.isoformat(), 'source': 'energy_mwh=%.6f' % energy}))
    except Exception as exc:
        print(json.dumps({'asset': asset, 'status': 'correction_interval_unavailable', 'error': type(exc).__name__}))
"""
sys.argv = [sys.argv[0], '--ssh-target', 'd4c3b82b-a09a-4d59-b3e7-bca9f2bd6270@ssh.railway.com', '--verify-only']
namespace['main']()

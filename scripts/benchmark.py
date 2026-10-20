#!/usr/bin/env python3
"""Measure real HTTP persistence and replay-to-alert latency on a running stack.

This writes synthetic replay data and records measured, environment-specific
results. It does not simulate a throughput claim or estimate server CPU usage.
"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import time
import httpx


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values)*fraction)-1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8000')
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--output', default='benchmark.json')
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error('--runs must be between 1 and 100')
    username, password = os.getenv('SENTINEL_USERNAME'), os.getenv('SENTINEL_PASSWORD')
    if not username or not password:
        parser.error('Set SENTINEL_USERNAME and SENTINEL_PASSWORD for a provisioned ANALYST or ADMIN')
    started = time.perf_counter()
    samples = []
    usage_before = resource.getrusage(resource.RUSAGE_SELF)
    with httpx.Client(base_url=args.url.rstrip('/'), timeout=args.timeout) as client:
        login = client.post('/api/auth/login', json={'username':username,'password':password})
        login.raise_for_status()
        initial = client.get('/api/stats'); initial.raise_for_status()
        for index in range(args.runs):
            before_response = client.get('/api/stats'); before_response.raise_for_status()
            before = before_response.json()
            start = time.perf_counter()
            replay = client.post('/api/replay/mixed', params={'seed':717+index})
            replay.raise_for_status()
            body = replay.json()
            persist_ms = (time.perf_counter()-start)*1000
            count = body['events_generated']
            deadline = time.perf_counter()+args.timeout
            alert_observed_ms = None
            while True:
                stats_response = client.get('/api/stats'); stats_response.raise_for_status()
                after = stats_response.json()
                alerts_response = client.get('/api/alerts', params={'limit':1000})
                alerts_response.raise_for_status()
                prefix = str(body['run_id']).replace('-', '')[:12] + ':'
                matched = [a for a in alerts_response.json() if prefix in json.dumps(a)]
                if len(matched) >= 2 and alert_observed_ms is None:
                    alert_observed_ms = (time.perf_counter()-start)*1000
                progress = client.get('/api/operations'); progress.raise_for_status()
                state = progress.json()
                drained = state['outbox_pending'] == 0 and state['cursor'] == state.get('stream_last_id')
                if after['events'] >= before['events']+count and len(matched) >= 2 and drained:
                    drained_ms = (time.perf_counter()-start)*1000
                    break
                if time.perf_counter() > deadline:
                    raise RuntimeError(f'Replay {body.get("run_id")} did not persist expected events and its alerts before timeout')
                time.sleep(0.1)
            events_response = client.get('/api/events', params={'limit':1000, 'replay_run_id':str(body['run_id'])})
            events_response.raise_for_status()
            persisted = [e for e in events_response.json() if e.get('metadata', {}).get('replay_run_id') == str(body['run_id'])]
            if len(persisted) != count:
                raise RuntimeError('Replay event evidence could not be verified in first 1000 events; run on an idle stack')
            samples.append({'run_id':body['run_id'],'events':count,
                            'persist_ms':round(persist_ms,3),
                            'alert_observed_ms':round(alert_observed_ms,3),
                            'pipeline_drained_ms':round(drained_ms,3),
                            'matching_alerts_by_run_namespace':len(matched)})
        final = client.get('/api/stats'); final.raise_for_status()
        operations = client.get('/api/operations')
    elapsed = time.perf_counter()-started
    usage_after = resource.getrusage(resource.RUSAGE_SELF)
    result = {'measurement':'sequential synthetic mixed replay over real HTTP/database/Redis worker',
              'client_platform':platform.platform(), 'runs':args.runs,
              'elapsed_seconds':round(elapsed,3), 'samples':samples,
              'persist_p50_ms':statistics.median([s['persist_ms'] for s in samples]),
              'persist_p95_ms':percentile([s['persist_ms'] for s in samples],.95),
              'alert_observed_p95_ms':percentile([s['alert_observed_ms'] for s in samples],.95),
              'pipeline_drained_p95_ms':percentile([s['pipeline_drained_ms'] for s in samples],.95),
              'events_per_second_observed':round(sum(s['events'] for s in samples)/elapsed,3),
              'client_cpu_seconds':round(usage_after.ru_utime+usage_after.ru_stime-usage_before.ru_utime-usage_before.ru_stime,4),
              'client_peak_rss_native_units':usage_after.ru_maxrss,
              'server_resources':'not instrumented; no server CPU or memory claim',
              'initial_stats':initial.json(), 'final_stats':final.json(),
              'operations':operations.json() if operations.is_success else {'http_status':operations.status_code},
              'limitations':'Sequential smoke workload waiting for per-run alerts and full outbox/stream cursor drain; 100ms polling granularity; retained history affects detection cost; not a capacity benchmark.'}
    Path(args.output).write_text(json.dumps(result,indent=2,default=str)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('operations','samples')},indent=2))


if __name__ == '__main__':
    main()

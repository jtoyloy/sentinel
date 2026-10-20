#!/usr/bin/env python3
"""Isolated real PostgreSQL/Redis/API outage and recovery validation.

Creates a disposable PostgreSQL database owned by --database-owner using the
current OS PostgreSQL role (createdb). Starts only its own Redis and Uvicorn on
unused ports. Never connects to or stops the primary Redis/API. Preserves AOF,
logs and measured JSON evidence under .runtime; drops only its disposable DB.

Example:
  backend/venv/bin/python scripts/fault_smoke.py
  backend/venv/bin/python scripts/fault_smoke.py --redis-port 6388 --api-port 8002

Override --database-url for database credentials/host/port. Use PGHOST/PGPORT/
PGUSER/PGPASSWORD for the administrative createdb/dropdb connection if necessary.
Database credentials and generated login credentials are never printed.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4
import httpx
import redis.asyncio as redis
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
STREAM = 'sentinel:trading_events'


def utc():
    return datetime.now(timezone.utc).isoformat()


def unused_port(port):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=12)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def run(args):
    if args.redis_port in (6379, 8000) or args.api_port in (6379, 8000):
        raise ValueError('Primary API/Redis ports 8000/6379 are forbidden')
    if args.redis_port == args.api_port:
        raise ValueError('Ports must differ')
    for port in (args.redis_port, args.api_port):
        unused_port(port)
    for command in ('createdb', 'dropdb', 'redis-server'):
        if not shutil.which(command):
            raise RuntimeError(f'Required local executable missing: {command}')
    namespace = 'sentinel_fault_' + uuid4().hex[:12]
    directory = ROOT / '.runtime' / namespace
    directory.mkdir(parents=True)
    redis_directory = directory / 'redis'
    redis_directory.mkdir()
    api_log = (directory / 'api.log').open('a')
    redis_log = (directory / 'redis.log').open('a')
    setup_log = (directory / 'setup.log').open('a')
    env = dict(os.environ)
    env['DATABASE_URL'] = make_url(args.database_url).set(database=namespace).render_as_string(hide_password=False)
    env['REDIS_URL'] = f'redis://127.0.0.1:{args.redis_port}/0'
    env['CORS_ORIGINS'] = f'http://127.0.0.1:{args.api_port}'
    env['SESSION_COOKIE_SECURE'] = 'false'
    env['PYTHONPATH'] = str(ROOT / 'backend')
    username = 'fault-admin'
    password = secrets.token_urlsafe(28)
    api_process = redis_process = None
    database_created = False
    result = {'started_at':utc(), 'namespace':namespace, 'isolated_api_port':args.api_port,
              'isolated_redis_port':args.redis_port, 'checks':[], 'log_directory':str(directory)}
    start = time.monotonic()
    transport = redis.from_url(env['REDIS_URL'],decode_responses=True,socket_timeout=2,socket_connect_timeout=2)
    client = httpx.AsyncClient(base_url=f'http://127.0.0.1:{args.api_port}',timeout=15)

    def checked(name, details):
        entry = {'name':name,'at':utc(),**details}
        result['checks'].append(entry)
        print('PASS '+name+' '+json.dumps(details,sort_keys=True),flush=True)

    async def request(method, path, expected=200, **kwargs):
        response = await client.request(method,path,**kwargs)
        if response.status_code != expected:
            raise AssertionError(f'{method} {path}: expected {expected}; got {response.status_code}: {response.text[:300]}')
        return response.json()

    async def wait_until(callback, label, timeout=50):
        deadline = time.monotonic()+timeout
        last_error = None
        while time.monotonic()<deadline:
            if api_process is not None and api_process.poll() is not None:
                raise RuntimeError('Isolated API stopped unexpectedly; inspect api.log')
            try:
                value = await callback()
                if value:
                    return value
            except (httpx.TransportError, redis.ConnectionError, redis.TimeoutError) as error:
                last_error = type(error).__name__
            await asyncio.sleep(.2)
        raise AssertionError(f'Timed out waiting for {label}; last transport error={last_error}')

    def start_redis():
        return subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(args.redis_port),
            '--dir',str(redis_directory),'--appendonly','yes','--appendfsync','always','--save','',
            '--pidfile',str(directory/'redis.pid')],stdout=redis_log,stderr=subprocess.STDOUT)

    async def pipeline_complete(events, alerts):
        stats = await request('GET','/api/stats')
        operations = await request('GET','/api/operations')
        if not await transport.exists(STREAM):
            return None
        info = await transport.xinfo_stream(STREAM)
        if (stats['events']==events and stats['alerts']==alerts and operations['outbox_pending']==0
                and operations['worker_healthy'] and operations['cursor']==info['last-generated-id']):
            return {'stats':stats,'outbox_pending':operations['outbox_pending'],
                    'cursor':operations['cursor'],'stream_length':info['length'],
                    'stream_last_id':info['last-generated-id'],'worker':operations['worker']}
        return None

    try:
        created = subprocess.run(['createdb','-O',args.database_owner,namespace],stdout=setup_log,stderr=subprocess.STDOUT)
        if created.returncode:
            raise RuntimeError('Could not create disposable PostgreSQL database; inspect setup.log')
        database_created = True
        migrated = subprocess.run([sys.executable,'-m','alembic','upgrade','head'],cwd=ROOT/'backend',env=env,
                                  stdout=setup_log,stderr=subprocess.STDOUT)
        if migrated.returncode:
            raise RuntimeError('Disposable migration failed; inspect setup.log')
        provision_env = {**env,'SENTINEL_USER_PASSWORD':password}
        provisioned = subprocess.run([sys.executable,str(ROOT/'scripts/create_user.py'),username,'--role','ADMIN'],
                                     cwd=ROOT,env=provision_env,stdout=setup_log,stderr=subprocess.STDOUT)
        if provisioned.returncode:
            raise RuntimeError('Disposable account provisioning failed; inspect setup.log')
        redis_process = start_redis()
        await wait_until(transport.ping,'isolated Redis startup')
        api_process = subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1',
                         '--port',str(args.api_port)],cwd=ROOT/'backend',env=env,stdout=api_log,stderr=subprocess.STDOUT)
        async def healthy():
            response = await client.get('/health')
            return response.status_code==200
        await wait_until(healthy,'isolated API startup')
        await request('POST','/api/auth/login',json={'username':username,'password':password})
        first = await request('POST','/api/replay/wash_trade',params={'seed':417})
        assert first['events_generated']==4
        initial = await wait_until(lambda:pipeline_complete(4,1),'first durable replay')
        checked('isolated baseline replay',initial)

        # Stop only this child process; SIGTERM flushes its AOF before exit.
        stop_process(redis_process)
        assert redis_process.poll() is not None
        health = await request('GET','/health',expected=503)
        assert health['postgres'] and not health['redis']
        second = await request('POST','/api/replay/wash_trade',params={'seed':418})
        assert second['events_generated']==4
        degraded = await request('GET','/api/operations')
        outage_stats = await request('GET','/api/stats')
        assert degraded['outbox_pending']==4 and outage_stats['events']==8 and outage_stats['alerts']==1
        checked('Redis outage retains committed source and pending outbox',{
            'health_http_status':503,'stats':outage_stats,'outbox_pending':degraded['outbox_pending']})
        redis_process = start_redis()
        recovered = await wait_until(lambda:pipeline_complete(8,2),'Redis AOF restart and automatic outbox recovery')
        checked('same-AOF Redis restart recovers pending work',recovered)

        rebuild = await request('POST','/api/operations/recover',json={'mode':'REBUILD_STREAM'})
        assert rebuild['events_requeued']==8
        rebuilt = await wait_until(lambda:pipeline_complete(8,2),'actual full stream rebuild without duplicate alerts')
        assert rebuilt['stream_length']==16
        checked('actual stream rebuild preserves source and deduplicates alerts',{'recovery':rebuild,**rebuilt})

        poisoned_id = await transport.xadd(STREAM,{'event_id':'not-a-valid-uuid','source':'isolated-fault-smoke'})
        third = await request('POST','/api/replay/wash_trade',params={'seed':419})
        assert third['events_generated']==4
        continued = await wait_until(lambda:pipeline_complete(12,3),'poison quarantine and subsequent valid replay')
        operations = await request('GET','/api/operations')
        failure = next(x for x in operations['stream_failures'] if x['stream_id']==poisoned_id)
        assert failure['status']=='QUARANTINED' and failure['attempts']==3
        assert failure['payload']['event_id']=='not-a-valid-uuid'
        checked('poison message persisted, retried three times and quarantined without blocking later events',{
            'quarantine':failure,**continued})
        result['status']='PASSED'
    except Exception as error:
        result['status']='FAILED'
        result['error']=f'{type(error).__name__}: {error}'
        raise
    finally:
        await client.aclose()
        await transport.aclose()
        stop_process(api_process)
        stop_process(redis_process)
        if database_created:
            cleanup = subprocess.run(['dropdb','--if-exists','--force',namespace],stdout=setup_log,stderr=subprocess.STDOUT)
            result['database_dropped']=cleanup.returncode==0
        result['child_processes_stopped']=all(p is None or p.poll() is not None for p in (api_process,redis_process))
        result['finished_at']=utc()
        result['duration_seconds']=round(time.monotonic()-start,3)
        output = Path(args.output)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,indent=2)+'\n')
        for handle in (api_log,redis_log,setup_log):
            handle.close()
        print(json.dumps({key:result[key] for key in ('status','namespace','duration_seconds',
              'started_at','finished_at','child_processes_stopped')},indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--database-url',default=os.getenv('DATABASE_URL','postgresql+asyncpg://sentinel:sentinel_dev@127.0.0.1:5432/sentinel'),
                        help='Application PostgreSQL URL; only its database name is replaced')
    parser.add_argument('--database-owner',default='sentinel',help='Existing PostgreSQL role to own disposable DB')
    parser.add_argument('--redis-port',type=int,default=6387,help='Unused isolated Redis port; primary6379 forbidden')
    parser.add_argument('--api-port',type=int,default=8001,help='Unused isolated API port; primary8000 forbidden')
    parser.add_argument('--output',default=str(ROOT/'.runtime/fault-smoke.json'))
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__':
    main()

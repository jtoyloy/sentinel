#!/usr/bin/env python3
"""Real synthetic cross-domain HTTP/WebSocket E2E. Requires running Sentinel v2.

Creates uniquely namespaced records. Credentials are read from a private JSON
file, never printed. Uses REPUBLISH_PENDING only; never rebuilds the stream.
"""
import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode


async def run(args):
    credentials = json.loads(Path(args.credentials).read_text())
    namespace = 'E2E-' + uuid4().hex[:12]
    seed = int(uuid4().hex[:7], 16)
    start = datetime.now(timezone.utc)
    base = start.isoformat()
    evidence = {'namespace': namespace, 'started_at': base, 'checks': [], 'artifacts': {}}
    clients = {role: httpx.AsyncClient(base_url=args.url, timeout=30) for role in credentials}

    async def call(role, method, path, *, expected=200, **kwargs):
        response = await clients[role].request(method, path, **kwargs)
        if response.status_code != expected:
            raise AssertionError(f'{method} {path}: expected {expected}, received {response.status_code}: {response.text[:500]}')
        return response.json() if response.content and 'json' in response.headers.get('content-type', '') else response

    def checked(name):
        evidence['checks'].append(name)
        print(f'PASS {name}', flush=True)

    async def wait_for(check, name, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = await check()
            if value:
                return value
            await asyncio.sleep(.15)
        raise AssertionError('Timed out: ' + name)

    def event(symbol, employee=0, side='BUY', owner=None, metadata=None):
        return {'event_id':str(uuid4()), 'event_type':'EXECUTION', 'timestamp':base,
                'instrument_id':symbol, 'trader_id':f'{namespace}-T{employee}',
                'firm_id':namespace, 'account_id':f'{namespace}-A{employee}',
                'side':side,'quantity':200,'price':'101.25000000','order_id':str(uuid4()),
                'metadata':{'synthetic':True, **({'beneficial_owner':owner} if owner else {}), **(metadata or {})}}

    try:
        # Prove application-wide unauthenticated access boundaries before signing in.
        await call('VIEWER', 'GET', '/api/overview', expected=401)
        for role, secret in credentials.items():
            result = await call(role, 'POST', '/api/auth/login', json=secret)
            assert result['role'] == role
        await call('VIEWER','POST','/api/employees/simulate', expected=403)
        checked('authentication and viewer mutation denial')
        before = await call('ADMIN','GET','/api/stats')

        profiles = []
        for index in range(2):
            profiles.append({'source_key':f'{namespace}:HR:{index}', 'employee_id':f'{namespace}-E{index}',
                'full_name':f'Synthetic E2E Investigator {index}', 'email':f'{namespace.lower()}.{index}@sentinel.example',
                'department':'Equities','role':'Trader','status':'ACTIVE','effective_at':base,
                'accounts':[{'firm_id':namespace,'account_id':f'{namespace}-A{index}'}]})
        bad = {**profiles[0], 'source_key':f'{namespace}:bad', 'email':'invalid-email'}
        hr = await call('ANALYST','POST','/api/employees/feed', json={'records':profiles+[bad]})
        assert (hr['accepted'],hr['rejected']) == (2,1)
        again = await call('ANALYST','POST','/api/employees/feed',json={'records':profiles})
        assert again['duplicates'] == 2
        resolved = await call('VIEWER','GET','/api/employees/resolve', params={'identity':profiles[0]['email']})
        assert resolved['employee_id'] == profiles[0]['employee_id']
        detail = await call('VIEWER','GET','/api/employees/'+profiles[0]['employee_id'])
        assert len(detail['accounts']) == 1 and len(detail['history']) == 1
        simulator = await call('ANALYST','POST','/api/employees/simulate',params={'seed':seed,'count':2})
        assert simulator['accepted'] == 2
        checked('HR feed quality, profile persistence, simulator, identity and idempotency')

        forged = event('AAPL',metadata={'employee_id':'FORGED','department':'FORGED','role':'ADMIN'})
        first_trade = await call('ANALYST','POST','/api/events/ingest',json=forged)
        assert first_trade['metadata']['employee_id'] == profiles[0]['employee_id']
        assert first_trade['metadata']['role'] == 'Trader'
        assert (await call('ANALYST','POST','/api/events/ingest',json=forged))['event_id'] == forged['event_id']
        unmatched = event('ZZZ',99,metadata={'employee_id':profiles[0]['employee_id'],'role':'ADMIN','security':{'name':'FORGED'}})
        unmatched_saved = await call('ANALYST','POST','/api/events/ingest',json=unmatched)
        assert not {'employee_id','role','security'}.intersection(unmatched_saved['metadata'])
        checked('trusted employee enrichment overrides source claims; unknown identity cannot forge enrichment')

        messages = []
        for index, body in enumerate(['We have inside information on $AAPL before the announcement.',
                                      'Buy $MSFT before the announcement; delete this chat.',
                                      'Routine $TSLA liquidity report for the desk meeting.']):
            messages.append({'source_key':f'{namespace}:COMM:{index}','channel':'EMAIL' if index != 1 else 'CHAT',
                'timestamp':base,'sender':profiles[0]['email'],'recipients':[profiles[1]['email']],
                'subject':'Synthetic market review','body':body})
        result = await call('ANALYST','POST','/api/communications/ingest',json={'messages':messages})
        assert result['accepted'] == 3 and result['signals_generated'] >= 2
        assert (await call('ANALYST','POST','/api/communications/ingest',json={'messages':messages}))['duplicates'] == 3
        all_messages = await call('VIEWER','GET','/api/communications',params={'employee_id':profiles[0]['employee_id'],'limit':1000})
        by_key = {m['source_key']:m for m in all_messages}
        message_ids = [by_key[m['source_key']]['id'] for m in messages]
        for communication_id in message_ids:
            preserved = await call('VIEWER','GET',f'/api/communications/{communication_id}/evidence')
            assert preserved['hash_verified'] and preserved['normalized_fields_verified']
        earlier = await call('VIEWER','GET','/api/communications/'+message_ids[0])
        assert forged['event_id'] in [e['event_id'] for e in earlier['related_events']]
        normal = await call('VIEWER','GET','/api/communications/'+message_ids[2])
        assert normal['signals'] == []
        late_trade = event('MSFT')
        await call('ANALYST','POST','/api/events/ingest',json=late_trade)
        async def late_correlation():
            detail = await call('VIEWER','GET','/api/communications/'+message_ids[1])
            return detail if late_trade['event_id'] in [e['event_id'] for e in detail['related_events']] else None
        late = await wait_for(late_correlation, 'communication-before-trade persisted correlation')
        assert any(s['rule'] == 'TRADE_CORRELATION' for s in late['signals'])
        checked('email/chat preservation, normal negative control and correlation in both arrival orders')

        for symbol, action in [('AAPL','BLOCK'),('MSFT','REVIEW')]:
            await call('ADMIN','POST','/api/restrictions/policies',json={'name':namespace+'-'+symbol,
                'instrument_id':symbol,'action':action,'reason':'Synthetic E2E restricted instrument',
                'effective_from':(start-timedelta(days=1)).isoformat(),'department':'Equities'})
        def permission(symbol):
            return {'employee_id':profiles[0]['employee_id'],'instrument_id':symbol,'side':'BUY','quantity':100,'price':'101.25'}
        block = await call('ANALYST','POST','/api/restrictions/check',json=permission('AAPL'))
        review = await call('ANALYST','POST','/api/restrictions/check',json=permission('MSFT'))
        assert block['decision'] == 'BLOCK' and review['decision'] == 'REVIEW'
        exemption = await call('ANALYST','POST','/api/restrictions/exemptions',json={
            'employee_id':profiles[0]['employee_id'],'instrument_id':'AAPL','reason':'Synthetic dual control test',
            'expires_at':(start+timedelta(hours=1)).isoformat()})
        await call('ANALYST','POST',f'/api/restrictions/exemptions/{exemption["id"]}/approve',expected=403,json={'notes':'Role denied'})
        approved = await call('ADMIN','POST',f'/api/restrictions/exemptions/{exemption["id"]}/approve',json={'notes':'Independent synthetic approval'})
        assert approved['status'] == 'APPROVED'
        allowed = await call('ANALYST','POST','/api/restrictions/check',json=permission('AAPL'))
        assert allowed['decision'] == 'ALLOW' and allowed['exemption_id'] == exemption['id']
        self_exemption = await call('ADMIN','POST','/api/restrictions/exemptions',json={
            'employee_id':profiles[1]['employee_id'],'instrument_id':'AAPL','reason':'Self approval negative control',
            'expires_at':(start+timedelta(hours=1)).isoformat()})
        await call('ADMIN','POST',f'/api/restrictions/exemptions/{self_exemption["id"]}/approve',expected=403,json={'notes':'Must not approve self'})
        checked('versioned policies, BLOCK/REVIEW/ALLOW and independent exemption approval')

        signals = await call('VIEWER','GET','/api/signals',params={'limit':1000})
        selected = [s['id'] for s in signals if block['id'] in s['evidence'].get('decisions',[]) or message_ids[0] in s['evidence'].get('communications',[])]
        assert len(selected) >= 2
        case = await call('ANALYST','POST','/api/investigations',json={'title':namespace+' cross-domain review',
            'priority':'HIGH','summary':'Synthetic communication and restriction evidence','signal_ids':selected,'alert_ids':[]})
        escalated = await call('ANALYST','POST',f'/api/investigations/{case["id"]}/actions',json={'action':'ESCALATE','notes':'Synthetic supervisory review'})
        assert escalated['status'] == 'ESCALATED'
        closed = await call('ANALYST','POST',f'/api/investigations/{case["id"]}/actions',json={'action':'CLOSE','notes':'Synthetic investigation completed'})
        assert closed['status'] == 'CLOSED'
        case_detail = await call('VIEWER','GET','/api/investigations/'+case['id'])
        assert len(case_detail['timeline']) == 3
        assert all(x['actor'] == credentials['ANALYST']['username'] for x in case_detail['timeline'])
        assert len(case_detail['signals']) == len(selected)
        checked('cross-domain investigation escalation, closure and authenticated audit attribution')

        alpha = await call('ANALYST','POST','/api/data/feeds/ALPHA/simulate',params={'seed':seed})
        beta = await call('ANALYST','POST','/api/data/feeds/BETA/simulate',params={'seed':seed})
        assert alpha['normalized'] == 6 and beta['normalized'] == 6
        repeated = await call('ANALYST','POST','/api/data/feeds/ALPHA/simulate',params={'seed':seed})
        assert repeated['duplicates'] == 6 and repeated['normalized'] == 0
        malformed = await call('ANALYST','POST','/api/data/feeds/ALPHA/ingest',json={'records':[
            {'event_ref':namespace+'-malformed','symbol':None}]})
        assert malformed['rejected'] == 1
        reconciliation = await call('ANALYST','POST','/api/data/reconcile')
        our_issues = [x for x in reconciliation['issues'] if x['execution_reference'].startswith(f'SYN-{seed}-')]
        assert {x['kind'] for x in our_issues} == {'MISSING','DUPLICATE','MISMATCH'}
        csv_response = await call('ANALYST','GET','/api/reports/trades.csv')
        assert b'event_id,timestamp,instrument_id' in csv_response.content
        digest = hashlib.sha256(csv_response.content).hexdigest()
        exports = await call('VIEWER','GET','/api/reports/exports',params={'limit':1000})
        exported = next(x for x in exports if x['sha256'] == digest)
        assert exported['requester'] == credentials['ANALYST']['username']
        artifact = Path(args.output).parent / (namespace.lower()+'-trades.csv')
        artifact.parent.mkdir(parents=True,exist_ok=True)
        artifact.write_bytes(csv_response.content)
        evidence['artifacts']['csv'] = str(artifact)
        evidence['artifacts']['csv_sha256'] = digest
        evidence['artifacts']['export_rows'] = exported['row_count']
        checked('broker normalization/idempotency/quality, three-way reconciliation and hashed CSV audit')

        ws_url = args.url.replace('http://','ws://').replace('https://','wss://').rstrip('/')+'/ws/alerts'
        try:
            async with websockets.connect(ws_url):
                raise AssertionError('Unauthenticated WebSocket accepted')
        except InvalidStatusCode as error:
            assert error.status_code in (401,403)
        cookie = '; '.join(f'{key}={value}' for key,value in clients['VIEWER'].cookies.items())
        async with websockets.connect(ws_url,extra_headers={'Cookie':cookie},origin=args.url.rstrip('/')) as socket:
            await socket.send('PING')
            response = json.loads(await asyncio.wait_for(socket.recv(),5))
            assert response['type'] == 'PONG'
            owner = namespace+'-WASH'
            pair = [event('E2E',0,'BUY',owner),event('E2E',1,'SELL',owner)]
            await call('ANALYST','POST','/api/events/ingest/batch',json=pair)
            async def find_live_alert():
                while True:
                    received = json.loads(await socket.recv())
                    if received.get('type') == 'ALERT' and namespace in json.dumps(received):
                        return received
            alert = await asyncio.wait_for(find_live_alert(),40)
            assert alert['alert_type'] == 'WASH_TRADE'
            alert_evidence = await call('VIEWER','GET',f'/api/alerts/{alert["alert_id"]}/evidence')
            assert {x['event_id'] for x in alert_evidence} == {x['event_id'] for x in pair}
            await call('VIEWER','POST','/api/auth/logout',expected=204)
            await socket.send('PING')
            try:
                while True:
                    await asyncio.wait_for(socket.recv(),5)
            except ConnectionClosed as error:
                assert error.code == 1008
        checked('WebSocket authentication, live persisted wash alert/evidence and logout revocation')

        async def drained():
            status = await call('ADMIN','GET','/api/operations')
            return status if status['outbox_pending'] == 0 and status['worker_healthy'] else None
        operations_before = await wait_for(drained,'healthy pipeline with empty outbox')
        alerts_before = await call('ADMIN','GET','/api/stats')
        for _ in range(2):
            recovery = await call('ADMIN','POST','/api/operations/recover',json={'mode':'REPUBLISH_PENDING'})
            assert recovery['mode'] == 'REPUBLISH_PENDING'
        await call('ANALYST','POST','/api/events/ingest/batch',json=pair)
        operations_after = await wait_for(drained,'recovered worker healthy')
        alerts_after = await call('ADMIN','GET','/api/stats')
        assert alerts_before['alerts'] == alerts_after['alerts']
        assert alerts_before['events'] == alerts_after['events']
        assert operations_after['postgres'] and operations_after['redis']
        checked('actual pending recovery, worker restart and duplicate event/alert prevention')
        evidence['initial_stats'] = before
        evidence['final_stats'] = alerts_after
        evidence['operations_before'] = operations_before
        evidence['operations_after'] = operations_after
        evidence['finished_at'] = datetime.now(timezone.utc).isoformat()
        evidence['duration_seconds'] = round((datetime.now(timezone.utc)-start).total_seconds(),3)
        evidence['status'] = 'PASSED'
        Path(args.output).parent.mkdir(parents=True,exist_ok=True)
        Path(args.output).write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps({'status':evidence['status'],'checks':len(evidence['checks']),
                          'namespace':namespace,'duration_seconds':evidence['duration_seconds'],
                          'initial_stats':before,'final_stats':alerts_after,'output':args.output},indent=2))
    finally:
        for client in clients.values():
            await client.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://localhost:8000')
    parser.add_argument('--credentials',required=True)
    parser.add_argument('--output',default='.runtime/platform-smoke.json')
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == '__main__':
    main()

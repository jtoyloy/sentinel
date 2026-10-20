"""Run against an already running PostgreSQL/Redis/API stack (adds replay data)."""
import asyncio
import json
import httpx
import websockets


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=20) as client:
        health = await client.get("/health")
        assert health.status_code == 200, health.text
        before = (await client.get("/api/stats")).json()
        async with websockets.connect("ws://127.0.0.1:8000/ws/alerts") as ws:
            response = await client.post("/api/replay/mixed", params={"seed": 42})
            assert response.status_code == 200, response.text
            run = response.json()
            notifications = []
            while len(notifications) < 2:
                notification = json.loads(await asyncio.wait_for(ws.recv(), 20))
                if notification["type"] == "ALERT":
                    # A running service can finish earlier queued work concurrently.
                    linked = (await client.get(f"/api/alerts/{notification['alert_id']}/evidence")).json()
                    if linked and all(e["metadata"].get("replay_run_id") == run["run_id"] for e in linked):
                        notifications.append(notification)
            assert {a["alert_type"] for a in notifications} == {"WASH_TRADE", "SPOOFING"}
            for alert in notifications:
                evidence = (await client.get(f"/api/alerts/{alert['alert_id']}/evidence")).json()
                assert {e["event_id"] for e in evidence} == set(alert["evidence"]["event_ids"])
                assert all(e["metadata"]["replay_run_id"] == run["run_id"] for e in evidence)
                if alert["alert_type"] == "SPOOFING":
                    assert {e["event_type"] for e in evidence} == {"ORDER", "EXECUTION", "CANCEL"}
            alert_id = notifications[0]["alert_id"]
            changed = await client.patch(f"/api/alerts/{alert_id}", json={
                "status": "INVESTIGATING", "actor": "Smoke investigator", "notes": "Reviewed exact persisted evidence"})
            assert changed.status_code == 200, changed.text
            history = (await client.get(f"/api/alerts/{alert_id}/history")).json()
            assert history[-1]["new_status"] == "INVESTIGATING"
            await ws.send("ping")
            assert json.loads(await ws.recv())["type"] == "PONG"
        # Allow the remaining stream deliveries to drain and ensure no duplicate alerts.
        for _ in range(100):
            after = (await client.get("/api/stats")).json()
            if after["alerts"] == before["alerts"] + 2:
                break
            await asyncio.sleep(.1)
        assert after["events"] == before["events"] + run["events_generated"]
        assert after["alerts"] == before["alerts"] + 2
        print(json.dumps({"health": health.json(), "run": run, "before": before,
                          "after": after, "websocket_alert_types": [a["alert_type"] for a in notifications]}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

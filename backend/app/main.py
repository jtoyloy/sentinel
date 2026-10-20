import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import engine, get_db, async_session_maker
from app.core.security import require_reader, websocket_principal
from app.api import auth
from app.platform import router as platform_router, register_domains
from app.models import auth as auth_models
from app.core.redis import redis_client
from app.models.models import Base, Alert, TradingEvent
from app.api import events, alerts, replay
from app.services.pipeline import run_pipeline

domain_modules = register_domains()

logging.basicConfig(level=logging.INFO)


class ConnectionManager:
    def __init__(self):
        self.active_connections = set()

    async def connect(self, websocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message):
        async def send(connection):
            try:
                async with async_session_maker() as db:
                    await websocket_principal(connection, db)
                await asyncio.wait_for(connection.send_json(message), timeout=2)
            except Exception:
                self.disconnect(connection)
                with suppress(Exception):
                    await connection.close(code=1008)
        await asyncio.gather(*(send(connection) for connection in tuple(self.active_connections)))


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app):
    # Schema is versioned by Alembic; run `alembic upgrade head` before startup.
    await redis_client.connect()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1 FROM event_outbox LIMIT 1"))
    app.state.broadcast = manager.broadcast
    app.state.pipeline_task = asyncio.create_task(run_pipeline(manager.broadcast))
    try:
        yield
    finally:
        task = app.state.pipeline_task
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await redis_client.disconnect()
        await engine.dispose()


app = FastAPI(title="Sentinel Trade Surveillance", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS.split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth.router)
app.include_router(platform_router)
for domain in domain_modules:
    app.include_router(domain.router)
app.include_router(events.router)
app.include_router(alerts.router)
app.include_router(replay.router)


@app.get("/")
async def root():
    return {"message": "Sentinel Trade Surveillance", "version": "2.0.0"}


@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    checks = {"postgres": False, "redis": False}
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception:
        pass
    try:
        checks["redis"] = bool(await redis_client.client.ping())
    except Exception:
        pass
    healthy = all(checks.values())
    return JSONResponse({"status": "healthy" if healthy else "degraded", **checks},
                        status_code=200 if healthy else 503)


@app.get("/api/stats", dependencies=[Depends(require_reader)])
async def stats(db: AsyncSession = Depends(get_db)):
    return {"events": await db.scalar(select(func.count()).select_from(TradingEvent)),
            "alerts": await db.scalar(select(func.count()).select_from(Alert)),
            "open_alerts": await db.scalar(select(func.count()).select_from(Alert).where(
                Alert.status.in_(["OPEN", "INVESTIGATING"]))),
            "critical_alerts": await db.scalar(select(func.count()).select_from(Alert).where(
                Alert.severity == "CRITICAL", Alert.status.in_(["OPEN", "INVESTIGATING"])))}


@app.websocket("/ws/alerts")
async def websocket_alerts(websocket: WebSocket):
    try:
        async with async_session_maker() as db:
            await websocket_principal(websocket, db)
    except HTTPException:
        await websocket.close(code=1008)
        return
    await manager.connect(websocket)
    try:
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                pass
            try:
                async with async_session_maker() as db:
                    await websocket_principal(websocket, db)
            except HTTPException:
                await websocket.close(code=1008)
                return
            await websocket.send_json({"type": "PONG"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)


@app.middleware("http")
async def response_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response

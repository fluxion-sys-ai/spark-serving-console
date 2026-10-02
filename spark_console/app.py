import asyncio
import contextlib
import fcntl
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .controller import Conflict, Controller

ROOT = Path(__file__).resolve().parents[1]
ModelID = Literal['4b', '7b', '27b', '47b']


class Operation(BaseModel):
    model: ModelID
    action: Literal['deploy', 'switch', 'stop']


class Prompt(BaseModel):
    model: ModelID
    prompt: str = Field(min_length=1, max_length=8000)
    max_tokens: int = Field(default=128, ge=1, le=512)


class SessionToken(BaseModel):
    token: str = Field(min_length=32, max_length=256)


def create_app(controller=None):
    @asynccontextmanager
    async def lifespan(app):
        ctrl = controller or Controller(os.environ.get('SPARK_BUNDLE', str(Path.home() / 'ai/spark-dual-models')),
                                        os.environ.get('SPARK_CONSOLE_DATA', str(ROOT / 'var')))
        lock = ctrl.instance_lock_path.open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError('A console is already running for this model launcher bundle.')
        app.state.controller = ctrl
        await asyncio.to_thread(ctrl.refresh)

        async def poll():
            while True:
                await asyncio.sleep(3)
                await asyncio.to_thread(ctrl.refresh)

        task = asyncio.create_task(poll())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            # UI shutdown must not terminate deployed model services.
            lock.close()

    app = FastAPI(title='Fluxion · Spark Console', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])

    @app.middleware('http')
    async def local_control(request: Request, call_next):
        def reject(detail, status):
            response = JSONResponse({'detail': detail}, status_code=status)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Frame-Options'] = 'DENY'
            return response

        session_path = request.url.path == '/api/session'
        if request.url.path.startswith('/api/') and not session_path:
            supplied = request.cookies.get('spark_console_session', '')
            expected = request.app.state.controller.access_token
            if not secrets.compare_digest(supplied, expected):
                return reject('Open the authenticated URL shown by ./console.sh status.', 401)
        if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return reject('Cross-origin control requests are not allowed.', 403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        if request.url.path.startswith('/api'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.post('/api/session')
    def session(body: SessionToken, request: Request):
        if not secrets.compare_digest(body.token, request.app.state.controller.access_token):
            raise HTTPException(403, 'Invalid console access token.')
        response = JSONResponse({'status': 'ok'})
        response.set_cookie('spark_console_session', body.token, httponly=True, samesite='strict',
                            max_age=12 * 60 * 60, path='/')
        return response

    @app.get('/api/state')
    def state(request: Request):
        return request.app.state.controller.snapshot()

    @app.get('/api/logs/{model}')
    def logs(model: ModelID, request: Request):
        return request.app.state.controller.logs(model)

    @app.post('/api/operations', status_code=202)
    def operate(body: Operation, request: Request):
        return request.app.state.controller.submit(body.model, body.action)

    @app.post('/api/cancel')
    def cancel(request: Request):
        return request.app.state.controller.cancel_job()

    @app.post('/api/test')
    def test(body: Prompt, request: Request):
        try:
            return request.app.state.controller.try_request(body.model, body.prompt, body.max_tokens)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(502, f'Model API returned {exc.response.status_code}: {exc.response.text[:600]}')
        except httpx.HTTPError as exc:
            raise HTTPException(502, 'Model request failed: ' + str(exc))

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    dist = ROOT / 'frontend/dist'
    if dist.exists():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

        @app.get('/fluxion-icon-transparent.svg')
        def icon():
            return FileResponse(dist / 'fluxion-icon-transparent.svg')

        @app.get('/')
        def index():
            return FileResponse(dist / 'index.html', headers={'Cache-Control': 'no-cache'})

    return app

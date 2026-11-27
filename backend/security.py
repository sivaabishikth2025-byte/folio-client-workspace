"""Small shared-reviewer session boundary; not a multi-user identity system."""
from . import accounts
import base64
import hashlib
import hmac
import os
import secrets
import time

COOKIE = 'folio_session'
TTL = 8 * 60 * 60


def password():
    return os.getenv('WORKSPACE_PASSWORD', '')


def configured():
    return accounts.enabled() or bool(password())


def signature(value):
    return hmac.new(password().encode(), ('folio-session-v1:' + value).encode(), hashlib.sha256).hexdigest()


def make_session():
    value = str(int(time.time())) + '.' + secrets.token_hex(24)
    return value + '.' + signature(value)


def authenticated(request, store):
    if accounts.enabled():return accounts.authenticate(request,store,COOKIE)
    if os.getenv('VERCEL'):return False
    if not configured():
        return not bool(os.getenv('VERCEL'))
    token = request.cookies.get(COOKIE, '')
    try:
        value, mac = token.rsplit('.', 1)
        issued = int(value.split('.', 1)[0])
        if hmac.compare_digest(signature(value), mac) and 0 <= time.time() - issued < TTL:
            return hashlib.sha256(token.encode()).hexdigest() in store.auth_state()['sessions']
    except (ValueError, TypeError):
        pass
    return False


def login(store, request, supplied):
    """Durable throttling and revocable sessions across server restarts."""
    from fastapi import HTTPException
    if not configured():
        raise HTTPException(503, 'The workspace operator must configure authentication.')
    # Vercel overwrites this proxy header; elsewhere use the socket peer.
    peer = request.headers.get('x-vercel-forwarded-for', '').split(',')[0] if os.getenv('VERCEL') else (request.client.host if request.client else 'unknown')
    peer_id = signature('login-peer:' + peer)
    def update(state):
        now = time.time()
        state['sessions'] = {key: expiry for key, expiry in state['sessions'].items() if expiry > now}
        state['attempts'] = {key: [t for t in times if t > now - 900] for key, times in state['attempts'].items() if any(t > now - 900 for t in times)}
        recent = state['attempts'].get(peer_id, [])
        total = sum(len(v) for v in state['attempts'].values())
        if len(recent) >= 10 or total >= 100:
            return None, 429
        state['attempts'][peer_id] = recent + [now]
        if not hmac.compare_digest(supplied.encode(), password().encode()):
            return None, 401
        token = make_session()
        if len(state['sessions']) >= 100:
            oldest = min(state['sessions'], key=state['sessions'].get)
            del state['sessions'][oldest]
        state['sessions'][hashlib.sha256(token.encode()).hexdigest()] = now + TTL
        return token, 200
    token, status = store.auth_state(update)
    if status != 200:
        raise HTTPException(status, 'Too many sign-in attempts. Try again in 15 minutes.' if status == 429 else 'Incorrect workspace password.')
    return token


def logout(store, request):
    token = request.cookies.get(COOKIE, '')
    if token:
        store.auth_state(lambda s: s['sessions'].pop(hashlib.sha256(token.encode()).hexdigest(), None))


class BodyLimit:
    """Reject oversized bodies before FastAPI's JSON or multipart parsers run."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        cap = (51 * 1024 * 1024 if not os.getenv('VERCEL') else 4 * 1024 * 1024 + 65536) if scope['path'].endswith('/upload') else 1024 * 1024
        chunks = []; total = 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            total += len(message.get('body', b''))
            if total > cap:
                from starlette.responses import JSONResponse
                return await JSONResponse({'detail': 'Request exceeds the supported size.'}, status_code=413)(scope, receive, send)
            chunks.append(message)
            if not message.get('more_body'):
                break
        index = 0
        async def replay():
            nonlocal index
            if index < len(chunks):
                item = chunks[index]; index += 1
                return item
            return await receive()
        await self.app(scope, replay, send)

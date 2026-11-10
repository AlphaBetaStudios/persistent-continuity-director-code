"""Public ComfyUI API transport and lightweight custom server routes.

The background job manager never waits inside a ComfyUI execution node.
The HTTP destination is derived from the accepted local socket, not user input.
"""
from __future__ import annotations

import ipaddress
import json
import logging
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp
from aiohttp import web


class ComfyAPIError(RuntimeError):
    def __init__(self, message: str, *, definitive: bool = False):
        super().__init__(message)
        self.definitive = definitive


class ComfyClient:
    def __init__(self, base_url: str, headers: dict | None = None):
        self.base_url = base_url.rstrip('/')
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('The ComfyUI destination must be a plain server origin.')
        # Only a kernel-derived literal address reaches this constructor in routes.
        ipaddress.ip_address(parsed.hostname)
        self.headers = dict(headers or {})

    async def _request(self, method: str, path: str, payload=None):
        timeout = aiohttp.ClientTimeout(total=120 if method == 'POST' else 30, connect=10)
        async with aiohttp.ClientSession(headers=self.headers, timeout=timeout, trust_env=False) as session:
            async with session.request(method, self.base_url + path, json=payload, allow_redirects=False) as response:
                text = await response.text()
                try:
                    body = json.loads(text)
                except (ValueError, TypeError):
                    body = {'error': text[:2000] or f'HTTP {response.status}'}
                if response.status < 200 or response.status >= 300:
                    # 4xx rejects the request. A 5xx or transport loss could occur
                    # after queue insertion and requires history/queue reconciliation.
                    error = body.get('error', body) if isinstance(body, dict) else body
                    if isinstance(body, dict) and body.get('node_errors'):
                        error = {'error': error, 'node_errors': body['node_errors']}
                    detail = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
                    raise ComfyAPIError(f'ComfyUI HTTP {response.status}: {detail[:4000]}', definitive=300 <= response.status < 500)
                if not isinstance(body, dict):
                    raise ComfyAPIError('ComfyUI returned an unexpected response; check history before retrying.')
                return body

    async def submit(self, prompt: dict, prompt_id: str) -> dict:
        return await self._request('POST', '/prompt', {'prompt': prompt, 'prompt_id': prompt_id})

    async def history(self, prompt_id: str) -> dict:
        return await self._request('GET', '/history/' + quote(prompt_id, safe=''))

    async def queue_ids(self) -> set[str]:
        result = await self._request('GET', '/queue')
        ids = set()
        for key in ('queue_running', 'queue_pending'):
            entries = result.get(key)
            if not isinstance(entries, list):
                raise ComfyAPIError('ComfyUI returned an unrecognized queue response.')
            for entry in entries:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    raise ComfyAPIError('ComfyUI returned an unrecognized queue entry.')
                ids.add(str(entry[1]))
        return ids


def connection_context(request) -> tuple[str, dict]:
    transport = request.transport
    address = transport.get_extra_info('sockname') if transport else None
    if not isinstance(address, tuple) or len(address) < 2:
        raise ValueError('Scene Director needs a TCP connection to this ComfyUI server.')
    host, port = address[:2]
    ip = ipaddress.ip_address(host)
    if ip.is_unspecified:
        host = '::1' if ip.version == 6 else '127.0.0.1'
    # An accepted LAN-bound socket is the same local server. Keeping that exact
    # kernel-provided address also supports --listen with a specific LAN IP.
    bracketed = f'[{host}]' if ip.version == 6 else host
    scheme = 'https' if request.secure else 'http'
    headers = {key: request.headers[key] for key in ('Authorization', 'Cookie', 'Comfy-User') if key in request.headers}
    return f'{scheme}://{bracketed}:{int(port)}', headers


def register_routes(prompt_server, input_root: Path, output_root: Path, *, continuity_gate=None):
    """Register once during custom-node import; return the manager for diagnostics.

    A gate is deliberately opt-in.  Installing the custom node must not turn
    an uncalibrated visual measurement into automatic approval.
    """
    if __package__:
        from .runner import JobManager
    else:
        from runner import JobManager

    transport_context = {'origin': None, 'headers': {}}

    def client_factory():
        if transport_context['origin'] is None:
            raise RuntimeError('Open Scene Director and Resume to reconnect this job.')
        return ComfyClient(transport_context['origin'], transport_context['headers'])

    manager = JobManager(Path(output_root) / 'alpha_scene_director', Path(input_root), client_factory, continuity_gate)

    async def respond(request, action):
        try:
            return web.json_response(await action())
        except (ValueError, KeyError, FileNotFoundError) as error:
            return web.json_response({'error': str(error)}, status=400)
        except RuntimeError as error:
            return web.json_response({'error': str(error)}, status=409)
        except Exception:
            logging.exception('Alpha Scene Director request failed')
            return web.json_response({'error': 'Scene Director could not complete the request; see the ComfyUI server log.'}, status=500)

    def set_context(request):
        origin, headers = connection_context(request)
        transport_context.update(origin=origin, headers=headers)

    @prompt_server.routes.get('/alpha_scene_director/jobs')
    async def list_jobs(request):
        async def action():
            return manager.list_jobs()
        return await respond(request, action)

    @prompt_server.routes.post('/alpha_scene_director/validate')
    async def validate(request):
        async def action():
            return manager.validate(await request.json())
        return await respond(request, action)

    @prompt_server.routes.post('/alpha_scene_director/jobs')
    async def create(request):
        async def action():
            payload = await request.json()
            set_context(request)
            return manager.create(payload)
        return await respond(request, action)

    @prompt_server.routes.get('/alpha_scene_director/jobs/{job_id}')
    async def get_job(request):
        async def action():
            return manager.get(request.match_info['job_id'])
        return await respond(request, action)

    @prompt_server.routes.post('/alpha_scene_director/jobs/{job_id}/pause')
    async def pause(request):
        async def action():
            return manager.pause(request.match_info['job_id'])
        return await respond(request, action)

    @prompt_server.routes.post('/alpha_scene_director/jobs/{job_id}/resume')
    async def resume(request):
        async def action():
            set_context(request)
            return manager.resume(request.match_info['job_id'])
        return await respond(request, action)

    return manager

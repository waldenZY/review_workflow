"""Real HTTP only. Immutable connections are captured before each operation."""
import copy
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

FIELDS = {'api_url', 'api_key', 'model', 'max_tokens', 'timeout', 'stream', 'enable_thinking', 'skip_special_tokens'}
DEFAULTS = dict(api_url='', api_key='', model='', max_tokens=4096, timeout=120, stream=False,
                enable_thinking=False, skip_special_tokens=False)


def strict_json(value):
    return json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('JSON 不支持非有限数值。')))


def parse_output(raw):
    import re
    text = raw.strip().lstrip('\ufeff').strip()
    changes = []
    if text != raw:
        changes.append('trim_whitespace_or_bom')
    match = re.fullmatch(r'```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```', text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()
        changes.append('removed_outer_code_fence')
    return text, changes


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class Connection:
    api_url: str = field(repr=False)
    api_key: str = field(repr=False)
    model: str
    max_tokens: int
    timeout: float
    stream: bool
    enable_thinking: bool
    skip_special_tokens: bool

    def __repr__(self):
        return 'Connection(credentials=<redacted>)'

    def public(self):
        value = {key: getattr(self, key) for key in FIELDS - {'api_url', 'api_key'}}
        value['endpoint_sha256'] = hashlib.sha256(self.api_url.encode()).hexdigest()
        value['fingerprint'] = fingerprint(value)
        return value


def validate_connection(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('模型配置必须且只能包含示例中的八个字段。')
    value = copy.deepcopy(value)
    for key in ('api_url', 'api_key', 'model'):
        if not isinstance(value[key], str) or not value[key].strip() or 'replace-me' in value[key].lower():
            raise ValueError(f'{key} 尚未填写有效值。')
        value[key] = value[key].strip()
    try:
        url = urlsplit(value['api_url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError()
        _ = url.port
    except ValueError:
        raise ValueError('api_url 应为不嵌入凭证的完整 HTTP(S) 调用地址。') from None
    if type(value['max_tokens']) is not int or value['max_tokens'] < 1:
        raise ValueError('max_tokens 必须是正整数。')
    if type(value['timeout']) not in (int, float) or not 0 < value['timeout'] < float('inf'):
        raise ValueError('timeout 必须是有限正数。')
    value['timeout'] = float(value['timeout'])
    for key in ('stream', 'enable_thinking', 'skip_special_tokens'):
        if type(value[key]) is not bool:
            raise ValueError(f'{key} 必须为布尔值。')
    return Connection(**value)


class ModelSettings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.values = dict(DEFAULTS)
        self.current = None
        self.error = '尚未配置模型，请打开模型设置。'
        self.secrets = set()
        try:
            raw = strict_json(self.path.read_text(encoding='utf-8-sig'))
            if isinstance(raw, dict) and set(raw) == FIELDS:
                self.values = raw
                if isinstance(raw.get('api_key'), str) and raw['api_key']:
                    self.secrets.add(raw['api_key'])
            self.current = validate_connection(raw)
            self.error = ''
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError, ValueError) as exc:
            self.error = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else '模型配置无法读取或不是合法 JSON，请在设置页重新填写。'

    def sanitize(self, value, extra_secrets=()):
        import html
        from urllib.parse import quote, quote_plus
        secrets = self.secrets | set(extra_secrets)
        variants = set()
        for key in secrets:
            if key:
                variants.update((key, json.dumps(key, ensure_ascii=True)[1:-1], json.dumps(key, ensure_ascii=False)[1:-1],
                                 quote(key, safe=''), quote_plus(key), html.escape(key, quote=True)))
        def clean(item):
            if isinstance(item, dict):
                return {k: '<redacted>' if k.lower() in ('api_key', 'authorization', 'secret', 'access_token') else clean(v) for k, v in item.items()}
            if isinstance(item, list):
                return [clean(v) for v in item]
            if isinstance(item, str):
                for key in sorted(variants, key=len, reverse=True):
                    if key:
                        item = item.replace(key, '<redacted>')
            return item
        return clean(value)

    def public(self):
        with self.lock:
            public = {k: v for k, v in self.values.items() if k != 'api_key'}
            return self.sanitize({'configured': self.current is not None, 'message': self.error or '配置已生效；修改影响所有项目的后续调用。',
                                  'settings': public, 'api_key_configured': bool(self.values.get('api_key')),
                                  'parameters': self.current.public() if self.current else None})

    def capture(self):
        with self.lock:
            if not self.current:
                raise ValueError(self.error)
            return self.current

    def candidate(self, body):
        with self.lock:
            value = copy.deepcopy(body)
            value['api_key'] = value.get('api_key') or self.values.get('api_key', '')
            return validate_connection(value)

    def _write(self, value):
        from .store import uid
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + '.' + uid('tmp'))
        try:
            with temp.open('w', encoding='utf-8', newline='\n') as stream:
                os.chmod(temp, 0o600)
                stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.exists():
                temp.unlink()

    def save(self, body):
        with self.lock:
            candidate = self.candidate(body)
            value = {key: getattr(candidate, key) for key in FIELDS}
            self._write(value)
            self.values, self.current, self.error = value, candidate, ''
            self.secrets.add(candidate.api_key)
            return self.public()

    def clear_key(self):
        with self.lock:
            value = {**self.values, 'api_key': ''}
            self._write(value)
            self.values, self.current, self.error = value, None, '密钥已清除，需要重新填写后才能调用模型。'
            return self.public()


class GatewayError(RuntimeError):
    def __init__(self, message, trace):
        super().__init__(message)
        self.trace = trace


class Gateway:
    def __init__(self, settings):
        self.settings = settings

    def payload(self, messages, connection=None):
        body = {'messages': copy.deepcopy(messages)}
        if connection:
            body.update(model=connection.model, max_tokens=connection.max_tokens, stream=connection.stream,
                        extra_body={'enable_thinking': connection.enable_thinking, 'skip_special_tokens': connection.skip_special_tokens})
        return body

    def preview(self, messages, connection=None):
        body = self.payload(messages, connection)
        return self.settings.sanitize({'method': 'POST', 'endpoint': '<configured-server-endpoint>',
            'model_config': connection.public() if connection else None, 'body': body,
            'notice': None if connection else '仅消息预览；模型尚未配置，不可发送。'})

    def invoke(self, messages, connection, probe=False):
        started = time.perf_counter()
        body = self.payload(messages, connection)
        if probe:
            body['max_tokens'] = min(connection.max_tokens, 32)
        trace = {'request': self.preview(messages, connection), 'raw_response': None, 'raw_output': None,
                 'usage': None, 'latency_ms': None, 'first_token_latency_ms': None, 'http_status': None, 'model_called': True}
        clean = lambda value: self.settings.sanitize(value, (connection.api_key,))
        trace['request']['body'] = clean(body)
        try:
            with httpx.Client(timeout=connection.timeout, follow_redirects=False, trust_env=False) as client:
                with client.stream('POST', connection.api_url, headers={'Authorization': f'Bearer {connection.api_key}', 'Content-Type': 'application/json'}, json=body) as response:
                    trace['http_status'] = response.status_code
                    if not 200 <= response.status_code < 300:
                        trace['raw_response'] = clean(response.read().decode('utf-8', errors='replace'))
                        raise ValueError(f'模型 HTTP 请求失败（{response.status_code}）。')
                    if not connection.stream:
                        raw = response.read().decode('utf-8', errors='replace')
                        trace['raw_response'] = clean(raw)
                        payload = strict_json(raw)
                        trace['raw_response'] = clean(payload)
                        message = payload['choices'][0]['message']
                        if not isinstance(message, dict):
                            raise ValueError('模型返回的 message 不是对象。')
                        text = message.get('content') or ''
                        trace['usage'] = clean(payload.get('usage'))
                    else:
                        events, chunks, parts = [], [], []
                        done, recognized = False, False
                        for line in response.iter_lines():
                            if not line.startswith('data:'):
                                continue
                            data = line[5:].strip()
                            events.append(data)
                            trace['raw_response'] = clean({'events': events, 'chunks': chunks})
                            if data == '[DONE]':
                                done = True
                                break
                            chunk = strict_json(data)
                            chunks.append(chunk)
                            choices = chunk.get('choices', [])
                            if isinstance(choices, list):
                                recognized = True
                            if chunk.get('usage') is not None:
                                trace['usage'] = clean(chunk['usage'])
                            if choices:
                                part = choices[0].get('delta', {}).get('content')
                                if part is not None:
                                    if not isinstance(part, str):
                                        raise ValueError('SSE content 应为文本。')
                                    if part and trace['first_token_latency_ms'] is None:
                                        trace['first_token_latency_ms'] = round((time.perf_counter() - started) * 1000, 2)
                                    parts.append(part)
                                    trace['raw_output'] = clean(''.join(parts))
                                if choices[0].get('finish_reason'):
                                    done = True
                            trace['raw_response'] = clean({'events': events, 'chunks': chunks})
                        if not done or not recognized:
                            raise ValueError('SSE 响应中断或不符合 Chat Completions 协议。')
                        text = ''.join(parts)
                    if not isinstance(text, str) or (not probe and not text.strip()):
                        raise ValueError('模型未返回有效文本，请查看原始响应。')
                    trace['raw_output'] = clean(text)
            trace['latency_ms'] = round((time.perf_counter() - started) * 1000, 2)
            return clean(trace)
        except Exception as exc:
            trace['latency_ms'] = round((time.perf_counter() - started) * 1000, 2)
            if isinstance(exc, httpx.TimeoutException):
                message = '模型请求超时。'
            elif isinstance(exc, httpx.HTTPError):
                message = '模型网络连接失败，请检查地址和服务。'
            elif isinstance(exc, (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError)):
                message = '模型响应不符合 JSON / Chat Completions 协议，请查看保留的原始响应。'
            elif isinstance(exc, ValueError):
                message = str(exc)
            else:
                message = '模型请求处理失败。'
            raise GatewayError(clean(message), clean(trace)) from None

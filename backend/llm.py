"""Server-owned connection settings and a transport with persistable public traces."""
import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx

FIELDS = {'api_url', 'api_key', 'model', 'max_tokens', 'timeout', 'stream', 'enable_thinking', 'skip_special_tokens'}


def strict_json(value):
    return json.loads(value, parse_constant=lambda item: (_ for _ in ()).throw(ValueError('JSON 不支持非有限数值。')))


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
        return f'Connection(model={self.model!r}, credentials=<redacted>)'

    def public(self):
        value = {key: getattr(self, key) for key in FIELDS - {'api_url', 'api_key'}}
        value['endpoint_sha256'] = hashlib.sha256(self.api_url.encode()).hexdigest()
        value['fingerprint'] = fingerprint(value)
        return value


def load_connection(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raise ValueError('尚未配置本地 LLM JSON，请按示例创建配置并重启。') from None
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError('LLM 配置文件无法读取或不是合法 JSON。') from None
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError('LLM 配置必须且只能包含：' + ', '.join(sorted(FIELDS)))
    for name in ('api_url', 'api_key', 'model'):
        if not isinstance(value[name], str) or not value[name].strip() or 'replace-me' in value[name].lower():
            raise ValueError(f'LLM 配置字段 {name} 未填写有效值。')
        value[name] = value[name].strip()
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
    for name in ('stream', 'enable_thinking', 'skip_special_tokens'):
        if type(value[name]) is not bool:
            raise ValueError(f'{name} 必须是布尔值。')
    return Connection(**value)


class GatewayError(RuntimeError):
    def __init__(self, message, trace):
        super().__init__(message)
        self.trace = trace


class LlmService:
    def __init__(self, path=None, transport=None):
        self.connection = None
        self.error = '尚未配置服务连接。'
        self.transport = transport
        if path is not None:
            try:
                self.connection = load_connection(path)
                self.error = ''
            except ValueError as exc:
                self.error = str(exc)

    def status(self):
        return {'configured': self.connection is not None, 'message': self.error or '配置就绪，修改后重启生效。',
                'source': 'server_json', 'parameters': self.connection.public() if self.connection else None}

    def require(self):
        if self.connection is None:
            raise ValueError(self.error)
        return self.connection

    def sanitize(self, value):
        if isinstance(value, dict):
            return {key: ('<redacted>' if key.lower() in ('api_key', 'authorization', 'access_token', 'secret') else self.sanitize(child)) for key, child in value.items()}
        if isinstance(value, list):
            return [self.sanitize(child) for child in value]
        if isinstance(value, str) and self.connection:
            return value.replace(self.connection.api_key, '<redacted>').replace(self.connection.api_url, '<server-endpoint>')
        return value

    def prepare(self, request, mode='mock'):
        request = copy.deepcopy(request)
        request.update(execution_mode=mode, is_mock=mode == 'mock')
        if mode == 'real':
            cfg = self.require()
            request['url'] = '<server-endpoint>'
            request['headers'] = {'Content-Type': 'application/json'}
            request.pop('api_key_env', None)
            request['timeout_seconds'] = cfg.timeout
            request['model_config'] = cfg.public()
            request['body'] = {'model': cfg.model, 'messages': request['body']['messages'], 'max_tokens': cfg.max_tokens,
                               'stream': cfg.stream, 'extra_body': {'enable_thinking': cfg.enable_thinking, 'skip_special_tokens': cfg.skip_special_tokens}}
        return self.sanitize(request)

    def invoke(self, request, mock_text=None, scenario='pass'):
        started = time.perf_counter()
        trace = {'request': request, 'raw_response': None, 'raw_output': None, 'usage': None,
                 'latency_ms': None, 'first_token_latency_ms': None, 'model_called': True}
        try:
            if request.get('execution_mode', 'mock') == 'mock':
                if scenario == 'request_error':
                    raise ValueError('模拟调用失败，未发送网络请求。')
                text = '{mock: invalid JSON' if scenario == 'invalid_json' else '{}' if scenario == 'schema_error' else mock_text
                response = {'object': 'chat.completion', 'model': request['body']['model'], 'is_mock': True,
                            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}], 'usage': None}
            else:
                cfg = self.require()
                if request.get('model_config', {}).get('fingerprint') != cfg.public()['fingerprint']:
                    raise ValueError('服务端公开配置已变化，请创建新运行。')
                with httpx.Client(timeout=cfg.timeout, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                    with client.stream('POST', cfg.api_url, headers={'Authorization': f'Bearer {cfg.api_key}', 'Content-Type': 'application/json'}, json=request['body']) as http_response:
                        if not 200 <= http_response.status_code < 300:
                            trace['raw_response'] = {'http_status': http_response.status_code, 'body': self.sanitize(http_response.read().decode('utf-8', errors='replace'))}
                            raise ValueError(f'LLM HTTP 请求失败（{http_response.status_code}），请检查服务配置。')
                        if not cfg.stream:
                            response = http_response.read()
                            trace['raw_response'] = self.sanitize(response.decode('utf-8', errors='replace'))
                            try:
                                response = strict_json(response)
                                trace['raw_response'] = self.sanitize(response)
                                text = response['choices'][0]['message']['content']
                            except (ValueError, KeyError, IndexError, TypeError):
                                raise ValueError('LLM 服务返回结构不符合 chat completion 协议。') from None
                        else:
                            chunks, events, parts, usage, finished = [], [], [], None, False
                            for line in http_response.iter_lines():
                                if not line.startswith('data:'):
                                    continue
                                data = line[5:].strip()
                                events.append(data)
                                trace['raw_response'] = {'chunks': self.sanitize(chunks), 'events': self.sanitize(events)}
                                if data == '[DONE]':
                                    finished = True
                                    break
                                try:
                                    chunk = strict_json(data)
                                    chunks.append(chunk)
                                    usage = chunk.get('usage') or usage
                                    trace['usage'] = self.sanitize(usage)
                                    choices = chunk.get('choices', [])
                                    if choices:
                                        part = choices[0].get('delta', {}).get('content')
                                        if choices[0].get('finish_reason'):
                                            finished = True
                                        if part is not None:
                                            if not isinstance(part, str):
                                                raise ValueError()
                                            if part and trace['first_token_latency_ms'] is None:
                                                trace['first_token_latency_ms'] = round((time.perf_counter() - started) * 1000, 2)
                                            parts.append(part)
                                            trace['raw_output'] = ''.join(parts)
                                    trace['raw_response'] = {'chunks': self.sanitize(chunks), 'events': self.sanitize(events)}
                                except (ValueError, KeyError, IndexError, TypeError):
                                    raise ValueError('LLM 流式响应格式错误，已保留可用的部分响应。') from None
                            if not finished:
                                raise ValueError('LLM 流式响应在结束标记前中断。')
                            text = ''.join(parts)
                            response = {'object': 'chat.completion', 'model': cfg.model, 'is_mock': False, 'chunks': chunks, 'events': events,
                                        'choices': [{'message': {'role': 'assistant', 'content': text}}], 'usage': usage}
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('LLM 未返回有效文本。')
            trace.update(raw_response=self.sanitize(response), raw_output=self.sanitize(text),
                         usage=self.sanitize(response.get('usage')) if isinstance(response, dict) else None)
            trace['latency_ms'] = round((time.perf_counter() - started) * 1000, 2)
            return trace
        except Exception as exc:
            trace['latency_ms'] = round((time.perf_counter() - started) * 1000, 2)
            if isinstance(exc, httpx.TimeoutException):
                message = 'LLM 请求超时。'
            elif isinstance(exc, httpx.HTTPError):
                message = 'LLM 网络请求失败，请检查连接与服务状态。'
            elif isinstance(exc, ValueError):
                message = self.sanitize(str(exc))
            else:
                message = 'LLM 请求处理失败。'
            raise GatewayError(message, self.sanitize(trace)) from None

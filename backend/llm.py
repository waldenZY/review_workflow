"""Real HTTP only. Immutable connections are captured before each operation."""
import copy
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

import httpx

FIELDS = {'api_url', 'api_key', 'model', 'max_tokens', 'timeout', 'stream', 'enable_thinking', 'skip_special_tokens'}
DEFAULTS = dict(api_url='', api_key='', model='', max_tokens=4096, timeout=120, stream=False,
                enable_thinking=False, skip_special_tokens=False)


def validate_execution(value):
    if not isinstance(value, dict) or set(value) - {'batch_concurrency'}:
        raise ValueError('执行设置仅支持 batch_concurrency。')
    concurrency = value.get('batch_concurrency', 1)
    if type(concurrency) is not int or not 1 <= concurrency <= 32:
        raise ValueError('批跑并发数必须为 1–32 的整数。')
    return {'batch_concurrency': concurrency}


def strict_json(value):
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('JSON 对象存在重复键，不允许覆盖同名属性。')
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('JSON 不支持非有限数值。')))


def parse_output(raw):
    import re
    text = raw.strip().lstrip('\ufeff').strip()
    changes = []
    if text != raw:
        changes.append('trim_whitespace_or_bom')
    # Fences inside a valid JSON string are data, not response wrappers.
    try:
        strict_json(text)
        return text, changes
    except (ValueError, TypeError):
        pass
    match = re.fullmatch(r'```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```', text, re.IGNORECASE)
    single = False
    if match:
        try:
            strict_json(match.group(1).strip())
            single = True
        except (ValueError, TypeError):
            single = '```' not in match.group(1)
    if match and single:
        text = match.group(1).strip()
        changes.append('removed_outer_code_fence')
    elif '```' in text:
        changes.append('rejected_non_outer_or_multiple_fences')
    return text, changes


def decode_output(text, changes):
    """Strict JSON with actionable diagnostics; normalization is retained by callers."""
    if 'rejected_non_outer_or_multiple_fences' in changes:
        raise ValueError('输出包含围栏外文字、多个围栏或不完整围栏；仅接受纯 JSON 或完整包裹响应的单个 JSON／无语言标记围栏。')
    try:
        return strict_json(text)
    except (ValueError, TypeError) as exc:
        prefix = '已剥离完整外围围栏，但内部 JSON 语法错误' if 'removed_outer_code_fence' in changes else '原始正文不是合法 JSON（未剥离围栏）'
        raise ValueError(prefix + '：' + str(exc)) from None


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
    profile_id: str = ''
    profile_name: str = ''
    # Workbench metadata captured with the connection, never part of HTTP parameters/fingerprint.
    batch_concurrency: int = 1

    def __repr__(self):
        return 'Connection(credentials=<redacted>)'

    def public(self):
        value = {key: getattr(self, key) for key in FIELDS - {'api_url', 'api_key'}}
        value['endpoint_sha256'] = hashlib.sha256(self.api_url.encode()).hexdigest()
        value['fingerprint'] = fingerprint(value)
        value.update(profile_id=self.profile_id, profile_name=self.profile_name)
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
        self.profiles = {}
        self.active_id = None
        self.error = ''
        self.secrets = set()
        try:
            raw = strict_json(self.path.read_text(encoding='utf-8-sig'))
            # Register credentials before validation, including malformed local files.
            def register(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key == 'api_key' and isinstance(item, str) and item:
                            self.secrets.add(item)
                        else:
                            register(item)
                elif isinstance(value, list):
                    for item in value:
                        register(item)
            register(raw)
            if isinstance(raw, dict) and set(raw) == FIELDS:
                # In-memory conversion only; startup never writes config or probes a provider.
                raw = {'schema_version': 2, 'active_profile_id': 'profile_original',
                       'profiles': [{'id': 'profile_original', 'name': '原有配置', 'config': raw}]}
            if not isinstance(raw, dict) or set(raw) != {'schema_version', 'active_profile_id', 'profiles'} or raw['schema_version'] != 2 or not isinstance(raw['profiles'], list):
                raise ValueError('配置文件结构错误，请参考命名配置示例。')
            profiles = {}
            names = set()
            for item in raw['profiles']:
                if not isinstance(item, dict) or not {'id', 'name', 'config'} <= set(item) or set(item) - {'id', 'name', 'config', 'execution'}:
                    raise ValueError('命名配置必须包含 id、name、config，可选 execution。')
                import re
                if not isinstance(item['id'], str) or not re.fullmatch(r'profile_[A-Za-z0-9_-]+', item['id']) or item['id'] in profiles:
                    raise ValueError('配置 ID 非法或重复。')
                name = self._name(item['name'])
                if name in names:
                    raise ValueError('配置名称不可重复。')
                config = copy.deepcopy(item['config'])
                if not isinstance(config, dict) or set(config) != FIELDS or not isinstance(config.get('api_key'), str):
                    raise ValueError('连接配置必须包含原有八个字段。')
                validate_connection({**config, 'api_key': config['api_key'] or 'cleared-key'})
                execution = validate_execution(item.get('execution', {}))
                profiles[item['id']] = {'id': item['id'], 'name': name, 'config': config, 'execution': execution}
                names.add(name)
            active_id = raw['active_profile_id']
            if active_id is not None and (not isinstance(active_id, str) or active_id not in profiles):
                raise ValueError('当前配置 ID 不存在。')
            self.profiles, self.active_id = profiles, active_id
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError, ValueError) as exc:
            self.error = self.sanitize(str(exc)) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else '模型配置无法读取或不是合法 JSON，请在设置页重新填写。'

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
            raise ValueError('配置名称应为 1–120 个字符。')
        return name.strip()

    def _connection(self, profile_id):
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise ValueError('原模型配置已删除或不存在，请选择配置并新建复测。')
        return replace(validate_connection(profile['config']), profile_id=profile_id, profile_name=profile['name'],
                       batch_concurrency=profile['execution']['batch_concurrency'])

    @property
    def current(self):
        with self.lock:
            try:
                return self._connection(self.active_id) if self.active_id else None
            except ValueError:
                return None

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
            current = self.current
            profiles = [{'id': p['id'], 'name': p['name'], 'settings': {k: v for k, v in p['config'].items() if k != 'api_key'},
                         'execution': copy.deepcopy(p['execution']), 'api_key_configured': bool(p['config']['api_key']),
                         'active': p['id'] == self.active_id} for p in self.profiles.values()]
            active = self.profiles.get(self.active_id)
            message = self.error or ('当前配置影响所有项目的后续调用；已提交任务保持原连接。' if current else '请选择并启用有效的命名配置；未配置仍可编辑、预览和执行程序规则。')
            return self.sanitize({'configured': current is not None, 'message': message, 'active_profile_id': self.active_id,
                'active_profile_name': active['name'] if active else None, 'profiles': profiles,
                'settings': {k: v for k, v in (active['config'] if active else DEFAULTS).items() if k != 'api_key'},
                'execution': copy.deepcopy(active['execution']) if active else {'batch_concurrency': 1},
                'parameters': current.public() if current else None})

    def capture(self, profile_id=None):
        with self.lock:
            selected = profile_id or self.active_id
            if not selected:
                raise ValueError(self.error or '尚未启用模型配置，请打开模型设置。')
            return self._connection(selected)

    def candidate(self, body, profile_id=None):
        with self.lock:
            value = copy.deepcopy(body)
            saved = self.profiles.get(profile_id) if profile_id else None
            if profile_id and saved is None:
                raise ValueError('配置不存在。')
            value['api_key'] = value.get('api_key') or (saved['config']['api_key'] if saved else '')
            result = validate_connection(value)
            return replace(result, profile_id=profile_id or '', profile_name=saved['name'] if saved else '未保存配置')

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

    def _commit(self, profiles, active_id):
        self._write({'schema_version': 2, 'active_profile_id': active_id, 'profiles': list(profiles.values())})
        self.profiles, self.active_id, self.error = profiles, active_id, ''

    def save(self, name, body, profile_id=None, execution=None):
        from .store import uid
        with self.lock:
            name = self._name(name)
            if any(p['name'] == name and p['id'] != profile_id for p in self.profiles.values()):
                raise ValueError('配置名称不可重复。')
            candidate = self.candidate(body, profile_id)
            execution = validate_execution({} if execution is None else execution)
            value = {key: getattr(candidate, key) for key in FIELDS}
            profile_id = profile_id or uid('profile')
            profiles = copy.deepcopy(self.profiles)
            profiles[profile_id] = {'id': profile_id, 'name': name, 'config': value, 'execution': execution}
            self.secrets.add(candidate.api_key)
            self._commit(profiles, self.active_id)
            return {'profile_id': profile_id, **self.public()}

    def activate(self, profile_id):
        with self.lock:
            self._connection(profile_id)
            self._commit(copy.deepcopy(self.profiles), profile_id)
            return self.public()

    def delete(self, profile_id):
        with self.lock:
            if profile_id not in self.profiles:
                raise KeyError('配置不存在。')
            profiles = copy.deepcopy(self.profiles)
            del profiles[profile_id]
            self._commit(profiles, None if self.active_id == profile_id else self.active_id)
            return self.public()

    def clear_key(self, profile_id):
        with self.lock:
            if profile_id not in self.profiles:
                raise KeyError('配置不存在。')
            profiles = copy.deepcopy(self.profiles)
            profiles[profile_id]['config']['api_key'] = ''
            self._commit(profiles, self.active_id)
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

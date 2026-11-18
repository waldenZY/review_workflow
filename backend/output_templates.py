"""Compile the small, non-executable per-criterion output language."""
import json
import math


def pointer(value):
    return value.replace('~', '~0').replace('/', '~1')


class TemplateParser:
    def __init__(self, text):
        self.text, self.pos, self.field = text, 0, None
        self.decoder = json.JSONDecoder()

    def error(self, message):
        line = self.text.count('\n', 0, self.pos) + 1
        column = self.pos - self.text.rfind('\n', 0, self.pos)
        field = f'字段「{self.field}」：' if self.field is not None else ''
        raise ValueError(f'输出模板第 {line} 行第 {column} 列：{field}{message}')

    def space(self):
        while self.pos < len(self.text) and self.text[self.pos] in ' \t\r\n':
            self.pos += 1

    def take(self, token):
        self.space()
        if not self.text.startswith(token, self.pos):
            self.error('应为 ' + token)
        self.pos += len(token)

    def string(self):
        self.space()
        if self.pos >= len(self.text) or self.text[self.pos] != '"':
            self.error('必须使用双引号字符串')
        try:
            value, end = self.decoder.raw_decode(self.text, self.pos)
        except ValueError:
            self.error('字符串或转义不合法')
        self.pos = end
        if not value.strip():
            self.error('名称或候选项不能为空')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            self.error('字符串包含非法 Unicode 字符')
        return value

    def parse(self):
        self.take('{')
        properties, fields, example = {}, [], {}
        while True:
            self.field = None
            name = self.string()
            self.field = name
            if name in properties:
                self.error('字段名重复')
            self.take(':')
            self.space()
            kind = next((k for k in ('multichoice', 'choice', 'value') if self.text.startswith(k, self.pos)), None)
            if kind is None:
                self.error('只支持 choice、multichoice、value:string、value:number')
            self.take(kind)
            self.take(':')
            if kind == 'value':
                self.space()
                value_type = next((k for k in ('string', 'number') if self.text.startswith(k, self.pos)), None)
                if value_type is None:
                    self.error('value 类型只能为 string 或 number')
                self.take(value_type)
                schema = {'type': value_type}
                sample = '结构示例，不代表判断' if value_type == 'string' else 0
            else:
                self.take('[')
                options = []
                while True:
                    option = self.string()
                    if option in options:
                        self.error('候选项重复')
                    options.append(option)
                    self.space()
                    if self.text.startswith(']', self.pos):
                        self.take(']')
                        break
                    self.take(',')
                schema = {'type': 'string', 'enum': options}
                sample = options[0]
                if kind == 'multichoice':
                    schema = {'type': 'array', 'items': schema, 'uniqueItems': True}
                    sample = []
            properties[name], example[name] = schema, sample
            fields.append({'name': name, 'type': schema['type'], 'rule': kind, 'path': '/' + pointer(name)})
            self.space()
            if self.text.startswith('}', self.pos):
                self.take('}')
                break
            self.take(',')
        self.space()
        if self.pos != len(self.text):
            self.error('对象结束后不能包含其他内容')
        return {'response_schema': {'type': 'object', 'properties': properties,
                'required': list(properties), 'additionalProperties': False},
                'output_fields': fields, 'example': example}


def compile_template(template):
    return TemplateParser(template).parse()


def compile_standards(standards):
    properties, fields, example = {}, [], {}
    for criterion in standards:
        if criterion['method'] != 'llm':
            continue
        name = criterion['name']
        if name in properties:
            raise ValueError('评测标准名称重复：' + name)
        try:
            result = compile_template(criterion['output_template'])
        except ValueError as exc:
            raise ValueError(f'标准「{name}」：{exc}') from None
        properties[name] = result['response_schema']
        example[name] = result['example']
        for field in result['output_fields']:
            fields.append({**field, 'standard_id': criterion['id'], 'standard_name': name,
                           'path': '/' + pointer(name) + field['path']})
    return {'response_schema': {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False},
            'output_fields': fields,
            'example_preview': {'value': example, 'message': '仅演示结构，不代表模型判断或默认答案。'}}


def decode_evaluation(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('JSON 对象存在重复键：' + key)
            value[key] = item
        return value

    def finite(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('JSON 不允许非有限数值')
        return value

    def invalid(value):
        raise ValueError('JSON 不允许非有限数值：' + value)

    value = json.loads(raw, object_pairs_hook=unique, parse_float=finite, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError('评测响应顶层必须为 JSON 对象，不接受代码围栏或解释文字。')
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode('utf-8')
            except UnicodeEncodeError:
                raise ValueError('JSON 包含非法 Unicode 字符') from None
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return value

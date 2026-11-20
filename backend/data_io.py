import hashlib
import io
import json
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE


MISSING = object()
MAX_IMPORT_BYTES = 100 * 1024 * 1024


def check_import_size(content):
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError('单文件最大 100 MB。')


def pointer_escape(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def at(value, path, default=MISSING):
    if path == '':
        return value
    if not path.startswith('/'):
        raise ValueError('字段路径必须使用 JSON Pointer，例如 /answer。')
    try:
        for token in path[1:].split('/'):
            token = token.replace('~1', '/').replace('~0', '~')
            value = value[int(token)] if isinstance(value, list) else value[token]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        return default


def fields_for(records):
    found = {}

    def visit(value, path):
        if path:
            found.setdefault(path, set()).add(type(value).__name__)
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, f'{path}/{pointer_escape(key)}')

    for row in records:
        visit(row, '')
    return [{'path': path, 'types': sorted(types)} for path, types in found.items()]


def excel_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def parse_file(filename, content, sheet=None, header_row=1):
    suffix = Path(filename).suffix.lower()
    if suffix not in ('.jsonl', '.json', '.xlsx'):
        raise ValueError('仅支持 JSONL、JSON、XLSX，不支持 CSV。')
    check_import_size(content)
    sources = []
    sheet_name = None
    if suffix in ('.json', '.jsonl'):
        text = content.decode('utf-8-sig')
        if suffix == '.json':
            records = json.loads(text)
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list):
                raise ValueError('JSON 顶层应是对象或对象数组。')
            sources = list(range(1, len(records) + 1))
        else:
            records = []
            for line_number, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise ValueError(f'JSONL 第 {line_number} 行不是合法 JSON：{exc.msg}') from exc
                    sources.append(line_number)
    else:
        if header_row < 1:
            raise ValueError('表头行必须大于等于 1。')
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False)
        try:
            sheet_name = sheet or workbook.sheetnames[0]
            if sheet_name not in workbook.sheetnames:
                raise ValueError('工作表不存在。')
            ws = workbook[sheet_name]
            rows = ws.iter_rows(min_row=header_row, values_only=True)
            header = next(rows, None)
            if not header or any(value is None or not str(value).strip() for value in header):
                raise ValueError('表头不能为空；请检查所选表头行及空列。')
            names = [str(value) for value in header]
            if len(names) != len(set(names)):
                raise ValueError('表头不可重复。')
            records = []
            for index, row in enumerate(rows, header_row + 1):
                if all(value is None for value in row):
                    continue
                records.append({name: excel_value(value) for name, value in zip(names, row)})
                sources.append(index)
        finally:
            workbook.close()
    if not records or any(not isinstance(row, dict) for row in records):
        raise ValueError('文件必须包含至少一条对象记录，每条记录均应是对象。')
    # Reject non-standard numeric values before they reach SQLite/API/Excel.
    json.dumps(records, allow_nan=False)
    return records, sources, sheet_name, hashlib.sha256(content).hexdigest()


def flatten(value, prefix=''):
    result = {}
    if isinstance(value, dict) and value:
        for key, child in value.items():
            result.update(flatten(child, f'{prefix}/{pointer_escape(key)}'))
    else:
        result[prefix] = value
    return result


def write_xlsx(path, rows, columns):
    if len(columns)>16384:
        raise ValueError('超过 XLSX 列容量。')
    workbook=Workbook(write_only=True)
    ws=workbook.create_sheet('评测结果')
    ws.freeze_panes='A2'
    overflow = None
    overflow_rows = 1

    def cell_for(sheet, value):
        cell = WriteOnlyCell(sheet, value=value)
        if isinstance(value, str):
            cell.data_type = 's'
        return cell

    try:
        from itertools import chain
        count=0
        for row_index,row in enumerate(chain((columns,),rows),1):
            if row_index>1048576:
                raise ValueError('超过 XLSX 行容量，请使用 JSONL。')
            cells=[]
            for col_index,value in enumerate(row,1):
                if isinstance(value,(dict,list)):
                    value=json.dumps(value,ensure_ascii=False,allow_nan=False)
                if isinstance(value, str):
                    invalid_xml = bool(ILLEGAL_CHARACTERS_RE.search(value)) or '\ufffe' in value or '\uffff' in value
                    too_long = len(value) > 32767 or (len(value) > 16383 and len(value.encode('utf-16-le')) > 65534)
                    if invalid_xml or too_long:
                        if row_index == 1:
                            raise ValueError(f'第 {col_index} 个列名超过 Excel 限制或含不支持的字符，请使用 JSONL。')
                        if overflow is None:
                            overflow = workbook.create_sheet('长文本')
                            overflow.freeze_panes = 'A2'
                            overflow.append(['结果表行号', '结果表列号', '列名', '分段序号', '内容格式', '内容'])
                        content = json.dumps(value, ensure_ascii=True) if invalid_xml else value
                        content_format = 'JSON 字符串' if invalid_xml else '原文'
                        for index, start in enumerate(range(0, len(content), 16383), 1):
                            overflow_rows += 1
                            if overflow_rows > 1048576:
                                raise ValueError('长文本超过 XLSX 行容量，请使用 JSONL。')
                            part = content[start:start + 16383]
                            overflow.append([row_index, col_index, cell_for(overflow, columns[col_index - 1]),
                                             index, content_format, cell_for(overflow, part)])
                        value = f'完整内容见「长文本」第 {row_index} 行、第 {col_index} 列对应的分段'
                cells.append(cell_for(ws, value))
            ws.append(cells)
            count=row_index
        from openpyxl.utils import get_column_letter
        if columns:
            ws.auto_filter.ref=f'A1:{get_column_letter(len(columns))}{count}'
        workbook.save(path)
    finally:
        workbook.close()

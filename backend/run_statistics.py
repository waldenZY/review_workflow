"""Summarize stored group results using the output schema pinned to the run."""
from collections import Counter
from decimal import Decimal, localcontext
import json
import math

from .data_io import MISSING, at


def field_stats(field, schema):
    stats = {**field, 'count': 0}
    if field['rule'] in ('choice', 'multichoice'):
        choices = schema['enum'] if field['rule'] == 'choice' else schema['items']['enum']
        stats['counts'] = dict.fromkeys(choices, 0)
        if field['rule'] == 'multichoice':
            stats['empty'] = 0
    elif field['type'] == 'number':
        stats.update(total=Decimal(0), min=None, max=None)
    else:
        stats['empty'] = 0
    return stats


def add_value(stats, value):
    if value is MISSING:
        return
    stats['count'] += 1
    if stats['rule'] == 'choice':
        stats['counts'][value] += 1
    elif stats['rule'] == 'multichoice':
        for choice in value:
            stats['counts'][choice] += 1
        stats['empty'] += not value
    elif stats['type'] == 'number':
        stats['total'] += Decimal(str(value))
        stats['min'] = value if stats['min'] is None else min(stats['min'], value)
        stats['max'] = value if stats['max'] is None else max(stats['max'], value)
    else:
        stats['empty'] += not value.strip()


def finish_field(stats):
    if 'counts' in stats:
        stats['options'] = [{'value': option, 'count': count} for option, count in stats.pop('counts').items()]
    elif stats['type'] == 'number':
        total = stats.pop('total')
        stats['mean'] = None
        if stats['count']:
            mean = total / stats['count']
            number = float(mean)
            stats['mean'] = number if math.isfinite(number) else str(mean)
        for key in ('min', 'max'):
            number = stats[key]
            if isinstance(number, int) and abs(number) > 2**53 - 1:
                stats[key] = str(number)
    else:
        stats['filled'] = stats['count'] - stats['empty']


def summarize_run(run, tasks, selected):
    groups = {}
    for group in run['snapshot']['groups']:
        schemas = group['response_schema']['properties']
        fields = [field_stats(field, schemas[field['standard_name']]['properties'][field['name']])
                  for field in group['output_fields']]
        groups[group['id']] = {'group_id': group['id'], 'name': group['name'], 'selected': selected,
                               'statuses': Counter(), 'valid': 0, 'fields': fields}

    # Sum in decimal so a large finite value cannot overflow the accumulated sum.
    with localcontext() as context:
        context.prec = 34
        for group_id, status, summary in tasks:
            group = groups[group_id]
            group['statuses'][status] += 1
            if status != 'completed' or not group['fields']:
                continue
            output = json.loads(summary).get('output')
            if not isinstance(output, dict):
                continue
            group['valid'] += 1
            for field in group['fields']:
                add_value(field, at(output, field['path']))

        for group in groups.values():
            statuses = group.pop('statuses')
            group.update(completed=statuses.pop('completed', 0), pending=statuses.pop('pending', 0),
                         running=statuses.pop('running', 0), interrupted=statuses.pop('interrupted', 0),
                         failed=sum(statuses.values()))
            for field in group['fields']:
                finish_field(field)
    return {'run_id': run['id'], 'status': run['status'], 'selected': selected, 'groups': list(groups.values())}

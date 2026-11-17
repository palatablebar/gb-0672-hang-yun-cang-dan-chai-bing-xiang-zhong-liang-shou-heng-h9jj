from decimal import Decimal
from app.coerce import number, doc_date
from app.errors import BusinessError
from app.reasons import reject

WEIGHTS = {'kg': Decimal(1), 't': Decimal(1000)}


def _convert(row, packages):
    """按指定口径的包装系数与重量单位换算。换算不了时抛出 BusinessError。"""
    if row['package_unit'] not in packages or row['weight_unit'] not in WEIGHTS:
        raise BusinessError('NONSTANDARD_UNIT' if row['package_unit'] not in packages else 'BAD_UNIT',
                            '单位在该口径下无换算' if row['package_unit'] not in packages else '重量单位无效')
    count = number(row['packages']) * Decimal(str(packages[row['package_unit']]))
    factor = WEIGHTS[row['weight_unit']]
    net = number(row['net']) * factor
    gross = number(row['gross']) * factor
    revision = number(row['revision'])
    if count < 0 or count % 1 or net < 0 or gross < net or revision < 1 or revision % 1:
        raise BusinessError('INVALID_RANGE', '件数或净毛重越界')
    return {'pieces': int(count), 'net_kg': str(net), 'gross_kg': str(gross)}


def normalize(row, context):
    """单口径换算，保留给单元测试与旧调用方；结果口径由单据日期决定。"""
    effective = doc_date(context['effective_date'])
    caliber_key = 'old' if doc_date(row['doc_date']) < effective else 'new'
    packages = context['calibers'][caliber_key]['packages']
    converted = _convert(row, packages)
    revision = int(number(row['revision']))
    return {**row, **converted, 'revision': revision,
            'caliber': caliber_key, 'caliber_label': context['calibers'][caliber_key]['label']}


def _try_convert(row, packages):
    try:
        return _convert(row, packages), None
    except BusinessError as exc:
        return None, exc.code


def normalize_dual(row, context):
    """同一行分别按新旧口径换算，记录原值、两种结果、单据日期与跨档标记。"""
    document_date = doc_date(row['doc_date'])
    effective = doc_date(context['effective_date'])
    received = doc_date(context['received_at'])
    doc_caliber = 'old' if document_date < effective else 'new'
    cross_period = (document_date < effective) != (received < effective)

    results = {}
    for key, spec in context['calibers'].items():
        converted, error = _try_convert(row, spec['packages'])
        results[key] = {'caliber': key, 'caliber_label': spec['label'],
                        'convertible': converted is not None,
                        'error_code': error,
                        **(converted or {'pieces': None, 'net_kg': None, 'gross_kg': None})}

    # 初始采用口径 = 单据日期对应口径；该口径换算失败则隔离（原值保留在隔离证据中）
    adopted = results[doc_caliber]
    if not adopted['convertible']:
        raise BusinessError(adopted['error_code'], '单据日期对应口径无法换算该行')

    return {**row,
            'pieces': adopted['pieces'], 'net_kg': adopted['net_kg'], 'gross_kg': adopted['gross_kg'],
            'revision': int(number(row['revision'])),
            'caliber': doc_caliber, 'caliber_label': adopted['caliber_label'],
            'doc_date': document_date.isoformat(),
            'doc_caliber': doc_caliber,
            'cross_period': cross_period,
            'caliber_results': results}


def apply(state):
    good = []
    for row in state['records']:
        try:
            good.append(normalize_dual(row, state['context']))
        except BusinessError as e:
            reject(state, row, e.code, e.message)
    state['records'] = good
    affected = [r for r in good if r['cross_period']]
    state['views']['packaging'] = {
        'title': '包装换算（新旧双口径）',
        'rows': good,
        'dual_rows': _dual_table(good),
        'metrics': {'映射行': len(good),
                    '跨档提单行': len(affected),
                    '包装类型_旧': len(state['context']['calibers']['old']['packages']),
                    '包装类型_新': len(state['context']['calibers']['new']['packages']),
                    '生效日期': state['context']['effective_date'],
                    '接收日期': state['context']['received_at']}}


def _dual_table(rows):
    """压平成双口径对照表，供页面同屏复核件数/净重/毛重与采用依据。"""
    table = []
    for r in rows:
        old, new = r['caliber_results']['old'], r['caliber_results']['new']
        table.append({
            'id': r['id'], 'doc_date': r['doc_date'],
            'package_unit': r['package_unit'], 'packages': r['packages'],
            'cross_period': '是' if r['cross_period'] else '否',
            'old_pieces': old['pieces'], 'old_net_kg': old['net_kg'], 'old_gross_kg': old['gross_kg'],
            'old_convertible': '可换算' if old['convertible'] else f"不可换算({old['error_code']})",
            'new_pieces': new['pieces'], 'new_net_kg': new['net_kg'], 'new_gross_kg': new['gross_kg'],
            'new_convertible': '可换算' if new['convertible'] else f"不可换算({new['error_code']})",
            'adopted_caliber': r['caliber'],
            'adopted_pieces': r['pieces'], 'adopted_net_kg': r['net_kg'], 'adopted_gross_kg': r['gross_kg'],
            'adopt_basis': r.get('adopt_basis', '按单据日期对应口径初始采用，待逐层核对裁决')})
    return table

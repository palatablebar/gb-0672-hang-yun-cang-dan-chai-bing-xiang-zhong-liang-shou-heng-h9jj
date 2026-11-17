from decimal import Decimal

FIELDS = ('pieces', 'net_kg', 'gross_kg')


def reconcile(state):
    roots = set(state['graph']['roots'])
    root_rows = [r for r in state['records'] if r['id'] in roots]
    # 逐层核对中存在差异的提单（按采用口径工作值）
    mismatch_roots = {c['id'] for c in state['weight_checks'] if c['code'] != 'BALANCED'}
    graph_bad_roots = {e['id'] for e in state['graph']['errors']}
    unresolved = set(state.get('unresolved_roots', []))

    included, excluded = [], []
    for row in root_rows:
        reason = None
        if row['id'] in unresolved:
            reason = 'CALIBER_UNRESOLVED'
        elif row['id'] in mismatch_roots:
            reason = 'ROOT_NOT_BALANCED'
        elif row['id'] in graph_bad_roots:
            reason = 'GRAPH_ROOT_BROKEN'
        if reason:
            excluded.append({'id': row['id'], 'caliber': row['caliber'], 'code': reason,
                             'pieces': row['pieces'], 'net_kg': row['net_kg'], 'gross_kg': row['gross_kg']})
        else:
            included.append(row)

    totals = {'pieces': sum(r['pieces'] for r in included),
              'net_kg': str(sum((Decimal(r['net_kg']) for r in included), Decimal(0))),
              'gross_kg': str(sum((Decimal(r['gross_kg']) for r in included), Decimal(0)))}
    mismatches = [k for k, v in totals.items()
                  if Decimal(str(v)) != Decimal(str(state['context']['declaration'][k]))]
    c = state['counts']
    row_total = len(state['records']) + len(state['quarantine']) + c['blank'] + c['duplicate'] + c['repeated_header']
    return {**totals, 'input': c['input'], 'accepted': len(state['records']),
            'quarantined': len(state['quarantine']), 'row_balanced': row_total == c['input'],
            'declaration_differences': mismatches,
            'included_roots': [r['id'] for r in included], 'excluded_roots': excluded,
            'graph_errors': len(state['graph']['errors']),
            'weight_errors': sum(r['code'] != 'BALANCED' for r in state['weight_checks'])}


def apply(state):
    state['balance'] = reconcile(state)
    state['views']['reconcile'] = {
        'title': '报关对账（未对平提单不进入汇总）',
        'rows': [state['balance']],
        'excluded_rows': state['balance']['excluded_roots'],
        'metrics': {'件数': state['balance']['pieces'], '净重_kg': state['balance']['net_kg'],
                    '毛重_kg': state['balance']['gross_kg'],
                    '纳入主单': len(state['balance']['included_roots']),
                    '排除主单': len(state['balance']['excluded_roots'])}}

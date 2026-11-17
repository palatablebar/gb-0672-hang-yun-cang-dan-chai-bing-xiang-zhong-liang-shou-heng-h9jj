import hashlib, json

def decide(state):
    b = state['balance']
    blocked = (b['graph_errors'] + b['weight_errors']
               + len(b['declaration_differences']) + len(state['hazard_issues'])
               + len(state['quarantine']) + len(b['excluded_roots']))
    evidence = {'roots': state['graph']['roots'], 'weights': state['weight_checks'],
                'hazards': state['hazard_issues'],
                'caliber_decisions': state.get('caliber_decisions', []),
                'excluded_roots': b['excluded_roots']}
    return {'status': '可封存' if b['row_balanced'] and not blocked else '待处理',
            'blocked': blocked,
            'seal': hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}

def apply(state):
    state['archive'] = decide(state)
    state['views']['archive'] = {
        'title': '版本封存',
        'rows': [{'id': r['id'], 'revision': r['revision'], 'parent': r['parent'],
                  'caliber': r['caliber'], 'adopt_basis': r.get('adopt_basis', '')} for r in state['records']],
        'metrics': {'主单': len(state['graph']['roots']), '状态': state['archive']['status'],
                    '阻断': state['archive']['blocked']}}

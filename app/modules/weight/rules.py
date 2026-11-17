from decimal import Decimal

CALIBER_FIELDS = ('pieces', 'net_kg', 'gross_kg')


def check_weights(rows, graph, caliber_key=None):
    """逐层（只加直接子节点）守恒核对。caliber_key 给定时读取该口径的换算快照；
    含不可换算子节点的父节点直接记 WEIGHT_MISMATCH，绝不把缺值按 0 参与求和。"""
    nodes = {r['id']: r for r in rows}
    results = []

    def snap_of(node):
        # 仅显式按某口径核对时读取换算快照；无口径参数时以当前工作值为准
        if caliber_key is not None and isinstance(node.get('caliber_results'), dict):
            return node['caliber_results'].get(caliber_key)
        return None

    def value(node, field):
        snap = snap_of(node)
        if snap is not None:
            if not snap['convertible']:
                return None
            return Decimal(str(snap[field]))
        return Decimal(str(node[field]))

    for parent, children in sorted(graph['children'].items()):
        if not children:
            continue
        p = nodes[parent]
        for field in CALIBER_FIELDS:
            blocked = sorted({c for c in [parent] + children
                              if (snap := snap_of(nodes[c])) is not None and not snap['convertible']})
            if blocked:
                results.append({'id': parent, 'field': field,
                                'caliber': caliber_key or p.get('caliber', 'adopted'),
                                'declared': 'N/A', 'children_sum': 'N/A',
                                'difference': 'N/A', 'code': 'WEIGHT_MISMATCH',
                                'blocked_nodes': blocked})
                continue
            total = sum((value(nodes[c], field) for c in children), Decimal(0))
            declared = value(p, field)
            difference = declared - total
            results.append({'id': parent, 'field': field, 'caliber': caliber_key or p.get('caliber', 'adopted'),
                            'declared': str(declared), 'children_sum': str(total), 'difference': str(difference),
                            'code': 'BALANCED' if not difference else 'WEIGHT_MISMATCH'})
    return results


def _subtree(root, graph):
    """返回一棵提单树的全部节点 id（含根），环/缺父级时返回 None。"""
    seen, stack = set(), [root]
    while stack:
        node = stack.pop()
        if node in seen:
            return None
        seen.add(node)
        stack.extend(graph['children'].get(node, []))
    return seen


def _caliber_balanced(rows, graph, root, caliber_key):
    """该口径下整棵提单树是否逐层对平。
    返回 (是否对平, 重量未对平节点, 不可换算节点)：不可换算只代表该口径不适用，
    不能与“原值与申报差异无法解释”混为一谈。"""
    nodes = {r['id']: r for r in rows}
    tree = _subtree(root, graph)
    if tree is None:
        return False, [], [root]
    unconvertible = []
    for node_id in tree:
        snap = nodes[node_id]['caliber_results'].get(caliber_key)
        if not snap['convertible']:
            unconvertible.append(node_id)
    bad = sorted({c['id'] for c in check_weights(rows, graph, caliber_key)
                  if c['code'] != 'BALANCED' and c['id'] in tree})
    return (not bad and not unconvertible), bad, unconvertible


def adjudicate(state):
    """按提单树裁决口径：跨档时用原值与申报差异能否解释来决定沿用旧口径还是采用新口径。"""
    policy = state.get('caliber_policy', state['context'].get('caliber_policy', 'auto'))
    return _adjudicate_with_policy(state, policy)


def _adjudicate_with_policy(state, policy):
    rows, graph, context = state['records'], state['graph'], state['context']
    checks_by_caliber = {key: check_weights(rows, graph, key) for key in ('old', 'new')}
    decisions, broken_roots = [], []

    for root in graph['roots']:
        root_row = next(r for r in rows if r['id'] == root)
        cross = root_row['cross_period']
        doc_caliber = root_row['doc_caliber']
        other_caliber = 'new' if doc_caliber == 'old' else 'old'
        doc_ok, doc_bad, doc_unconv = _caliber_balanced(rows, graph, root, doc_caliber)
        other_ok, other_bad, other_unconv = _caliber_balanced(rows, graph, root, other_caliber)
        # 该口径“能解释”= 全部行可按该口径换算且逐层守恒对平

        if policy == 'force_new':
            if other_ok:
                adopted, basis, resolved = 'new', '接收批次统一新口径：新口径可换算且逐层对平', True
            else:
                why = ('存在不可换算单位 ' + '/'.join(other_unconv)) if other_unconv and not other_bad \
                    else '新口径下逐层核对未对平 ' + '/'.join(other_bad)
                decisions.append(_decision(root, cross, doc_caliber, 'new', False,
                                           f'force_new 失败：{why}，整批回滚保留原值',
                                           doc_bad, other_bad, other_unconv))
                return checks_by_caliber, decisions, [root]
        elif not cross:
            # 同档提单不存在口径选择问题：换算/重量问题留给隔离与逐层核对阻断，不记口径未裁决
            adopted, basis, resolved = doc_caliber, '单据日期与接收日期同档，按单据日期口径', True
        elif doc_ok:
            adopted = doc_caliber
            basis = ('跨档：单据日期旧口径下逐层对平，原值与申报可解释，沿用旧口径' if doc_caliber == 'old'
                     else '跨档：单据日期新口径下逐层对平，采用新口径')
            resolved = True
        elif other_ok:
            adopted = other_caliber
            basis = ('跨档：单据日期旧口径的差异无法解释，接收日期新口径对平，改用新口径' if other_caliber == 'new'
                     else '跨档：单据日期口径无法解释，回退接收日期口径对平')
            resolved = True
        else:
            adopted, resolved = doc_caliber, False
            if other_unconv and not other_bad:
                basis = ('跨档：旧口径可换算但重量未对平；新口径对历史托盘/非标准单位无换算，'
                         '原值与申报差异无法解释，阻断申报')
            else:
                basis = '跨档：新旧两种口径下逐层核对均未对平，原值与申报差异无法解释，阻断申报'

        decisions.append(_decision(root, cross, doc_caliber, adopted, resolved, basis,
                                   doc_bad, other_bad, other_unconv))
        if not resolved:
            broken_roots.append(root)

    return checks_by_caliber, decisions, broken_roots


def _decision(root, cross, doc_caliber, adopted, resolved, basis, doc_bad, other_bad, other_unconv=None):
    return {'id': root, 'cross_period': cross, 'doc_caliber': doc_caliber,
            'adopted_caliber': adopted, 'resolved': resolved, 'basis': basis,
            'doc_caliber_bad': doc_bad, 'other_caliber_bad': other_bad,
            'other_caliber_unconvertible': other_unconv or []}


def _apply_adopted(rows, decisions):
    """把裁决后的采用口径写回每行的工作值；未裁决行沿用单据日期口径。"""
    adopted_by_root = {d['id']: d['adopted_caliber'] for d in decisions}
    basis_by_root = {d['id']: d['basis'] for d in decisions}
    parent = {r['id']: r['parent'].strip() for r in rows}

    def root_of(node_id):
        seen = set()
        while parent.get(node_id) and node_id not in seen:
            seen.add(node_id)
            node_id = parent[node_id]
        return node_id

    for row in rows:
        root = root_of(row['id'])
        key = adopted_by_root.get(root, row['doc_caliber'])
        snap = row['caliber_results'][key]
        row['caliber'] = key
        row['caliber_label'] = snap['caliber_label']
        row['pieces'], row['net_kg'], row['gross_kg'] = snap['pieces'], snap['net_kg'], snap['gross_kg']
        row['adopt_basis'] = basis_by_root.get(root, '按单据日期对应口径采用')


def apply(state):
    checks_by_caliber, decisions, broken_roots = adjudicate(state)
    state['caliber_switch_failed'] = bool(broken_roots) and state.get('caliber_policy') == 'force_new'
    if state['caliber_switch_failed']:
        # 切换失败：恢复原有结果并保留原值——按 auto 重新裁决，工作值与决策回到切换前状态
        state['caliber_rollback_from'] = 'force_new'
        state['caliber_rollback_basis'] = [d['basis'] for d in decisions if not d['resolved']]
        checks_by_caliber, decisions, broken_roots = _adjudicate_with_policy(state, 'auto')
    state['caliber_decisions'] = decisions
    _apply_adopted(state['records'], decisions)
    # 采用口径结果驱动逐层重量核对（工作值即裁决采用值）
    state['weight_checks'] = check_weights(state['records'], state['graph'])
    state['weight_checks_by_caliber'] = checks_by_caliber
    state['unresolved_roots'] = broken_roots
    state['views']['weight'] = {
        'title': '逐层守恒（采用口径驱动）',
        'rows': state['weight_checks'],
        'decisions': decisions,
        'metrics': {'检查项': len(state['weight_checks']),
                    '差异项': sum(r['code'] != 'BALANCED' for r in state['weight_checks']),
                    '跨档提单': sum(1 for d in decisions if d['cross_period']),
                    '未裁决提单': len(broken_roots)}}

"""新旧双口径包装换算、跨档裁决、逐层核对与申报汇总的边界测试。"""
import pytest
from app.engine import evaluate
from app.errors import BusinessError
from app.modules.packaging.rules import normalize, normalize_dual
from app.modules.weight.rules import check_weights, adjudicate


@pytest.fixture
def caliber():
    return evaluate({'dataset': 'caliber'})


# ---------- 双口径换算 ----------

def test_dual_results_kept_for_every_row(caliber):
    row = next(r for r in caliber['records'] if r['id'] == 'M101-P1-1')
    old, new = row['caliber_results']['old'], row['caliber_results']['new']
    # 同一历史托盘：旧口径 10 件，新口径 12 件，原值（1 pallet / 0.1t）原样保留
    assert old['pieces'] == 10 and new['pieces'] == 12
    assert old['net_kg'] == new['net_kg'] == '100.0'
    assert row['packages'] == '1' and row['package_unit'] == 'pallet'


def test_nonstandard_unit_only_unconvertible_under_new(caliber):
    row = next(r for r in caliber['records'] if r['id'] == 'M102-P2-1')  # 10 roll
    assert row['caliber_results']['old']['convertible'] is True
    assert row['caliber_results']['old']['pieces'] == 10
    new = row['caliber_results']['new']
    assert new['convertible'] is False and new['error_code'] == 'NONSTANDARD_UNIT'
    assert new['pieces'] is None  # 缺值绝不按 0 参与求和


def test_cross_period_flag(caliber):
    # 单据日期在 2025 年内、接收日期 2026-01-05，全部跨档
    assert all(r['cross_period'] for r in caliber['records'])
    clean = evaluate({'dataset': 'clean'})
    assert all(not r['cross_period'] for r in clean['records'])


def test_normalize_legacy_keeps_old_factor():
    # 旧口径 normalize 单测入口仍按单据日期选口径
    state = evaluate({'dataset': 'legacy'})
    row = next(r for r in state['records'] if r['id'] == 'M001-P1-1')
    assert row['pieces'] == 10 and row['caliber'] == 'old'


def test_bad_doc_date_quarantined():
    csv = ('id,parent,packages,package_unit,net,gross,weight_unit,hazard,un_code,container,revision,doc_date\n'
           'X,,1,carton,1,2,kg,none,,C,1,2025/12/01\n')
    s = evaluate({'csv_text': csv})
    assert s['quarantine'][0]['code'] == 'BAD_DATE'


# ---------- 跨档裁决 ----------

def test_old_caliber_kept_when_explainable(caliber):
    d = next(x for x in caliber['caliber_decisions'] if x['id'] == 'M101')
    assert d['adopted_caliber'] == 'old' and d['resolved'] is True
    assert '沿用旧口径' in d['basis']


def test_nonstandard_tree_kept_old_not_false_unresolved(caliber):
    # M102 旧口径可对平（非标准单位只是新口径不适用），沿用旧口径
    d = next(x for x in caliber['caliber_decisions'] if x['id'] == 'M102')
    assert d['adopted_caliber'] == 'old' and d['resolved'] is True
    assert d['other_caliber_unconvertible']  # 新口径不可换算节点留证


def test_new_caliber_adopted_when_old_cannot_explain(caliber):
    d = next(x for x in caliber['caliber_decisions'] if x['id'] == 'M103')
    assert d['adopted_caliber'] == 'new' and d['resolved'] is True
    assert '改用新口径' in d['basis']
    root = next(r for r in caliber['records'] if r['id'] == 'M103')
    assert root['pieces'] == 48  # 新口径工作值驱动后续核对


def test_unexplainable_tree_blocked(caliber):
    d = next(x for x in caliber['caliber_decisions'] if x['id'] == 'M104')
    assert d['resolved'] is False and caliber['unresolved_roots'] == ['M104']


def test_same_period_weight_issue_is_not_caliber_unresolved():
    # 脏批同档 M001 的 1kg 毛重差异属于重量未对平，不是口径裁决失败
    s = evaluate({'dataset': 'dirty'})
    assert all(x['resolved'] for x in s['caliber_decisions'])
    excluded = {e['id']: e['code'] for e in s['balance']['excluded_roots']}
    assert excluded.get('M001') == 'ROOT_NOT_BALANCED'


# ---------- 逐层核对由采用口径驱动 ----------

def test_weight_checks_use_adopted_values(caliber):
    m103 = [c for c in caliber['weight_checks'] if c['id'].startswith('M103') and c['field'] == 'pieces']
    assert all(c['code'] == 'BALANCED' for c in m103)
    # 双口径明细都保留可复核
    new_checks = [c for c in caliber['weight_checks_by_caliber']['new']
                  if c['id'] == 'M103' and c['field'] == 'pieces']
    assert new_checks and new_checks[0]['code'] == 'BALANCED'


def test_unconvertible_never_summed_as_zero(caliber):
    bad = [c for c in caliber['weight_checks_by_caliber']['new']
           if c['code'] != 'BALANCED' and c['id'].startswith('M102')]
    assert bad and all(c['children_sum'] == 'N/A' for c in bad)


# ---------- 未对平提单不进入申报汇总 ----------

def test_excluded_roots_not_in_declaration_totals(caliber):
    b = caliber['balance']
    assert b['included_roots'] == ['M101', 'M102', 'M103']
    assert [e['id'] for e in b['excluded_roots']] == ['M104']
    assert (b['pieces'], b['net_kg'], b['gross_kg']) == (168, '1200', '1320')
    assert b['declaration_differences'] == []  # 对平提单汇总后与申报一致


def test_archive_blocked_by_unresolved(caliber):
    assert caliber['archive']['status'] == '待处理'
    assert caliber['archive']['blocked'] >= 1


# ---------- 切换失败回滚、复核锁、幂等 ----------

def test_force_new_failure_rolls_back_original(caliber):
    rolled = evaluate({'dataset': 'caliber', 'caliber': 'force_new'})
    assert rolled['caliber_switch_failed'] is True
    # 工作值、决策与切换前 auto 完全一致（恢复原有结果并保留原值）
    for r, o in zip(rolled['records'], caliber['records']):
        assert (r['pieces'], r['net_kg'], r['gross_kg'], r['caliber']) == \
               (o['pieces'], o['net_kg'], o['gross_kg'], o['caliber'])
    assert rolled['caliber_decisions'] == caliber['caliber_decisions']
    assert rolled['caliber_rollback_basis']


def test_force_new_success_on_new_rule_batch():
    csv = ('id,parent,packages,package_unit,net,gross,weight_unit,hazard,un_code,container,revision,doc_date\n'
           'R,,48,carton,400,440,kg,none,,C,1,2025-12-31\n'
           'R-P1,R,24,carton,200,220,kg,none,,C,1,2025-12-31\n'
           'R-P1-1,R-P1,1,pallet,0.1,0.11,t,none,,C,1,2025-12-31\n'
           'R-P1-2,R-P1,1,pallet,0.1,0.11,t,none,,C,1,2025-12-31\n'
           'R-P2,R,24,carton,200,220,kg,none,,C,1,2025-12-31\n'
           'R-P2-1,R-P2,1,pallet,0.1,0.11,t,none,,C,1,2025-12-31\n'
           'R-P2-2,R-P2,1,pallet,0.1,0.11,t,none,,C,1,2025-12-31\n')
    ov = {'received_at': '2026-01-05', 'declaration': {'pieces': 48, 'net_kg': '400', 'gross_kg': '440'}}
    fn = evaluate({'csv_text': csv, 'overrides': ov, 'caliber': 'force_new'})
    assert fn['caliber_switch_failed'] is False
    assert fn['caliber_decisions'][0]['adopted_caliber'] == 'new'
    assert fn['balance']['excluded_roots'] == []


def test_rerun_does_not_accumulate(caliber):
    again = evaluate({'dataset': 'caliber'})
    assert again['manifest']['sha256'] == caliber['manifest']['sha256']
    assert again['balance'] == caliber['balance']
    third = evaluate({'dataset': 'caliber', 'caliber': 'force_new'})
    third_again = evaluate({'dataset': 'caliber', 'caliber': 'force_new'})
    assert third['manifest']['sha256'] == third_again['manifest']['sha256']


def test_policy_changes_digest_but_input_snapshot_stable(caliber):
    fn = evaluate({'dataset': 'caliber', 'caliber': 'force_new'})
    assert fn['input_snapshot']['bytes_b64'] == caliber['input_snapshot']['bytes_b64']
    assert fn['manifest']['sha256'] != caliber['manifest']['sha256']


def test_bad_caliber_policy_rejected():
    with pytest.raises(BusinessError) as exc:
        evaluate({'dataset': 'caliber', 'caliber': 'middle'})
    assert exc.value.code == 'BAD_CALIBER'


# ---------- API：复核锁 / 回滚 / 幂等 ----------

def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'caliber.sqlite'))
    return TestClient(main.app)


def test_switch_rollback_returns_422_and_keeps_original(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={'dataset': 'caliber'}).json()
    rid = run['manifest']['run_id']
    resp = c.post(f'/api/runs/{rid}/caliber', json={'caliber': 'force_new'})
    assert resp.status_code == 422
    assert resp.json()['code'] == 'CALIBER_SWITCH_FAILED'
    # 原批次结果未被改写
    stored = c.get(f'/api/runs/{rid}').json()
    assert stored['manifest']['sha256'] == run['manifest']['sha256']
    assert stored['caliber_decisions'] == run['caliber_decisions']
    assert len(c.get('/api/runs').json()) == 1  # 失败候选不落库，不产生新批次


def test_reviewed_run_caliber_locked(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={'dataset': 'caliber'}).json()
    rid = run['manifest']['run_id']
    assert c.post(f'/api/runs/{rid}/review', json={'revision': 1}).status_code == 200
    resp = c.post(f'/api/runs/{rid}/caliber', json={'caliber': 'force_new'})
    assert resp.status_code == 409 and resp.json()['code'] == 'CALIBER_LOCKED'
    # 已复核结果保持不变
    stored = c.get(f'/api/runs/{rid}').json()
    assert stored['status'] == '已复核'
    assert stored['manifest']['sha256'] == run['manifest']['sha256']


def test_switch_success_idempotent_no_double_accumulation(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={'dataset': 'clean'}).json()
    rid = run['manifest']['run_id']
    # clean 在新口径下托盘 10->12 不守恒，先演示失败回滚不落库
    fail = c.post(f'/api/runs/{rid}/caliber', json={'caliber': 'force_new'})
    assert fail.status_code == 422
    # 同批重复重跑不产生新批次、不累计差异
    again = c.post(f'/api/runs/{rid}/rerun')
    assert again.json()['manifest']['run_id'] == rid
    history = c.get('/api/runs').json()
    assert len(history) == 1 and history[0]['id'] == rid


def test_switch_endpoint_bad_policy(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={'dataset': 'caliber'}).json()
    resp = c.post(f"/api/runs/{run['manifest']['run_id']}/caliber", json={'caliber': 'x'})
    assert resp.status_code == 422 and resp.json()['code'] == 'BAD_CALIBER'


def test_switch_creates_distinct_idempotent_run(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    new_csv = (
        'id,parent,packages,package_unit,net,gross,weight_unit,hazard,un_code,container,revision,doc_date\n'
        'R,,48,carton,400,440,kg,none,,C,1,2025-12-31\n'
        'R-P1,R,24,carton,200,220,kg,none,,C,1,2025-12-31\n'
        'R-P1-1,R-P1,1,pallet,0.1,0.11,t,none,,C,1,2025-12-31\n'
        'R-P1-2,R-P1,1,pallet,0.1,0.11,t,none,,C,1,2025-12-31\n'
        'R-P2,R,24,carton,200,220,kg,none,,C,1,2025-12-31\n'
        'R-P2-1,R-P2,1,pallet,0.1,0.11,t,none,,C,1,2025-12-31\n'
        'R-P2-2,R-P2,1,pallet,0.1,0.11,t,none,,C,1,2025-12-31\n')
    ov = {'received_at': '2026-01-05', 'declaration': {'pieces': 48, 'net_kg': '400', 'gross_kg': '440'}}
    run = c.post('/api/runs', json={'csv_text': new_csv, 'overrides': ov}).json()
    rid = run['manifest']['run_id']
    switched = c.post(f'/api/runs/{rid}/caliber', json={'caliber': 'force_new'})
    assert switched.status_code == 200
    new_id = switched.json()['manifest']['run_id']
    assert new_id != rid  # 策略参与摘要
    assert switched.json()['input_snapshot']['bytes_b64'] == run['input_snapshot']['bytes_b64']
    # 重复切换复用同一批号，不新增批次、不重复累计
    again = c.post(f'/api/runs/{rid}/caliber', json={'caliber': 'force_new'})
    assert again.json()['manifest']['run_id'] == new_id
    assert sorted(h['id'] for h in c.get('/api/runs').json()) == sorted([rid, new_id])


def test_rerun_preserves_stored_policy(tmp_path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    run = c.post('/api/runs', json={'dataset': 'caliber'}).json()
    rerun = c.post(f"/api/runs/{run['manifest']['run_id']}/rerun").json()
    assert rerun['caliber_policy'] == 'auto'
    assert rerun['manifest']['run_id'] == run['manifest']['run_id']

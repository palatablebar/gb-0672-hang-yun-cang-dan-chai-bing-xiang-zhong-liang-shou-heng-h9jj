import io, csv, json, zipfile, hashlib
from app.errors import BusinessError

def serialize(result):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps({**result['manifest'],'revision':result.get('revision',1),'status':result.get('status','待复核')},ensure_ascii=False,indent=2))
        archive.writestr('evidence.json',json.dumps({'counts':result['counts'],'views':result['views'],'quarantine':result['quarantine']},ensure_ascii=False,indent=2))
        if 'caliber_decisions' in result:
            archive.writestr('caliber_evidence.json',json.dumps({
                'policy':result.get('caliber_policy','auto'),
                'effective_date':result['context'].get('effective_date'),
                'received_at':result['context'].get('received_at'),
                'decisions':result['caliber_decisions'],
                'excluded_roots':result['balance'].get('excluded_roots',[]),
                'switch_failed':result.get('caliber_switch_failed',False),
                'dual_caliber':result['views']['packaging'].get('dual_rows',[])},ensure_ascii=False,indent=2))
    return buffer.getvalue()

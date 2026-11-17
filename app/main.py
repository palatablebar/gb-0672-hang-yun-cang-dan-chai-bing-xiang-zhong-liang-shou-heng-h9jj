from contextlib import asynccontextmanager
from importlib import import_module
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, META, MODULES, DB_PATH
from app.errors import BusinessError
from app.engine import evaluate
from app import store
from app.archive import serialize

@asynccontextmanager
async def lifespan(app):
    store.save(DB_PATH,evaluate())
    yield
app=FastAPI(title=META['title'],lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(ROOT/'app/static')),name='static')
@app.exception_handler(BusinessError)
async def business_error(request:Request,error:BusinessError):
    return JSONResponse({'code':error.code,'detail':error.message},status_code=error.status)
@app.get('/')
def home():
    return RedirectResponse('/'+MODULES[0],status_code=303)
@app.get('/healthz')
def health():
    return {'status':'ok','project':META['short']}
@app.post('/api/runs')
def create(payload:dict=Body(default={})):
    return store.save(DB_PATH,evaluate(payload))
@app.post('/api/runs/{run_id}/rerun')
def rerun(run_id:str):
    previous=store.get(DB_PATH,run_id)
    if 'input_snapshot' not in previous:
        raise BusinessError('INPUT_SNAPSHOT_MISSING','旧批缺少源快照，请重新导入')
    return store.save(DB_PATH,evaluate({'dataset':previous['source']},snapshot=previous['input_snapshot']))
@app.post('/api/runs/{run_id}/caliber')
def switch_caliber(run_id:str,payload:dict=Body(default={})):
    previous=store.get(DB_PATH,run_id)
    # 已复核舱单不得被悄悄改写：复核锁优先于一切重算
    if previous.get('status')=='已复核':
        raise BusinessError('CALIBER_LOCKED','批次已复核，口径锁定；如需切换请先创建新批次',409)
    if 'input_snapshot' not in previous:
        raise BusinessError('INPUT_SNAPSHOT_MISSING','旧批缺少源快照，请重新导入')
    policy=payload.get('caliber','force_new')
    if policy not in {'auto','force_new'}:
        raise BusinessError('BAD_CALIBER','口径策略无效')
    candidate=evaluate({'dataset':previous['source'],'caliber':policy},snapshot=previous['input_snapshot'])
    # 切换失败：返回原批次结果与原值，不落库任何候选结果（恢复原有结果并保留原值）
    if candidate.get('caliber_switch_failed'):
        failed=[d for d in candidate['caliber_decisions'] if not d['resolved']]
        raise BusinessError('CALIBER_SWITCH_FAILED',
                            '口径切换失败，已回滚保留原值：'+ '；'.join(d['basis'] for d in failed),422)
    return store.save(DB_PATH,candidate)
@app.get('/api/runs')
def runs():
    return store.history(DB_PATH)
@app.get('/api/runs/{run_id}')
def run(run_id:str):
    return store.get(DB_PATH,run_id)
@app.post('/api/runs/{run_id}/review')
def review(run_id:str,payload:dict=Body(...)):
    revision=payload.get('revision')
    if not isinstance(revision,int) or revision<1: raise BusinessError('BAD_REVISION','版本号须为正整数')
    return store.review(DB_PATH,run_id,revision)
@app.get('/api/export/{run_id}')
def export(run_id:str):
    result=store.get(DB_PATH,run_id)
    return Response(serialize(result),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{run_id}.zip"'})
for key in MODULES:
    app.include_router(import_module(f'app.modules.{key}.handler').router)

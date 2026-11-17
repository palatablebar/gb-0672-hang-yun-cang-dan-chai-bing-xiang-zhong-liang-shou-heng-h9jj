from .reader import read_source
FIELDS=['id', 'parent', 'packages', 'package_unit', 'net', 'gross', 'weight_unit', 'hazard', 'un_code', 'container', 'revision', 'doc_date']
def apply(state):read_source(state,FIELDS,{'编号':'id','单据日期':'doc_date'})

import hashlib,json
from decimal import Decimal
from app.errors import DomainError
from app.reasons import sources
def evaluate(rows,context):
    stages=[v for v in context.values() if isinstance(v,dict) and "metrics" in v]
    balances=[s["metrics"]["balanced"] for s in stages if "balanced" in s["metrics"]]
    if balances and not all(balances): raise DomainError("UNBALANCED","来源行未对平，不能归档")
    ids=[str(r.get("parcel",r.get("_line",""))) for r in rows]
    if len(ids)!=len(set(ids)): raise DomainError("ARCHIVE_DUPLICATE","归档业务键重复")
    encoded=json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()
    # 原始扫描证据（_raw 原始单元格、_sources 来源行号）随归档逐行保留，任何阶段不删除、不改写
    missing_raw=[r.get("parcel",r.get("_line")) for r in rows if not r.get("_raw")]
    if missing_raw: raise DomainError("RAW_EVIDENCE_LOST","原始扫描证据缺失，不能归档")
    timeliness=context.get("timeliness") or {}
    policy=timeliness.get("clock_policy") or {}
    return {"rows":rows,"quarantine":[],"clock_policy":timeliness.get("clock_policy"),
            "metrics":{"records":len(rows),"source_lines":len(set(x for row in rows for x in sources(row))),"sha256":hashlib.sha256(encoded).hexdigest(),"ready":True,"differences":sum(r.get("status")=="差异" for r in rows),"clock_policy":policy.get("code",""),"raw_evidence_retained":True}}

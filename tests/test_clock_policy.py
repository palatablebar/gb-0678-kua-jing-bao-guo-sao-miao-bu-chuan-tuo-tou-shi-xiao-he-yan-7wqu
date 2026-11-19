import csv,io,json
from decimal import Decimal
import pytest
from app.pipeline import calculate,execute,load_sample
from app.modules.m5_timeliness.rules import evaluate,clock_segments,CLOCK_POLICY
from app.values import instant
from app import store

def row(accepted="2026-11-02T00:00:00Z",delivered="2026-11-05T00:00:00Z",destination="JP",zone="Asia/Tokyo",promised="100"):
    return {"parcel":"X1","accepted_at":accepted,"delivered_at":delivered,"destination":destination,
            "zone":zone,"promised_hours":promised,"declared_value":"200","reported":"0","_sources":[1],"_raw":["r"]}

# --- 裁决口径：仅目的地工作日停表，作业地周末不扣除 ---

def test_policy_is_destination_working_calendar_only():
    r=evaluate([row()],{"parameters":{}})
    assert r["clock_policy"]["code"]=="DESTINATION_WORKING_CALENDAR"
    text="".join(r["rows"][0]["clock_basis"])
    assert "目的地" in text and "不扣除" in text and "海外作业地周末扣除" in text and "不予采纳" in text

def test_origin_weekend_still_counts_when_destination_is_working():
    # 起运地与目的地均为工作日的整段（11-09 周一 至 11-12 周四，东京），不跨假日 -> 全额72h
    r=evaluate([row(accepted="2026-11-08T15:00:00Z",delivered="2026-11-11T15:00:00Z",promised="100")],{"parameters":{}})
    assert r["rows"][0]["working_hours"]=="72.0"
    assert all(s["kind"]=="working" for s in r["rows"][0]["clock_segments"])

def test_destination_weekend_is_stopped_but_not_origin_calendar():
    # 2026-11-06(周五) 至 11-09(周一) UTC，东京周末 11-07/08 停表，仅周五+周一两段计时
    r=evaluate([row(accepted="2026-11-06T00:00:00Z",delivered="2026-11-09T00:00:00Z")],{"parameters":{}})
    kinds=[s["kind"] for s in r["rows"][0]["clock_segments"]]
    assert kinds==["working","weekend","weekend","working"]
    # 东京：周五15h + 周一0至9点9h = 24h；周末48h全部停表
    assert r["rows"][0]["working_hours"]=="24.0"

def test_segments_have_explicit_basis_and_hours_tie_out():
    r=evaluate([row(accepted="2026-11-06T00:00:00Z",delivered="2026-11-09T00:00:00Z")],{"parameters":{}})
    segs=r["rows"][0]["clock_segments"]
    counted=sum(Decimal(s["counted_hours"]) for s in segs)
    deducted=sum(Decimal(s["deducted_hours"]) for s in segs)
    elapsed=sum(Decimal(s["elapsed_hours"]) for s in segs)
    assert counted+deducted==elapsed and counted==Decimal(r["rows"][0]["working_hours"])
    assert all(s["reason"] for s in segs)
    assert any("周末停业，停表" in s["reason"] for s in segs)

def test_holiday_segment_marked_with_contract_basis():
    segs=clock_segments(instant("2026-11-02T15:00:00Z"),instant("2026-11-03T15:00:00Z"),"Asia/Tokyo",{"2026-11-03"})
    holiday=[s for s in segs if s["kind"]=="holiday"][0]
    assert holiday["counted_hours"]=="0" and "法定节假日，合同免责停表" in holiday["reason"]

def test_carrier_origin_deduction_is_not_offered():
    # 规则输出中不存在任何按作业地/起运地停表的开关或字段，口径唯一
    r=evaluate([row()],{"parameters":{}})
    keys=set(r["rows"][0].keys())
    assert not any("origin" in k or "carrier" in k for k in keys)
    assert r["metrics"]["clock_policy"]=="DESTINATION_WORKING_CALENDAR"

# --- 补传不改变结论：以事件时刻而非上传时刻计时 ---

def test_late_reupload_keeps_event_time_conclusion(result):
    m5=result["stages"][4]
    # dirty 样例妥投事件 11-04、上传 11-06；若按上传计时会超时，按事件计时全部达标
    assert m5["metrics"]["late_parcels"]==0 and m5["metrics"]["compensation"]=="0.00"
    assert all(r["clock_end"]==r["delivered_at"] for r in m5["rows"])

# --- 赔付与证据下载同源 ---

def test_compensation_metrics_inherit_clock_policy(result):
    m6=result["stages"][5]
    assert m6["metrics"]["clock_policy"]=="DESTINATION_WORKING_CALENDAR"
    m5_total=result["stages"][4]["metrics"]["compensation"]
    assert m6["metrics"]["expected_total"]==m5_total

def test_archive_download_matches_stored_compensation(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    stored=client.get("/api/runs/"+p["run_id"]).get_json()
    r=client.get("/api/runs/"+p["run_id"]+"/archive")
    records=list(csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
    accepted=[x for x in records if x["kind"]=="accepted"]
    downloaded_total=sum(Decimal(json.loads(x["evidence"])["expected"]) for x in accepted)
    assert downloaded_total==Decimal(stored["stages"][5]["metrics"]["expected_total"])
    # 证据行携带逐段计时与依据，与页面同源
    first=json.loads(accepted[0]["evidence"])
    assert first["clock_policy"]=="DESTINATION_WORKING_CALENDAR" and first["clock_segments"] and first["clock_basis"]

# --- 重试同一补传不新增计赔（幂等） ---

def test_repeat_same_upload_does_not_add_compensation(sample):
    a=execute(sample);b=execute(sample)
    assert a["run_id"]==b["run_id"] and len(store.history())==1
    assert a["stages"][5]["metrics"]["expected_total"]==b["stages"][5]["metrics"]["expected_total"]

# --- 切换失败保留上一可用结论 ---

def test_failed_switch_keeps_previous_run(client):
    ok=client.post("/api/runs",json={"sample":"dirty.csv"})
    before=client.get("/api/runs").get_json()["runs"]
    bad=client.post("/api/runs",json={"csv":"scan_id,bad\n1"})
    assert bad.status_code==422
    after=client.get("/api/runs").get_json()["runs"]
    assert after==before  # 失败批次不落库，上一可用结论原样保留

# --- 原始扫描证据不能被误删 ---

def test_raw_evidence_retained_through_all_stages(result):
    for idx in (0,3,4,5,6):
        assert all(r.get("_raw") for r in result["stages"][idx]["rows"])
    assert result["stages"][6]["metrics"]["raw_evidence_retained"] is True

def test_archive_blocks_when_raw_evidence_lost():
    from app.modules.m7_archives.rules import evaluate as archive
    from app.errors import DomainError
    with pytest.raises(DomainError) as e:
        archive([{"parcel":"1"}],{})
    assert e.value.code=="RAW_EVIDENCE_LOST"

def test_archive_csv_keeps_raw_cells(client):
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    r=client.get("/api/runs/"+p["run_id"]+"/archive")
    records=list(csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
    evidence=[json.loads(x["evidence"]) for x in records if x["kind"]=="accepted"]
    assert all(isinstance(e.get("_raw"),list) and e.get("_sources") for e in evidence)

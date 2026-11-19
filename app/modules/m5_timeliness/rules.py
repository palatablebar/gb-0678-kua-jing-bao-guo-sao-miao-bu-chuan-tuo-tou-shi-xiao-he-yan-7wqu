from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
from decimal import Decimal
from app.values import instant,number,money
from app.errors import DomainError
HOLIDAYS={"JP":{"2026-11-03","2026-11-23"},"US":{"2026-11-26"},"DE":{"2026-10-03"}}
# 正式停表口径（合同口径，TRAP-27 裁决）：
# 承诺时钟只按目的地工作日计时；目的地周末与法定节假日停表。
# 起运地及海外作业地的周末/停工不属于合同免责范围，一律不扣除、计入承诺时长；
# 承运商主张的“海外作业地周末扣除”不予采纳。
CLOCK_POLICY={
    "code":"DESTINATION_WORKING_CALENDAR",
    "name":"目的地工作日停表口径（合同口径）",
    "rule":"承诺时钟仅按目的地当地工作日小时计时；目的地周末与法定节假日停表扣除。",
    "excluded":"起运地/海外作业地周末、节假日及当地停工均不扣除，照常计入承诺时长；承运商主张的海外作业地周末扣除无合同依据，不予采纳。",
    "upload_rule":"补传仅按扫描事件发生时刻重建路径，迟到的上传时刻不参与计时，补传本身不改变时效结论。",
    "basis":"服务合同仅将目的地停业列为免责事项，未授权按作业地日历二次停表。",
}
WEEKDAY_CN=["周一","周二","周三","周四","周五","周六","周日"]
def working_hours(start,end,zone,holidays):
    if end<start:raise DomainError("NEGATIVE_INTERVAL","妥投早于收件")
    tz=ZoneInfo(zone);cursor=start;seconds=Decimal(0)
    while cursor<end:
        local=cursor.astimezone(tz);next_day=datetime.combine(local.date()+timedelta(days=1),time.min,tzinfo=tz).astimezone(cursor.tzinfo)
        stop=min(end,next_day)
        if local.weekday()<5 and local.date().isoformat() not in holidays:seconds+=Decimal(str((stop-cursor).total_seconds()))
        cursor=stop
    return seconds/3600
def clock_segments(start,end,zone,holidays):
    """按目的地当地自然日半开切段，逐段给出有效计时与停表依据，区间统一[start,end)。"""
    if end<start:raise DomainError("NEGATIVE_INTERVAL","妥投早于收件")
    tz=ZoneInfo(zone);cursor=start;segments=[]
    while cursor<end:
        local=cursor.astimezone(tz);next_day=datetime.combine(local.date()+timedelta(days=1),time.min,tzinfo=tz).astimezone(cursor.tzinfo)
        stop=min(end,next_day);day=local.date().isoformat();weekday=local.weekday()
        elapsed=Decimal(str((stop-cursor).total_seconds()))/Decimal(3600)
        is_holiday=day in holidays
        if is_holiday:
            counted=Decimal(0);deducted=elapsed;kind="holiday";reason=f"目的地{day}（{WEEKDAY_CN[weekday]}）法定节假日，合同免责停表"
        elif weekday>=5:
            counted=Decimal(0);deducted=elapsed;kind="weekend";reason=f"目的地{day}（{WEEKDAY_CN[weekday]}）周末停业，停表"
        else:
            counted=elapsed;deducted=Decimal(0);kind="working";reason=f"目的地{day}（{WEEKDAY_CN[weekday]}）工作日，全额计入承诺时长"
        segments.append({"date":day,"weekday":WEEKDAY_CN[weekday],"kind":kind,
                         "start_utc":cursor.isoformat(),"end_utc":stop.isoformat(),
                         "elapsed_hours":str(elapsed),"counted_hours":str(counted),
                         "deducted_hours":str(deducted),"reason":reason})
        cursor=stop
    return segments
def _basis_text(row,segments,clock_end):
    counted=sum((Decimal(s["counted_hours"]) for s in segments),Decimal(0))
    deducted=sum((Decimal(s["deducted_hours"]) for s in segments),Decimal(0))
    stop_days="、".join(f"{s['date']}{s['weekday']}" for s in segments if s["kind"]!="working") or "无"
    lines=[
        f"口径：{CLOCK_POLICY['name']}，时区{row['zone']}（目的地{row['destination']}）。",
        f"计时区间：{row['accepted_at']} 至 {clock_end.isoformat()}，按当地自然日半开切段共{len(segments)}段。",
        f"有效计时 {counted} 小时；停表扣除 {deducted} 小时，停表日：{stop_days}（仅目的地周末/节假日）。",
        CLOCK_POLICY["excluded"],
        CLOCK_POLICY["upload_rule"],
    ]
    return lines
def evaluate(rows,context):
    p=context.get("parameters",{});as_of=instant(p.get("as_of","2026-11-18T00:00:00Z"));rate=number(p.get("hourly_compensation","1.00"))
    if rate<0:raise DomainError("BAD_RATE","赔付费率不能为负")
    good=[]
    for row in rows:
        start=instant(row["accepted_at"]);end=instant(row["delivered_at"]) if row.get("delivered_at") else max(start,as_of)
        holidays=set(p.get("holidays",{}).get(row["destination"],HOLIDAYS.get(row["destination"],set())))
        hours=working_hours(start,end,row["zone"],holidays)
        segments=clock_segments(start,end,row["zone"],holidays)
        # 逐段有效计时之和必须与整段口径一致，同源数据不得出现两套数字
        segment_total=sum((Decimal(s["counted_hours"]) for s in segments),Decimal(0))
        if segment_total!=hours:raise DomainError("CLOCK_MISMATCH","分段计时与整段计时不一致")
        late=max(Decimal(0),hours-number(row["promised_hours"]));comp=min(number(row["declared_value"])*Decimal("0.10"),late*rate)
        good.append({**row,"working_hours":str(hours),"late_hours":str(late),"expected":money(comp),
                     "sla":"超时" if late>0 else "达标","clock_end":end.isoformat(),
                     "clock_policy":CLOCK_POLICY["code"],
                     "clock_segments":segments,"clock_basis":_basis_text(row,segments,end)})
    return {"rows":good,"quarantine":[],"clock_policy":CLOCK_POLICY,
            "metrics":{"parcels":len(good),"late_parcels":sum(r["sla"]=="超时" for r in good),
                       "compensation":money(sum((number(r["expected"]) for r in good),Decimal(0))),
                       "clock_policy":CLOCK_POLICY["code"]}}

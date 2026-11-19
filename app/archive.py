import csv,io,json
def build_archive(run):
    stream=io.StringIO(newline="");writer=csv.writer(stream)
    writer.writerow(["run_id","input_sha256","rule_version","clock_policy","kind","source_lines","reason","evidence"])
    meta=run["manifest"]
    policy=run["stages"][4].get("clock_policy",{}).get("code","")
    for row in run["stages"][-1]["rows"]:
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],policy,"accepted",json.dumps(row.get("_sources",[]),ensure_ascii=False),row.get("status",""),json.dumps(row,ensure_ascii=False)])
    for row in run["stages"][-1].get("cumulative_quarantine",[]):
        writer.writerow([run["run_id"],meta["input_sha256"],meta["rule_version"],policy,"quarantine",json.dumps(row.get("_sources",[]),ensure_ascii=False),row.get("reason",""),json.dumps(row,ensure_ascii=False)])
    return "﻿"+stream.getvalue()

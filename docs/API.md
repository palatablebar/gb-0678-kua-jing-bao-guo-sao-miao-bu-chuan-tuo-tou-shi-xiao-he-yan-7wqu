# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 停表口径（TRAP-27 裁决）

正式时效唯一口径为 `DESTINATION_WORKING_CALENDAR`：承诺时钟只按目的地当地工作日计时，目的地周末与法定节假日停表；起运地及海外作业地停工不扣除。M5 每行附 `clock_segments`（当地自然日半开切段：类型 working/weekend/holiday、计入小时、停表小时、逐条依据）与 `clock_basis`（口径说明），阶段级 `clock_policy` 与 metrics 同步输出口径代码。M6 赔付、M7 归档与 `/api/runs/<id>/archive` 证据 CSV（新增 clock_policy 列）均沿用同一口径、同一封存金额，无第二套计赔数字。补传按事件发生时刻重建，不按上传时刻计时；同源重跑返回同一 run_id（INSERT OR IGNORE），不新增计赔。计算或导入失败返回 4xx 且不落库，历史批次与页面上一可用结论保持不变。归档行必须保留 `_raw` 原始单元格与 `_sources` 来源行号，缺失即 RAW_EVIDENCE_LOST 拒绝。

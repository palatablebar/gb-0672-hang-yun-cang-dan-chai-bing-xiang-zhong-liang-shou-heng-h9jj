# 航运舱单拆并箱与重量守恒

给货代单证员使用。主提单与分提单拆并形成有向无环关系，件数和净毛重逐层守恒。

七个业务模块：舱单采信、包装映射、拆并编组、危险品检查、净毛重核验、报关对账、版本封存。

```sh
docker compose up --build -d
docker compose exec -T app pytest -q
```

访问 http://localhost:18241 。启动直接载入现场脏批。切换干净样例、旧编码样例和跨档双口径样例可对照核验。干净批42个节点、6个主单，件数240、净重2400kg、毛重2640kg；现场脏批包含父级缺失、依赖环、危险品不相容及1kg毛重差异，封存状态为待处理。跨档批（caliber）含历史托盘、非标准单位（roll/bundle/托）、旧口径可解释沿用、新口径可解释改用、两口径均不可解释被排除四类裁决场景。

各模块页面对应 `/<模块英文名>`，各模块执行入口为 `POST /api/<模块英文名>`，不传参即可独立演示。完整运行 `POST /api/runs` 支持 `dataset`（dirty/clean/legacy/caliber）、`csv_text`、`caliber`（auto/force_new）与 `overrides` 参数。批次历史 `GET /api/runs`，详情 `GET /api/runs/{run_id}`。

同输入与规则版本生成相同批号，只保留一条批次。复核入口 `POST /api/runs/{run_id}/review` 提交当前 `revision`，陈旧提交409。口径切换 `POST /api/runs/{run_id}/caliber`：已复核批次409锁定；force_new 无法按新口径解释时422回滚，候选不落库、原值保留。归档包只有一个下载入口 `GET /api/export/{run_id}`，含证据、运行摘要与 `caliber_evidence.json` 双口径裁决证据。原始隔离在页面同屏显示。

SQLite使用命名卷，容器重启后保留历史。要重建演示可在Compose中改用新的卷名称，保留旧卷。

源码在 `app/`，样例在 `sample_data/`，测试在 `tests/`；定义见 `docs/DATA-DICTIONARY.md`，模块和边界测试对应关系见 `docs/DEPTH.md`。输入上限2MB，属于离线复核工具。

采信页支持上传或粘贴CSV（末列须含单据日期 `doc_date`）。完整源字节与参数随批次持久化，跨页通过run_id读取同一结果。`POST /api/runs/{run_id}/rerun` 读取该批不可变输入快照重新计算，规则未变时批号不变，重跑不重复累计差异。切换另一示例则创建或复用另一批次。

## 新旧包装口径裁决

包装换算规则按 `effective_date`（2026-01-01）分新旧两套：旧口径 pallet=10 件并接受历史 托/roll/bundle 等非标准单位；新口径 pallet=12 件且不再接受非标准单位。单据日期与接收日期跨档时，按提单整树裁决——单据日期旧口径能让原值与逐层申报对平就沿用旧口径；否则接收日期新口径能对平就改用新口径；两套都解释不了的提单（如 caliber 批的 M104）标记未裁决，不进入申报汇总。包装页同屏展示每行件数、净重、毛重在两种口径下的结果与采用依据，采用值驱动后续逐层重量核对。

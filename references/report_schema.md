# 药师帮采购比价报表 · 字段 → HTML 元素映射表

> 配套文档：《药师帮采购比价报表·结构化建议书》
> 用途：把建议书 A/B/C 三节的每一个字段，钉死到最终 HTML 报表的具体元素（id / class / 列位置），供 `process.py` 重构时逐条对照。
> 数据完整性约定：**真实数据**=detail_data.json 中该 wholesaleid 有非空 `paid_units`；否则该行所有"详情页"字段渲染为 `待补采`，并在行尾标 `data-status="pending"`。

---

## 0. 数据源 → 字段 来源对照

| 数据源 | 关键字段 | 对应建议书层级 |
|--------|----------|----------------|
| `vuex_raw.json`（900 行列表） | `drugname`, `specification`, `minamount`, `unit`, `priceToken`, `provider_name`, `wholesaleid`, `domText` | 报价级·列表侧 |
| `detail_data.json`（按 wholesaleid 索引） | `detail_price`, `paid_units`, `paid_unit`, `stores_joined`, `purchase_records`, `expiry_date`, `produce_date`, `manufacturer`, `approval_no`, `min_qty`, `min_unit`, `recent_purchases`, `last_7_days_sales`, `large_orders_count`, `large_orders_total_qty` | 报价级·详情页（权威） |

- `priceToken` 为加密价，需解码；解码值须与 `detail_data.detail_price` **互验**（不一致时以详情页为准，列表值仅作兜底）。
- `domText`（"已拼 XXX"）仅作参考展示，**不参与任何排名/统计**。

---

## 1. 报价级字段（wholesaleid，档位页每行的数据底座）

档位页表格列顺序固定如下，列头 `th` 带 `data-field` 属性便于核对：

| # | 列头 | 字段 | 取值来源 | HTML 元素 |
|---|------|------|----------|-----------|
| 1 | 报价链接 | `wholesaleid` | vuex_raw | `<a class="offer-link" href="https://dian.ysbang.cn/#/drugInfo?wholesaleid={wid}&isAssemble={isAssemble}&scene={scene}&trafficType=1" target="_blank">{wid}</a>` — 拼团商品(包邮)用 `isAssemble=true&scene=0`，普通商品(起购)用 `isAssemble=false&scene=1`，由 `process.py` 的 `is_group_buy_name()` 按商品名自动选择 |
| 2 | 成团价 | `price` | `priceToken` 解码，与 `detail_price` 互验 | `<td class="col-price" data-field="price">{价}{unit}</td>` |
| 3 | 起拼量+单位 | `minamount`+`unit` | vuex_raw（兜底 `min_qty`+`min_unit`） | `<td class="col-moq">{minamount} {unit}</td>` |
| 4 | 供应商 | `provider_name` | vuex_raw | `<td class="col-supplier">{provider_name}</td>` |
| 5 | 有效期至 | `expiry_date` | detail_data | `<td class="col-exp">{expiry_date 或 待补采}</td>` |
| 6 | **已付款件数** | `paid_units` | **detail_data（权威主指标，加粗）** | `<td class="col-paid paid-main" data-field="paid_units"><b>{paid_units}{paid_unit}</b></td>` |
| 7 | 参团店铺数 | `stores_joined` | detail_data | `<td class="col-stores">{stores_joined}</td>` |
| 8 | 采购笔数 | `purchase_records` | detail_data | `<td class="col-records">{purchase_records}</td>` |
| 9 | 档位+销量标签 | tier + label | 见 §3 计算 | `<td class="col-tier"><span class="tier {tier_class}">{档位}</span> <span class="label {label_class}">{标签}</span></td>` |

> 行属性：`<tr class="offer-row" data-wid="{wid}" data-status="{real|pending}">`。`pending` 行整体置灰，且第 5–8 列显示 `待补采`。

---

## 2. 产品级字段（商品名+规格归并，首页 264 行）

归并主键：`(drugname, specification)` 去重 → 约 264 个产品。

| # | 列头 | 字段 | 计算口径 | HTML 元素 |
|---|------|------|----------|-----------|
| 1 | 商品名+规格 | product | `(drugname) (specification)` | `<td class="col-prod"><a href="#tier-{pid}">{drugname} {specification}</a></td>` |
| 2 | 销量排名 | `rank` | 按代表报价 `paid_units` 降序后赋序号 | `<td class="col-rank" data-field="rank">#{rank}</td>` |
| 3 | 均价 | `avg_price` | 该产品所有报价 `price` 的算术均值 | `<td class="col-avg">{avg_price}</td>` |
| 4 | 供应商数 | `supplier_cnt` | 该产品 `provider_name` 去重计数 | `<td class="col-sup-num">{supplier_cnt}</td>` |
| 5 | 全网最低价 | `min_price` | 该产品所有报价 `price` 的最小值 | `<td class="col-min">{min_price}</td>` |
| 6 | 有效期缩略 | `exp_short` | 代表报价（见下）的 `expiry_date` 取 `YYYY-MM` | `<td class="col-exp-short">{exp_short}</td>` |

**代表报价 best 规则**：该产品下 `paid_units` 最高者；其 `paid_units` 即首页"销量排名"的排序值；产品级**只取这一条**，杜绝错配。

---

## 3. 档位（Tier）与销量标签 → HTML

档位页顶部"销量分布"小结 + 每行标签：

| 元素 | 内容 | HTML |
|------|------|------|
| 销量分布小结（档位页顶部） | 最高 paid_units / 平均 paid_units / 店铺总数 | `<div class="tier-summary">最高 <b>{max_paid}</b> · 平均 <b>{avg_paid}</b> · 参团店铺 {total_stores}</div>` |
| 档位计算 | 该产品 10 个报价按 `price` 升序，取 Q1/Q3 分位：`价≤Q1`→源头直供档；`Q1<价≤Q3`→主流走量档；`价>Q3`→精选优价档 | `tier_class` ∈ {`tier-source`, `tier-main`, `tier-premium`} |
| 销量标签 | 按 `paid_units` 与 `join_shops`（=stores_joined）：头部→`爆款领跑`；中位→`稳健供货`；尾部→`长尾备选` | `label_class` ∈ {`lb-hot`, `lb-steady`, `lb-tail`} |

> 档内按价升序排列；命名传达采购决策含义，**禁用序数词**（不用"第一档/第二档"）。

---

## 4. 详情页专属字段 → 折叠区

| 字段 | 来源 | HTML |
|------|------|------|
| 最近采购明细（采购时序） | `recent_purchases[]` | `<details class="purchase-history"><summary>采购时序</summary><ul>{buyer / time / qty}</ul></details>` |
| 生产日期 | `produce_date` | 折叠区内一行 |
| 厂家 | `manufacturer` | 折叠区内一行 |
| 批准文号 | `approval_no` | 折叠区内一行 |
| 近7天销量 / 大单≥50(笔) / 大单总销量 | `last_7_days_sales` / `large_orders_count` / `large_orders_total_qty` | 折叠区内一行（与建议书校正一致：这些进详情，不进首页） |

---

## 5. 待补采（pending）渲染约定

- 触发：`detail_data[wid].paid_units` 为空 / None。
- 档位页该行：第 5–8 列显示 `待补采`，`<tr data-status="pending">` 置灰。
- 首页该产品：若其 `best` 报价为 pending，则 `销量排名` 列显示 `—`，不参与排名（整体后移）。
- 顶部加一行状态条：`<div class="data-status-bar">已采集 {N}/900 真实销量 · 剩余 {M} 条待补采</div>`。

---

## 6. process.py 重构落点速查

| 建议书节 | 改造点 | 改动位置（process.py） |
|----------|--------|------------------------|
| A 报价级 | `build_offers()` 改为读取 `detail_data[wid]`，缺则 pending | 现有 `build_offers` 函数 |
| A 产品级 | 新增 `product_summary()`：按 (drugname,specification) 归并，取 best=max(paid_units) | 新增函数 |
| B 档位 | 新增 `assign_tier()`：Q1/Q3 分位 + 标签 | 新增函数 |
| C 首页 | 渲染 264 行，列顺序按 §2 | `renderHome()` |
| C 档位页 | 渲染每产品 10 报价，列顺序按 §1；顶部加 §3 小结 | `renderDetail()` |
| 完整性 | 顶部状态条 + pending 置灰 | `renderShell()` |

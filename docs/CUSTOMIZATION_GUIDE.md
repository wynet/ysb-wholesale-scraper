# Skill 自定义使用指南

> 本文档说明如何在对话过程中随时修改详情页采集字段、报表列、筛选规则等，AI agent 会直接修改脚本代码并重新运行。

---

## 一、核心概念：字段是怎么流转的

详情页数据经过 3 个脚本层层传递，理解这条数据链是自定义的基础：

```
详情页文本 (document.body.innerText)
    ↓
cdp_fetch_detail_v2.py → parse_detail(txt)
    ↓ 正则提取，写入 detail_data.json
    ↓
process.py → build_offers(recs)
    ↓ 读取 detail_data.json，映射到输出结构
    ↓
Excel 三表 + HTML 报表
```

| 阶段 | 脚本 | 做什么 | 你需要关心什么 |
|------|------|--------|---------------|
| 采集 | `cdp_fetch_detail_v2.py` | 打开详情页，提取页面文本，用正则抽取字段 | `parse_detail()` 函数里的正则 |
| 中转 | `detail_data.json` | 按商品 ID 索引存储所有详情字段 | 字段名（key） |
| 出表 | `process.py` | 读取 detail_data.json，映射到 Excel/HTML | `build_offers()` 里的字段传递 + 表头/列定义 |

**添加一个新字段的完整路径**：

```
1. parse_detail() 加一行正则  →  采集时自动提取
2. build_offers() 加一行映射  →  传递到输出结构
3. Excel/HTML 加一列表头     →  报表中展示
```

AI agent 在对话中收到你的需求后，会自动完成这 3 步修改并重新运行。

---

## 二、当前已支持的详情页字段

### 2.1 采集层（`parse_detail()` 已实现）

| 字段名 (JSON key) | 页面文本来源 | 正则模式 | 数据类型 |
|-------------------|-------------|----------|----------|
| `detail_price` | 成团价¥X / 采购价X | `成团价\s*¥\s*([\d.]+)` / `采购价\s*\n?\s*([\d.]+)` | float |
| `discount_price` | 折后约¥X | `折后约\s*¥?\s*([\d.]+)` | float |
| `stores_joined` | N店参团 | `(\d+)店参团` | int |
| `paid_units` + `paid_unit` | N盒已付款 | `([\d.]+万?)\s*([盒瓶支包袋片套罐条贴副双个])\s*已付款` | int + str |
| `purchase_records` | 采购记录（N笔） | `采购记录\s*[（(]\s*(\d+)\s*笔\s*[）)]` | int |
| `total_purchased` + `total_purchased_unit` | 累计已购买N盒 | `累计已购买\s*(\d+)\s*([盒瓶支包袋片套罐条贴副双个])` | int + str |
| `expiry_date` | 有效期至：YYYY-MM-DD | `有效期至[：:]\s*([\d-]+)` | str |
| `produce_date` | 生产日期：YYYY-MM-DD | `生产日期[：:]\s*([\d-]+)` | str |
| `manufacturer` | 生产厂家：XXX | `生产厂家[：:]\s*([^\n]+)` | str |
| `approval_no` | 批准文号：XXX | `批准文号[：:]\s*([^\n]+)` | str |
| `min_qty` + `min_unit` | 已成团N盒起拼 | `已成团[/\s]*(\d+)\s*([盒瓶支包袋片套罐条贴副双个])起拼` | int + str |
| `recent_purchases` | 采购明细列表 | 复合正则（买家/手机/数量/时间） | list[dict] |
| `last_7_days_sales` | 从采购记录计算 | 按时间戳筛选最近7天 | int |
| `large_orders_count` | 从采购记录计算 | 单笔≥50的订单笔数 | int |
| `large_orders_total_qty` | 从采购记录计算 | 单笔≥50的订单总销量 | int |

### 2.2 出表层（`process.py` 输出到报表）

| 报表位置 | 当前展示的详情字段 |
|----------|-------------------|
| Excel Sheet1 (热销产品) | 有效期示例 |
| Excel Sheet2 (TOP10档位) | 有效期、该价销量 |
| Excel Sheet3 (产品明细) | 有效期 |
| HTML 首页 | 有效期 |
| HTML 档位页 | 已付款(权威)、参团店、采购笔、有效期、阶梯价、近7天、大单数、大单总销量、生产厂家、批准文号、生产日期、最近采购明细 |

> **注意**：`build_offers()` 已传递 `manufacturer`、`approval_no`、`produce_date` 到 HTML 档位页，但 Excel 三表目前未展示这些字段。如需在 Excel 中也展示，只需加列。

---

## 三、如何添加新字段（对话示例）

### 场景 1：添加「医保代码」和「医保类型」

**你只需要说**：

> "详情页里帮我加抓医保代码和医保类型，报表里也展示出来"

**AI agent 会自动执行**：

**步骤 1** — 在 `cdp_fetch_detail_v2.py` 的 `parse_detail()` 中添加正则：

```python
# 假设详情页文本格式为 "医保代码：H12345678" 和 "医保类型：甲类"
m = re.search(r'医保代码[：:]\s*([A-Z0-9]+)', txt)
d['insurance_code'] = m.group(1) if m else None

m = re.search(r'医保类型[：:]\s*([甲乙丙类]+类)', txt)
d['insurance_type'] = m.group(1) if m else None
```

**步骤 2** — 在 `process.py` 的 `build_offers()` 中添加映射：

```python
"insuranceCode": det.get("insurance_code"),
"insuranceType": det.get("insurance_type"),
```

**步骤 3** — 在 HTML 档位页表头和数据行中添加列，或在 Excel 中添加列。

**步骤 4** — 重新运行采集 + 出表，新字段自动出现在报表中。

> **关键点**：正则的写法取决于详情页的实际文本格式。AI agent 会先打开一个详情页查看文本结构，再编写准确的正则。你只需描述需求，不需要自己写正则。

### 场景 2：修改大单统计的阈值

**你说**：

> "大单统计的阈值改成 100，不要 50"

**AI 修改** `parse_detail()` 中的阈值：

```python
# 原: if q >= 50
if q >= 100: large_orders.append(q)
```

### 场景 3：只采部分字段（加快速度）

**你说**：

> "我只需要价格和销量，其他字段不用抓，加快速度"

**AI 精简** `parse_detail()`，注释掉不需要的正则（采购记录解析最耗时，去掉可提速 30-50%）。

### 场景 4：增加 Excel 列

**你说**：

> "Excel 热销产品表里帮我加上生产厂家和批准文号两列"

**AI 修改** `process.py` 中 Sheet1 的表头和数据行：

```python
# 表头加入
hdr1 = [..., "商品ID", "生产厂家", "批准文号"]

# 数据行加入
ws.cell(row=r, column=12, value=detail.get("manufacturer", ""))
ws.cell(row=r, column=13, value=detail.get("approval_no", ""))
```

---

## 四、详情页文本结构参考

AI agent 修改正则前，会先通过 CDP 获取详情页的 `document.body.innerText` 来确认文本格式。以下是常见的文本模式：

### 拼团商品详情页（`isAssemble=true, scene=0`）

```
乐药师 藿香正气口服液
10支/盒
已成团 10盒起拼
成团价 ¥9.48
折后约 ¥8.80
23店参团
1280盒已付款
采购记录（45笔）
有效期至：2027-06-30
生产日期：2026-07-15
生产厂家：XX药业有限公司
批准文号：国药准字Z53020023
...
采购明细：
张** 138****1234 5盒 3天前
李** 139****5678 10盒 5小时前
```

### 普通商品详情页（`isAssemble=false, scene=1`）

```
XX 阿莫西林胶囊
0.25g*24粒/盒
采购价
¥12.50
折后约 ¥11.80
累计已购买 500盒
采购记录（20笔）
有效期至：2027-12-31
生产日期：2026-06-01
生产厂家：XX制药有限公司
批准文号：国药准字H44021345
...
```

### 正则编写规则

| 规则 | 说明 | 示例 |
|------|------|------|
| `[：:]` 匹配中英文冒号 | 页面可能用 `：` 或 `:` | `批准文号[：:]\s*(.+)` |
| `([^\n]+)` 匹配到行尾 | 提取冒号后整行内容 | `生产厂家[：:]\s*([^\n]+)` |
| `([\d.]+)` 匹配数字 | 价格、数量等 | `成团价\s*¥\s*([\d.]+)` |
| `([盒瓶支包袋片套罐条贴副双个])` 匹配单位 | 药品单位枚举 | 已在 `UNIT_RE` 中定义 |
| `\s*` 匹配空白 | 标签和值之间可能有空格 | `有效期至[：:]\s*([\d-]+)` |

---

## 五、对话自定义速查表

| 你想做的 | 怎么说 | AI 改哪里 |
|----------|--------|----------|
| 加抓新字段 | "详情页帮我加抓医保代码" | `parse_detail()` + `build_offers()` + 表头 |
| 改大单阈值 | "大单阈值改成100" | `parse_detail()` 的 `if q >= 50` |
| 改近N天统计 | "近7天改成近30天" | `parse_detail()` 的 `is_last_7_days()` + 变量名 |
| Excel加列 | "Excel里加上生产厂家" | `process.py` 的 `hdr1`/`hdr3` + 数据行 |
| HTML加列 | "档位页加上医保类型" | `process.py` 的 HTML 模板 `<th>` + `<td>` |
| 删字段 | "不需要采购明细了" | 注释掉 `parse_detail()` 对应正则 |
| 改档位定义 | "档位按4分位而不是3分位" | `assign_tier()` 的分位逻辑 |
| 改排序方式 | "按价格升序而不是销量降序" | `product_list.sort()` |

---

## 六、注意事项

### 6.1 正则准确性

- AI agent 修改正则前会先获取详情页实际文本，确保模式匹配
- 部分字段在不同商品类型（拼团/普通）下格式不同，AI 会同时适配两种格式
- 如果正则匹配失败，字段值为 `null`，报表中显示「—」，不会报错

### 6.2 重新采集

- **新增字段后需要重新采集**才能获得新数据。已有的 `detail_data.json` 不含新字段
- 用 `--existing detail_data.json` 断点续传时，旧记录不会自动补采新字段。需要删除 `detail_data.json` 全量重采，或让 AI 写一段补采逻辑
- AI agent 会根据情况建议你是否需要重新采集

### 6.3 性能影响

| 操作 | 性能影响 |
|------|----------|
| 加 1-2 个正则字段 | 几乎无影响（<1ms/条） |
| 加采购记录解析 | 已有，不影响 |
| 加图片下载/OCR | 显著增加耗时 |
| 减少字段（去掉采购记录） | 提速 30-50% |

### 6.4 数据准确性

- 详情页文本是 `document.body.innerText`，即用户在页面上看到的文字
- 字体反爬只影响价格数字（列表页），详情页价格是明文，不受影响
- 如果字段在页面上不存在（如普通商品没有"店参团"），正则不匹配，值为 `null`

---

## 七、完整自定义流程示例

以下是一个完整的对话场景，展示从需求到结果的全过程：

### 对话

```
用户: 帮我采集汤臣倍健的数据，抓3页。另外详情页帮我加抓医保代码和医保类型，
      Excel热销产品表里也加上这两列。

AI:   好的，我先看一下详情页的实际文本格式，确认医保代码和医保类型的展示方式。
      [AI 通过 CDP 打开一个详情页，获取 body.innerText]
      确认格式为 "医保代码：H20090012" 和 "医保类型：甲类"。
      现在修改脚本：
      1. cdp_fetch_detail_v2.py 的 parse_detail() 添加两个正则
      2. process.py 的 build_offers() 添加字段映射
      3. process.py 的 Sheet1 表头添加两列
      修改完成，开始采集...
      [运行 extract.py → cdp_fetch_detail_v2.py → process.py]
      采集完成，Excel 和 HTML 报表已生成，包含医保代码和医保类型两列。
```

### 修改的代码

**`cdp_fetch_detail_v2.py` — `parse_detail()` 新增**：

```python
m = re.search(r'医保代码[：:]\s*([A-Z0-9]+)', txt)
d['insurance_code'] = m.group(1) if m else None
m = re.search(r'医保类型[：:]\s*([甲乙丙]+类)', txt)
d['insurance_type'] = m.group(1) if m else None
```

**`process.py` — `build_offers()` 新增**：

```python
"insuranceCode": det.get("insurance_code"),
"insuranceType": det.get("insurance_type"),
```

**`process.py` — Sheet1 表头和数据行新增**：

```python
hdr1 = [..., "商品ID", "医保代码", "医保类型"]
# 数据行
ws.cell(row=r, column=12, value=detail.get("insurance_code", ""))
ws.cell(row=r, column=13, value=detail.get("insurance_type", ""))
# 列宽
for ci, w in enumerate([...], 1):  # 末尾追加 14, 10
```

---

## 八、支持的报表自定义维度

| 维度 | 可自定义内容 | 怎么说 |
|------|-------------|--------|
| 采集字段 | 任何详情页上可见的文本 | "加抓XXX字段" |
| Excel列 | 任意列的增删和顺序 | "Excel加上XXX列" |
| HTML列 | 档位页表格的列 | "档位页加上XXX" |
| 排序方式 | 按销量/价格/供应商数等 | "按XXX排序" |
| 筛选条件 | 只看某类商品/某价位 | "只看价格>50的" |
| 档位定义 | 分位数/档位数/命名 | "改成4档" |
| 统计阈值 | 大单阈值/近N天 | "大单阈值改100" |
| 去重规则 | 按名称/规格/供应商 | "按供应商去重" |
| 品牌前缀 | 系列归并时剥离的前缀 | "去掉'乐药师'前缀" |
| 图片大小 | Excel缩略图尺寸 | "图片改大一点" |

---

## 九、快速验证修改效果

修改字段后，可以用以下方式快速验证：

```bash
# 只抓 1 页 10 条，快速看效果
python cdp_fetch_detail_v2.py --input vuex_raw.json --top-n 10 --brand 测试

# 检查 detail_data.json 中是否有新字段
python -c "import json; d=json.load(open('detail_data.json')); print([k for k in list(d.values())[0].keys()])"

# 重新出表
python process.py --brand 测试 --input vuex_raw.json --detail detail_data.json
```

AI agent 会在修改后自动运行验证，确认新字段已正确采集和展示。

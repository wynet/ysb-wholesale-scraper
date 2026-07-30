---
name: ysb-wholesale-scraper
description: "Scrape 药帮忙 / 药师帮 (dian.ysbang.cn) wholesale product listings for a brand or keyword, decrypt prices that are BOTH font-scrambled AND encrypted in a priceToken protobuf, dedupe products, merge variants into product families, and produce an Excel report (with embedded images) plus an HTML report. Use this when the user asks to collect, rank, or compare wholesale drug/health products from 药帮忙, especially when they need real prices despite anti-scraping, best-seller ranking by sales, or lowest-price tiers per product family."
agent_created: true
---

# 药帮忙 (YSB) 批发商品采集与统计

采集药师帮（dian.ysbang.cn）按关键词/品牌搜索的批发商品，破解其价格反爬，产出
可排序、可去重、按「产品系列」归并的统计报表（Excel + HTML）。

> **本 skill 由 AI agent 自动调用。用户只需用自然语言描述需求（品牌、页数等），AI agent 负责全部执行流程。**

## 何时使用
- 用户要采集药师帮某品牌/关键词（如「汤臣倍健」「同仁堂」）的商品列表。
- 需要真实**单价**（站点用字体反爬 + 数据层加密，直接抓 DOM 是乱码）。
- 要按**销量**排名、找**热销 TOP10**、或对比每款产品的**最低几个报价档位**。
- 要求去重、按系列合并不同规格、导出 Excel / HTML。

## 前置条件（AI agent 自动检查）

AI agent 收到请求后，依次检查以下条件，缺少时自动处理或提示用户：

1. **Chrome 9222 调试会话** — 若不可用，提示用户启动：
   `chrome.exe --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=持久目录 --no-first-run`
2. **Python 依赖** — AI agent 自动检测并安装：`browser-use`、`openpyxl`、`websocket-client`、`Pillow`、`numpy`
3. **登录态** — 若未登录或已过期，AI agent 自动运行 `auto_login.py`（需用户提供手机号+密码）

## 关键反爬事实（务必先读）
价格有**两层**保护，详情见 `references/decryption.md`：
1. **字体反爬**：数字经自定义字体渲染成乱码 CJK 字符，**DOM 文本里的价格不可信**。
2. **数据层加密**：每条商品在 Vuex 里有 `priceToken`（base64 编码的 protobuf），内含明文真实单价。解码即可，**无需破解字体、无需登录**。

采集与解析的 Vuex 结构、字段含义、以及最严重的「推荐商品串号」陷阱，见 `references/vuex_schema.md`。

## AI agent 执行流程

### 步骤 0 — 自动登录 + 滑块验证（脚本：`scripts/auto_login.py`）
若 Chrome 9222 会话**未登录**或登录已过期，AI agent 自动运行此脚本完成登录+网易易盾滑块验证。
脚本通过 CDP 直连浏览器，自动填写账号密码、检测滑块、下载背景图分析缺口位置、
模拟人类拖拽轨迹（ease-out + y轴抖动 + 回弹），失败自动刷新验证码重试（最多 4 次）。

**AI 执行方式**：
```bash
python scripts/auto_login.py <手机号> <密码>
```

- 退出码 0=成功，1=失败（需人工处理滑块）。
- 若已登录则直接退出（不重复登录）。
- **滑块验证原理**：网易易盾拼图验证码——`cdp_fetch_detail_v2.py` 用 `ddddocr.slide_match()` 精准识别缺口（推荐）；`auto_login.py` 用 numpy 分析每列像素梯度找到缺口边缘（成对峰值，间距≈拼图块宽度），缩放到显示尺寸后计算拖拽距离。
- **人类轨迹模拟**：先慢速靠近滑块→按下→ease-out 拖拽（先快后慢，25+步）→y轴微小抖动→终点回弹→释放。
- **依赖**：`websocket-client`（CDP 通信）、`Pillow`+`numpy`（图像分析）。
- **注意**：滑块验证可能因图片质量或策略更新偶尔失败，实测第 3 次尝试通过率最高。若 4 次均失败，需用户手动完成滑块验证后重跑采集脚本。

### 步骤 1 — 列表采集（脚本：`scripts/extract.py`）
该脚本在 **browser-use 沙箱**内执行，连接已登录的 9222 Chrome，逐页读取 Vuex
store 并**按商品名精确匹配**各自卡片文本，把全部商品 JSON 打到 stdout。

**AI 执行方式**（browser-use CLI 只接 stdin，不接文件参数）：
```bash
B="<managed_env>/Scripts/browser-use.exe"
"$B" > vuex_raw.json 2> extract.err.log <<'PY'
import sys; sys.argv = ['extract.py', '--brand', '<品牌名>', '--pages', '<页数>']
_p = r'<skill_dir>\scripts\extract.py'
exec(compile(open(_p, encoding='utf-8').read(), _p, 'exec'), globals())
PY
```

AI agent 根据用户描述自动设置参数（写进 sys.argv）：
| 参数 | 默认 | 说明 |
|---|---|---|
| `--brand` | 汤臣倍健 | 品牌名，仅用于日志 |
| `--search-key` | 同 `--brand` | 搜索关键词，自动 URL 编码 |
| `--pages` | 15 | 抓取页数（每页约 60 条） |
| `--sort-by-sales` | true | 是否按销量排序（true/false） |
| `--verify-wait` | 60 | 验证弹窗等待秒数 |

脚本特性：翻页靠点「下一页」按钮（SPA 的 `?page=N` 无效）；列表稳定后才读（防瞬间空列表）；每页主动检测登录失效 + 验证弹窗——**检测到滑块先自动解决（JS canvas 图像分析找缺口 + 模拟人类拖拽轨迹，刷新重试3次），失败再暂停等人工完成**（详见 `references/troubleshooting.md`）。

### 步骤 2 — 解析与出表（脚本：`scripts/process.py`）
AI agent 用**普通 python**（非 browser-use）运行，从 `vuex_raw.json` 读取：
- 解码 `priceToken` 得到真实单价；
- 解析「已拼N」销量（支持「N万+」×10000）、有效期、满减档位；
- 按商品规格去重，再按「产品系列」归并不同规格；
- 生成三表 Excel（热销系列 / TOP10 最低价 3 档位 / 产品明细）并嵌入图片；
- 同时产出 HTML 报告，并把当日 TOP10 快照追加进 `sales_history.json`（用于拼趋势）。

**AI 执行方式**：
```bash
python scripts/process.py --brand <品牌名> --input vuex_raw.json --detail detail_data.json
```
- 换品牌只改 `--brand`，文件名/标题自动带品牌名。
- 若有 `detail_data.json`（步骤 2.5 产出），加 `--detail detail_data.json` 整合权威销量。
- 如需「近 N 天趋势」，重复每日运行（或接每日自动化），`sales_history.json` 会累积。

参数：
| 参数 | 默认 | 说明 |
|---|---|---|
| `--brand` | 妇炎洁 | 品牌名（文件命名/标题/前缀剥离） |
| `--input` | vuex_raw.json | extract.py 的输出 |
| `--detail` | detail_data.json | fetch_detail.py 的输出（无则权威销量列显示「—」） |
| `--img-dir` | ysb_images | 图片目录 |
| `--output-xlsx` | `<BRAND>_热销统计_系列合并_<日期>.xlsx` | 显式指定 |
| `--output-html` | `<BRAND>_热销采购分析_<日期>.html` | 显式指定 |

### 步骤 2.5 — 详情页权威销量（脚本：`scripts/fetch_detail.py`）
步骤 1 的搜索列表「已拼」销量粗糙且易重复累计，**不可作权威销量**。商品详情页
`#/drugInfo?wholesaleid=X` 含更精准的深层销量。本脚本用 browser-use 脚本化模式
（`js()` 直跑页面 JS，不依赖 LLM）逐一点开全部代表报价的详情页（默认全量，见下方「取数范围」），正则抽取：
- `成团价`、`已成团/N-unit起拼`（起拼量+单位）
- **`N店参团`**（参团店铺数）、**`N unit已付款`**（权威销量，带单位 盒/瓶/支…）
- `采购记录（N笔）`、有效期至 / 生产日期 / 生产厂家 / 批准文号
- 最近采购明细（买家脱敏 / 手机 / 数量 / 时间，可做销量时序）
- **近 7 天销量**：从采购明细按时间戳（支持"5分钟前"/"2026-07-27"等相对/绝对格式）筛选最近 7 天的销量合计
- **大单统计（≥50）**：单笔数量 ≥ 50 的订单笔数 + 总销量（用于识别大宗采购/分销商行为）
输出 `detail_data.json`（按 wholesaleid 索引）。

**AI 执行方式**（同样走 stdin，结果 print 到 stdout 由外层重定向）：
```bash
B="<managed_env>/Scripts/browser-use.exe"
"$B" > detail_data.json 2> fetch.err.log <<'PY'
import sys; sys.argv = ['fetch_detail.py', '--input', 'vuex_raw.json', '--existing', 'detail_data.json', '--top-n', '<N>']
_p = r'<skill_dir>\scripts\fetch_detail.py'
exec(compile(open(_p, encoding='utf-8').read(), _p, 'exec'), globals())
PY
```

参数：
| 参数 | 默认 | 说明 |
|---|---|---|
| `--input` | vuex_raw.json | extract.py 的输出 |
| `--existing` | detail_data.json | 已有详情路径，用于断点续传（跳过已抓 wid） |
| `--top-n` | 0 | 只抓前 N 个 wid（0=全量） |

- **取数范围**：默认抓**全部**搜索到的代表报价详情页——从 `vuex_raw.json` 提取所有不重复 `wholesaleid`（约 900 个，耗时约 1–1.5 小时）。若只抓 Top N，用 `--top-n N`。
- **断点续传**：脚本读 `--existing` 指定的已有 `detail_data.json`，跳过已抓的 wid。崩溃后重跑自动续上，不会重复抓。
- **不再在沙箱里写文件**：结果 print 到 stdout，由外层 `> detail_data.json` 重定向（旧版在沙箱里 `open().write()` 会丢，已修复）。
- **自动重试失败记录**：重跑时不仅跳过已抓的 wid，还会检测关键字段（paid_units/stores_joined/purchase_records/detail_price）全为 None 的记录并重新抓取，避免异形页空数据残留。
- **智能路由选择**：脚本根据商品名自动判断拼团/普通类型，选择正确的路由参数：
  - 拼团商品（商品名含「包邮」）：先尝试 `isAssemble=true, scene=0`（拼团页，有店参团/已付款/采购记录），失败再尝试 `isAssemble=false, scene=1`（普通页）。
  - 普通商品（商品名含「起购」）：先尝试 `isAssemble=false, scene=1`（普通页，有采购价/折后约/累计已购买），失败再尝试拼团路由。
  - 两种格式都支持解析：成团价/采购价、折后价、店参团、已付款、采购记录、累计已购买、有效期、生产厂家、批准文号等。
- **SPA 缓存清除**：每次导航到新商品前，先 `$router.push('/home')` 回首页，强制 Vue 卸载当前详情页组件再重新挂载，避免 SPA 缓存导致所有商品数据相同。
- **弹窗处理**：支持多种弹窗按钮（确认/确定/我知道了/取消/关闭等），每次导航后自动关闭弹窗。
- **延迟防验证**：每个商品之间延迟 2 秒，避免频繁请求触发滑块验证。
- **采集途中滑块自动处理**：每次导航到详情页后检测验证弹窗——检测到易盾滑块则自动解决（JS canvas 图像分析找缺口 + setTimeout 调度鼠标事件模拟人类拖拽，刷新重试3次），自动解决失败再等待人工完成。检测点共3处：SPA 入口页、每次路由导航后、new_tab 兜底后。
- `new_tab` 异常被捕获，单条失败仅跳过不中断。
- 个别异形页返回 null，`process.py` 在 HTML 中按 `data-status="pending"` 把该行**置灰标「待补采」**，并回退列表「已拼」值（标「列表」区分）。
- `process.py` 读 `detail_data.json`：用「已付款件数」作**权威销量主指标**（不同报价单位盒/瓶不可相加，排名取代表报价单一值），并在 HTML 档位页顶部展示「销量分布」小结（最高/平均已付款件数、店铺总数）。

### 步骤 2.7 — CDP 直连详情页采集 v2（脚本：`scripts/cdp_fetch_detail_v2.py`）
> **推荐方案**：不依赖 browser-use，直连 Chrome 9222 WebSocket，兼容 Python 3.10+。

`fetch_detail.py` 依赖 browser-use（需 Python ≥3.11），在 Python 3.10 环境下不可用。
`cdp_fetch_detail_v2.py` 是等价替代，用 `websocket-client` 直连 CDP，并集成了 **ddddocr** 滑块识别。

**与 fetch_detail.py 的差异**：
- **ddddocr 滑块识别**：用 `ddddocr.slide_match()` 精准匹配缺口位置（替代 canvas 梯度分析），识别精度大幅提升。
- **CDP Input.dispatchMouseEvent 拖拽**：通过 CDP 原生鼠标事件模拟拖拽（比 JS `MouseEvent`/`PointerEvent` 更底层、更难被检测），配合人类轨迹模拟（缓动+抖动+过冲回修）。
- **三级滑块解决策略**：① ddddocr + CDP 鼠标（首选）→ ② Canvas 梯度分析 + JS 事件（备用）→ ③ 等待人工完成（兜底，120秒超时）。
- **断线自动重连**：WebSocket 连接中断后自动重新连接 Chrome 标签页，不中断采集。
- **周期保存**：每 10 条自动保存到 `--existing` 文件（原子写入 `.tmp` 后 `os.replace`），防止崩溃丢数据。
- **页面崩溃恢复**：检测到 Chrome 标签页崩溃时，通过 CDP HTTP 接口创建新标签页继续采集。

**AI 执行方式**（普通 python，不走 browser-use 沙箱）：
```bash
python scripts/cdp_fetch_detail_v2.py \
  --input vuex_raw.json \
  --existing detail_data.json \
  --brand <品牌名> \
  --top-n 0 \
  > detail_data.json 2> detail.err.log
```

参数：
| 参数 | 默认 | 说明 |
|---|---|---|
| `--input` | vuex_raw.json | extract.py 的输出 |
| `--existing` | detail_data.json | 已有详情路径（断点续传+周期保存） |
| `--top-n` | 0 | 只抓前 N 个 wid（0=全量） |
| `--brand` | unknown | 品牌名（用于日志标识） |

**额外依赖**：`ddddocr`（`pip install ddddocr`）、`websocket-client`。

### 步骤 3 — browser-use 文件系统隔离（重要）
browser-use 沙箱进程写出的文件**在真实工作区不可见**（看似成功实则丢失）。
**解决：采集脚本只把 JSON 打到 stdout，由普通 python 进程负责写 xlsx / 图片 / html。**
切勿在 extract.py 内直接 `open().write()` 产物。

## 文件交付

AI agent 完成采集后，用 `computer://` 链接向用户交付以下文件：
- `BRAND_热销统计_系列合并_日期.xlsx` —— 三张工作表（BRAND 为品牌名、日期为抓取日期）。
  - **热销产品**：排名 / 产品名称 / 规格 / 单价 / 起订量 / 总销量 / 供应商数 / 全网最低价 / **阶梯价(所有档位)** / 有效期 / 商品ID / **近7天销量** / **大单数(≥50)** / **大单总销量**。
  - **TOP10最低价档位**：产品内 3 个最低报价（含价格/起订/供应商/商品ID）。
  - **产品明细(去重)**：全部去重 listing 详情。
- `BRAND_热销采购分析_日期.html` —— 网页版（顶部数据完整性状态条；含阶梯价/近7天/大单列，档位页可钻取；待补采行置灰）。
- `sales_history.json` —— 每日 TOP10 快照，供趋势分析。
- `ysb_images/` —— 下载的商品图片。

## 已知局限 / 待补采机制
- **详情页抓取依赖稳定 Chrome**。新版已大幅改善稳定性：SPA 内 Vue Router 导航（不 new_tab+reload）、每次导航前回 /home 清缓存、智能路由选择（拼团/普通）、弹窗自动关闭、2 秒间隔防验证。崩溃时 stdout 不会 flush 到 `detail_data.json`——重跑时 `--existing detail_data.json` 自动跳过已抓的 wid 续传，并自动重试关键字段为空的记录。
- `process.py` 对此做了**诚实降级**：HTML 顶部显示「数据完整性状态条」（已采集 N / 总 M 条真实销量 · 剩余 K 条待补采），档位页 pending 行置灰标「待补采」，其余缺失指标显示「—」。
- **根治办法**：用独立 `--user-data-dir` 起 Chrome（避免守护进程抢锁）、脚本内加 CDP 重连/重试、或在抓取稳定后再跑 `fetch_detail.py` 全量补全。补全后只需重跑 `process.py`，状态条与置灰会自动消失。
- 字段→HTML 元素的完整映射见 `references/report_schema.md`（由《药师帮采购比价报表·结构化建议书》定稿）。

## 数据 schema 与档位定义（团队定稿）
- **报价级(wholesaleid)**：成团价/采购价｜折后价｜起拼量+单位｜供应商｜**已付款件数(权威销量)**｜参团店铺数｜采购笔数｜累计已购买｜有效期至/生产日期/厂家/批号｜最近采购明细｜近7天销量｜大单(≥50)笔数+总销量。（拼团页含店参团/已付款/采购记录；普通页含采购价/折后约/累计已购买，脚本自动适配两种格式。）
- **产品级(名+规格)**：均价｜供应商数(去重)｜全网最低价｜**阶梯价(所有档位)**｜代表报价(取已付款最高者)｜销量排名｜**近7天销量**｜**大单统计**｜档位页入口。
  - **阶梯价**：同一产品下所有唯一 `(起订量, 单价, 单位)` 组合，按起订量升序排列。例如 `1盒/¥13.30; 6瓶/¥11.50; 10盒/¥13.20`。来源是同一产品的不同 listing（不同商家或同商家的不同起订档位），在 `product_summary()` 中聚合去重。
  - **近7天销量**：来自详情页代表报价的采购记录时序筛选（取销量最高那家详情页的数据）。
  - **大单统计**：同上，筛选数量 ≥ 50 的订单。
- **档位三命名（替代最低/次低/次次低）**，按价格分位带切分：
  - `源头直供档`（价≤Q1，价格锚点）· `主流走量档`（Q1<价≤Q3，成交密集，含销量冠军）· `精选优价档`（价>Q3，小批量/高服务/长效期）。
  - 叠加销量标签 `爆款领跑 / 稳健供货 / 长尾备选`（按已付款件数排名）。
  - 报价不足 3 个时无分位意义，统一归 `主流走量档`。
- **HTML 结构**：报告顶部有**数据完整性状态条**（已采集 N / 总 M 条真实销量 · 剩余 K 条待补采）。首页列 排名 / 最低价 / **阶梯价(所有档位)** / 均价 / 供应商数 / 销量(权威) / **近7天** / **大单数+总量** / 有效期，点标题进档位页；档位页列最多 10 个报价链接（drugInfo），含权威销量、顶部销量分布小结（含**近7天/大单指标**）、可折叠最近采购时序。**`paid_units` 缺失的报价行按 `data-status="pending"` 置灰标「待补采」**，缺失的近7天/大单显示「—」。
  - **商品详情链接**：Excel 三表和 HTML 报告中的商品 ID/详情链接均根据商品名自动选择路由参数——拼团商品（包邮）用 `isAssemble=true&scene=0`，普通商品（起购）用 `isAssemble=false&scene=1`，确保点击后打开正确的详情页。

## 易错点速查
- **翻页必须用「下一页」按钮，URL 的 `?page=N` 参数无效**：站点是 SPA，直接改 URL 或 `new_tab(url?page=N)` 打开的永远是第 1 页（表现为「浏览器一直停在第 1 页」、且所有页数据雷同）。正确做法：单标签页进入第 1 页 → 必要时点「销量」排序 → 循环 `click_next()`（点 `.pagination-next` 或文本「下一页」），每次翻页后**等待列表首条变化**才算生效。每页约 60 条。
- **销量不能简单求和，否则虚高 2–20×**：同一独立商品（商品名+供应商+规格）常被同一商家挂成多个报价档位（实测最多 20 个），每个档位有各自真实「已拼」销量。系列/品牌总销量 = **每款独立商品取「最高已拼」后求和**，绝不能把所有 listing 的销量直接相加。（见 `process.py` 的 `series_summary()`。）
- 卡片与 Vuex 列表**不能按索引对齐**——搜索页混入了「猜你喜欢」推荐商品，错位会把别家销量/价格挂到主商品上（详见 vuex_schema.md）。必须用**商品名匹配卡片**并截取主商品文本块。
- `alreadysales` 字段常为 0，不是真实销量；真实销量在卡片「已拼N」文本里。
- **图片压缩务必「内存内」完成，不要落临时 `.orig` 文件**：用 `PIL.Image.open(io.BytesIO(data))` 直接读字节、缩略图后保存；不要 `open(tmp).write()` 再 `os.remove(tmp)`。本环境有「批量删除安全策略」（单轮约 50 次删除即触发拦截、杀进程）。内存压缩可彻底规避。缩略图 (120×120, JPEG q82) 能把单图 120KB→~2KB，900 张图 Excel 从 160MB 降到 ~2MB。
- 按「产品系列」合并时，要去掉品牌前缀、规格（如 120g/400g）、括号，并用去重词规则（如「蛋白粉蛋白粉」→「蛋白粉」），否则会把同名拆成多系列。
- 「最低/次低/次次低」应在**同一系列内部**取 3 个不同价格水平，每档标注供应商+所需数量+有效期+该价销量，**不要跨系列混**。

## 成长机制（skill 自我积累经验 · 增强版）
skill 会随着使用自动成长——每次 `process.py` 出表后，自动把本次运行记录写入
skill 目录下的 `profile.json`（v2），下次运行时自动打印成长摘要和趋势提示。

**自动记录的内容**：
- 每次运行：日期、品牌、采集条数、产品数、TOP1 名称+销量、**运行耗时**
- **数据质量**：详情页采集成功率（已采集/总数）、待补采数量、质量历史趋势
- **配置记忆**：上次运行的参数（品牌/输入/是否有详情），下次运行自动提示
- **价格/销量趋势**：对比 `sales_history.json` 上次快照，提示 TOP1 销量和最低价变化
- 按品牌聚合：运行次数、首次/最近运行、累计采集量、平均耗时、最佳质量
- 全局统计：总运行次数、总采集量、首次运行日期

**同日去重**：同一天同一品牌的多次运行只保留最后一次，避免调试运行污染历史。
**runs 上限**：保留最近 200 条，更旧的自动归档到 `archived_runs`。

**成长摘要**（process.py 输出末尾自动打印）：
```
[grow] 第 3 次运行 · 累计采集 156 条 · 耗时 12.3s · 常用: 妇炎洁(2), 汤臣倍健(1)
[grow] 数据质量: 55/60 已采集(91.7%) · 待补采 5
[grow] 妇炎洁 已跑 2 次 · 上次 48 条/29 个产品 · 平均耗时 11.5s · 最佳质量 95.0%
[trend] TOP1 销量变化: +1200 · 最低价变化: -0.50（对比 2026-07-28）
```

**查看成长报告**（脚本：`scripts/grow.py`）：
```bash
python scripts/grow.py                  # 全局摘要（含趋势快报/质量/配置建议）
python scripts/grow.py --brand 妇炎洁   # 某品牌的详细历史（含耗时/质量）
python scripts/grow.py --runs 10        # 最近 10 次运行
python scripts/grow.py --trend          # TOP产品价格/销量趋势分析
python scripts/grow.py --quality        # 数据质量追踪（采集率变化趋势）
python scripts/grow.py --html           # 生成 HTML 可视化成长报告
```

`profile.json` 位于 skill 目录，跨项目共享——不管在哪个工作区跑，成长记录都累积到同一份。

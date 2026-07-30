# ysb-wholesale-scraper

采集药师帮（dian.ysbang.cn）批发商品数据，破解价格反爬，生成可排序、去重的 Excel + HTML 统计报表。

## 功能

- **价格破解**：两层反爬（字体反爬 + priceToken protobuf 加密），用解码绕过
- **销量采集**：列表页「已拼」销量 + 详情页「已付款件数」权威销量
- **深度数据**：近7天销量、大单统计(≥50)、采购记录时序、阶梯价、店参团数
- **自动登录**：CDP 直连浏览器，网易易盾滑块自动解决（图像分析+模拟拖拽）
- **滑块处理**：采集全程3处检测点，先自动解决，失败转人工等待
- **智能路由**：拼团商品(包邮)和普通商品(起购)自动选择正确路由参数
- **数据归并**：按商品名+规格去重，按产品系列归并不同规格
- **诚实降级**：抓取失败的行置灰标「待补采」，不伪造数据
- **成长机制**：自动记录运行次数、品牌排行、数据质量、价格趋势

## 使用方式

本 skill 由 AI agent（如 TRAE / WorkBuddy）自动调用，用户只需用自然语言描述需求：

| 用户说 | AI 自动执行 |
|--------|------------|
| "帮我采集云南白药牙膏的数据，只抓第1页" | 登录检测 → 列表采集(1页) → 详情页采集 → 出表 |
| "采集汤臣倍健，抓5页" | 登录检测 → 列表采集(5页) → 详情页采集 → 出表 |

## 目录结构

```
ysb-wholesale-scraper/
├── SKILL.md                         # 技能说明文档（AI agent 读取）
├── profile.json                     # 成长记录 v2
├── docs/                            # 技术文档
│   ├── CHANGELOG.md                 # 更新说明
│   ├── DEPENDENCIES.md              # 依赖说明
│   └── flowchart.html               # Skill 实现流程图（可视化）
├── references/
│   ├── decryption.md                # 价格解密原理（字体反爬 + priceToken）
│   ├── vuex_schema.md               # Vuex 数据结构与采集陷阱
│   ├── report_schema.md             # 字段→HTML元素映射表
│   └── troubleshooting.md           # 常见问题排查
└── scripts/
    ├── ysb_common.py                # 公共模块（CDP操作 + 滑块验证，被多处调用）
    ├── auto_login.py                # 自动登录 + 滑块验证（调用 ysb_common）
    ├── extract.py                   # 列表页采集（browser-use 沙箱内）
    ├── cdp_fetch_detail_v2.py       # 详情页采集 v2（CDP直连，调用 ysb_common）
    ├── fetch_detail.py              # 详情页采集 v1（browser-use 沙箱内）
    ├── process.py                   # 解析出表（普通 Python）
    └── grow.py                      # 成长报告查看
```

## 前置条件

- Chrome 调试会话（端口 9222）
- Python 依赖：browser-use、openpyxl、websocket-client、Pillow、numpy

## 技术架构

| 步骤 | 脚本 | 说明 |
|------|------|------|
| 0. 自动登录 | auto_login.py | CDP 直连 + 网易易盾滑块自动解决（调用 ysb_common 公共模块） |
| 1. 列表采集 | extract.py | browser-use 沙箱内读 Vuex Store |
| 2.5 详情页采集 | cdp_fetch_detail_v2.py | CDP 直连 + SPA Vue Router 导航 + 智能路由（调用 ysb_common） |
| 2. 解析出表 | process.py | 解码 priceToken + 按系列归并去重 |

> **公共模块**：`ysb_common.py` 封装 CDP 基础操作、Windows 窗口激活、易盾滑块完整验证流程（numpy+ddddocr 缺口识别 + 人类轨迹拖拽），被 `auto_login.py` 和 `cdp_fetch_detail_v2.py` 共同调用，确保登录和采集场景验证逻辑完全一致。详见 `docs/` 目录。

## License

MIT

# 依赖说明 (DEPENDENCIES)

## 系统环境要求

| 项目 | 要求 | 说明 |
|------|------|------|
| 操作系统 | Windows 10/11 | `ysb_common.py` 使用 `ctypes.windll` (Win32 API) 激活窗口，仅支持 Windows |
| Python | ≥ 3.10 | `cdp_fetch_detail_v2.py` 兼容 3.10+；`extract.py`/`fetch_detail.py` 的 browser-use 沙箱需 ≥ 3.11 |
| Chrome | 任意版本 | 需以 `--remote-debugging-port=9222` 启动调试会话 |
| 网络 | 可访问 dian.ysbang.cn | 需能访问药师帮网站及 9222 本地调试端口 |

## Chrome 调试会话启动

```bash
chrome.exe --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=C:\chrome-debug --no-first-run
```

| 参数 | 说明 |
|------|------|
| `--remote-debugging-port=9222` | 开启 CDP 调试端口 |
| `--remote-allow-origins=*` | 允许 WebSocket 连接（必须） |
| `--user-data-dir` | 持久化登录态，避免每次重新登录 |
| `--no-first-run` | 跳过首次启动向导 |

---

## Python 依赖一览

### 核心依赖（所有脚本必需）

| 包 | 版本要求 | 安装命令 | 用途 | 被谁使用 |
|------|----------|----------|------|----------|
| `websocket-client` | ≥ 1.0 | `pip install websocket-client` | CDP WebSocket 通信 | `ysb_common.py`, `auto_login.py`, `cdp_fetch_detail_v2.py` |
| `Pillow` | ≥ 9.0 | `pip install Pillow` | 滑块背景图加载、图片缩略图 | `ysb_common.py`, `process.py` |
| `numpy` | ≥ 1.20 | `pip install numpy` | 滑块缺口梯度分析 | `ysb_common.py` |

### 采集专用依赖

| 包 | 版本要求 | 安装命令 | 用途 | 被谁使用 |
|------|----------|----------|------|----------|
| `browser-use` | ≥ 0.1 | `pip install browser-use` | 列表页/详情页采集沙箱 | `extract.py`, `fetch_detail.py` |
| `ddddocr` | ≥ 1.4 | `pip install ddddocr` | 滑块缺口精准匹配（备用方案） | `ysb_common.py` (可选) |

### 出表专用依赖

| 包 | 版本要求 | 安装命令 | 用途 | 被谁使用 |
|------|----------|----------|------|----------|
| `openpyxl` | ≥ 3.0 | `pip install openpyxl` | 生成 Excel 报表（三表+嵌入图片） | `process.py` |

### 标准库依赖（无需安装）

| 模块 | 用途 |
|------|------|
| `json` | 数据序列化 |
| `base64` | priceToken 解码 |
| `re` | 正则提取详情页字段 |
| `time` | 延时控制 |
| `datetime` | 日期解析、趋势计算 |
| `sys` | 命令行参数、stdout/stderr |
| `argparse` | 命令行参数解析 |
| `os` | 文件操作 |
| `io` | 内存字节流（图片处理） |
| `urllib.request` | CDP HTTP 接口调用、图片下载 |
| `ctypes` | Win32 API 窗口激活 |
| `ctypes.wintypes` | Windows 类型定义 |

---

## 一键安装

```bash
pip install websocket-client Pillow numpy openpyxl ddddocr
```

如需使用 browser-use 沙箱版采集脚本（`extract.py` / `fetch_detail.py`）：
```bash
pip install browser-use
```

> **注意**：`browser-use` 依赖 Python ≥ 3.11。若使用 Python 3.10，请改用 `cdp_fetch_detail_v2.py`（CDP 直连，不依赖 browser-use）。

---

## 依赖与脚本对应关系

```
auto_login.py ────────────┬── websocket-client
                          ├── Pillow
                          ├── numpy
                          └── ysb_common.py (→ websocket-client, Pillow, numpy, ddddocr, ctypes)

extract.py ───────────────┬── browser-use
                          └── (标准库: json, re, time, urllib)

cdp_fetch_detail_v2.py ───┬── websocket-client
                          ├── ysb_common.py (→ 同上)
                          └── (标准库: json, base64, re, time, datetime, sys, argparse, os)

process.py ───────────────┬── openpyxl
                          ├── Pillow
                          └── (标准库: json, re, io, os, datetime, argparse)

grow.py ──────────────────└── (标准库: json, sys, argparse, datetime, os)
```

---

## ddddocr 说明

`ddddocr` 是可选依赖，作为 numpy 梯度分析的备用方案：

- **主方案 (numpy)**：分析背景图每列像素差异，找成对峰值（间距≈拼图块宽度），缩放到显示尺寸。成功率约 80%，无需额外依赖。
- **备用方案 (ddddocr)**：使用 `ddddocr.slide_match()` 精准匹配拼图块与背景图的相对位置。识别精度更高，但需要安装 ddddocr（含 ONNX Runtime）。

`ysb_common.py` 的 `solve_slider()` 先尝试 numpy，失败后自动回退到 ddddocr。未安装 ddddocr 时仅使用 numpy，不影响运行。

---

## browser-use 说明

`browser-use` 是一个 AI 驱动的浏览器自动化框架，用于 `extract.py`（列表采集）和 `fetch_detail.py`（详情页采集 v1）。

`cdp_fetch_detail_v2.py` 是 CDP 直连替代方案，**不依赖 browser-use**，兼容 Python 3.10+，推荐使用。

| 场景 | 推荐脚本 | 依赖 |
|------|----------|------|
| 列表采集 | `extract.py` | browser-use |
| 详情页采集 (Python ≥ 3.11) | `fetch_detail.py` | browser-use |
| 详情页采集 (Python 3.10) | `cdp_fetch_detail_v2.py` | websocket-client (推荐) |
| 自动登录 | `auto_login.py` | websocket-client, Pillow, numpy |

---

## 环境检查

运行以下命令检查依赖是否安装完整：

```python
python -c "
import importlib
deps = {
    'websocket': 'websocket-client',
    'PIL': 'Pillow',
    'numpy': 'numpy',
    'openpyxl': 'openpyxl',
    'ddddocr': 'ddddocr (可选)',
}
for mod, name in deps.items():
    try:
        importlib.import_module(mod)
        print(f'[OK] {name}')
    except ImportError:
        print(f'[MISSING] {name}')

try:
    import browser_use
    print('[OK] browser-use')
except ImportError:
    print('[MISSING] browser-use (列表采集需要)')
"
```

---

## 常见依赖问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `ModuleNotFoundError: No module named 'websocket'` | 未安装 websocket-client | `pip install websocket-client` |
| `ctypes.windll` 报错 | 非 Windows 系统 | 本 skill 仅支持 Windows |
| ddddocr 安装失败 | 缺少 ONNX Runtime 依赖 | `pip install onnxruntime` 后重试，或不安装（numpy 够用） |
| browser-use 找不到 | 未安装或 Python 版本过低 | 需 Python ≥ 3.11，或改用 `cdp_fetch_detail_v2.py` |
| Chrome 9222 连接被拒 | 未加 `--remote-allow-origins=*` | 重新启动 Chrome 并加上该参数 |
| 滑块坐标全为 0 | Chrome 标签页不在前台 | `ysb_common.bring_chrome_to_front()` 已内置处理 |

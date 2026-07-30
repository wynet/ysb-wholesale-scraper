# 更新说明 (CHANGELOG)

## 2026-07-30 — 滑块验证公共模块重构 + 稳定性增强

### 核心变更：提取 `ysb_common.py` 公共模块

将 `auto_login.py` 和 `cdp_fetch_detail_v2.py` 中重复的滑块验证逻辑合并为统一的公共模块 `scripts/ysb_common.py`，消除代码冗余，确保登录和采集两个场景使用完全一致的验证逻辑。

#### 新增文件

| 文件 | 说明 |
|------|------|
| `scripts/ysb_common.py` | 公共模块：CDP 基础操作 + Windows 窗口激活 + 易盾滑块完整验证流程 |

#### 修改文件

| 文件 | 变更内容 |
|------|----------|
| `scripts/auto_login.py` | 重构为调用 `ysb_common` 模块，删除约 200 行重复代码 |
| `scripts/cdp_fetch_detail_v2.py` | 新增 `_log()` 带 flush 日志函数，调用 `ysb_common.handle_verify()` 统一处理验证弹窗，支持断线重连 |

---

### `ysb_common.py` 公共模块功能清单

| 功能 | 函数 | 说明 |
|------|------|------|
| Windows 窗口激活 | `bring_chrome_to_front()` | 用 Windows API (`SetForegroundWindow`) 激活 Chrome 窗口，解决 hidden 标签页 `getBoundingClientRect` 返回 0 的问题 |
| CDP 标签页查找 | `find_tab()` | 从 9222 端口查找 `dian.ysbang.cn` 标签页 |
| CDP 消息收发 | `cdp_send()` / `cdp_eval()` | 全局自增消息 ID，消除 `wait_and_read` 与主循环的 ID 冲突 |
| CDP 鼠标操作 | `cdp_mouse()` / `cdp_mouse_press()` / `cdp_mouse_release()` | 封装 `Input.dispatchMouseEvent`，支持 `button`/`buttons` 参数 |
| 图片下载 | `download_image()` | 下载滑块背景图和拼图块 |
| 缺口识别 (numpy) | `find_gap_numpy()` | 主方案：numpy 梯度分析，识别每列像素差异峰值，成对匹配间距≈拼图块宽度的边缘 |
| 缺口识别 (ddddocr) | `find_gap_ddddocr()` | 备用方案：`ddddocr.slide_match()` 精准匹配缺口位置 |
| 滑块信息获取 | `get_slider_info()` | 获取滑块按钮/轨道/背景图/拼图块的精确坐标，含零尺寸检查 |
| 验证弹窗检测 | `check_verify()` | 检测易盾滑块/极验/腾讯防水墙/通用验证弹窗，含尺寸过滤防误报 |
| 人类轨迹拖拽 | `simulate_drag()` | ease-out 轨迹(先快后慢) + y 轴抖动 + 终点回弹，25+ 步 |
| 滑块验证完整流程 | `solve_slider()` | 获取滑块 → 等待渲染 → numpy/ddddocr 识别缺口 → 拖拽 → 检查结果 → 刷新重试(最多 6 次) |
| 验证弹窗处理 | `handle_verify()` | 自动验证 → 失败等待人工(120 秒超时)，支持断线重连回调 |

---

### 关键改进点

#### 1. 统一滑块验证逻辑

**变更前**：`auto_login.py` 和 `cdp_fetch_detail_v2.py` 各自维护一套滑块验证代码，逻辑存在细微差异（如重试次数、日志格式、缺口识别策略），导致登录和采集场景的验证成功率不一致。

**变更后**：两个脚本统一调用 `ysb_common.solve_slider()` 和 `ysb_common.handle_verify()`，确保行为完全一致。后续修改滑块逻辑只需更新一处。

#### 2. 滑块渲染等待机制

**问题**：`check_verify()` 检测到验证弹窗时，滑块元素可能刚出现在 DOM 中但尚未完成渲染（`getBoundingClientRect` 返回 width=0），导致后续缺口分析和拖拽失败。

**修复**：`solve_slider()` 在开始验证前循环等待最多 7.5 秒（15 次 × 0.5 秒），确认背景图宽度 > 50px 且 `img.complete=true`、拼图块宽度 > 10px 且 `img.complete=true` 后才开始验证。

#### 3. 零尺寸元素过滤

**问题**：隐藏的 `display:none` 元素仍能被 `querySelector` 选中，导致误判为检测到验证弹窗。

**修复**：`get_slider_info()` 增加 `sRect.width < 5 || sRect.height < 5 || cRect.width < 50` 检查；`VERIFY_JS` 中对所有候选元素增加 `getBoundingClientRect().width > 5 || height > 5` 过滤。

#### 4. Windows API 窗口激活

**问题**：Chrome 标签页不在前台时（hidden=true），`getBoundingClientRect` 返回全 0，滑块坐标无效。

**修复**：`bring_chrome_to_front()` 使用 `EnumWindows` 遍历窗口，匹配标题含 "Chrome"/"药师"/"ysbang" 的窗口并 `SetForegroundWindow`。设置了正确的 `argtypes` 和 `restype`，并调用 `SetProcessDpiAwareness(2)` 确保 DPI 感知。

#### 5. 断线重连支持

**问题**：采集过程中 WebSocket 连接可能断开，导致验证检测失败。

**修复**：`handle_verify()` 接受可选的 `reconnect_fn` 回调，连接异常时自动调用重连函数获取新的 WebSocket 连接。`cdp_fetch_detail_v2.py` 传入 `reconnect()` 函数实现自动重连。

#### 6. 实时日志输出

**问题**：PowerShell 管道中 stderr 默认缓冲，日志无法实时显示。

**修复**：`cdp_fetch_detail_v2.py` 新增 `_log()` 函数，每次 `sys.stderr.write()` 后立即 `sys.stderr.flush()`，确保日志实时输出。

---

### 测试验证

| 测试场景 | 结果 |
|----------|------|
| 退出登录 → 重新登录 | 滑块第 2 次尝试通过，登录成功跳转 `#/home` |
| 登录后采集 10 条详情 | 验证弹窗自动处理通过，数据正常采集 |
| 滑块验证一致性 | 登录和采集使用同一套 `ysb_common` 代码，行为一致 |

---

### 架构对比

```
变更前：
  auto_login.py ──┬── CDP 操作（重复）
                  ├── 缺口识别（重复）
                  ├── 拖拽模拟（重复）
                  └── 验证检测（重复）
  
  cdp_fetch_detail_v2.py ──┬── CDP 操作（重复）
                           ├── 缺口识别（重复）
                           ├── 拖拽模拟（重复）
                           └── 验证检测（重复）

变更后：
  auto_login.py ──────────┐
                          ├──→ ysb_common.py（统一公共模块）
  cdp_fetch_detail_v2.py ─┘      ├── CDP 基础操作
                                 ├── Windows 窗口激活
                                 ├── 缺口识别 (numpy + ddddocr)
                                 ├── 人类轨迹拖拽
                                 ├── 滑块验证完整流程
                                 └── 验证弹窗处理（含重连）
```

---

### 已知限制

- 网易易盾行为分析会检测轨迹特征（速度曲线、时间间隔），纯 CDP 自动化偶尔被拒绝
- 多次连续失败会触发易盾服务端锁定（5-15 分钟自动解除）
- 自动验证失败时需人工完成滑块（脚本等待 120 秒）
- `ysb_common.py` 依赖 Windows API（`ctypes.windll`），仅支持 Windows 平台

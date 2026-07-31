# -*- coding: utf-8 -*-
"""药师帮公共模块 — CDP基础操作 + Windows窗口激活 + 易盾滑块验证

被 auto_login.py 和 cdp_fetch_detail_v2.py 共同调用，
避免滑块验证逻辑在多个脚本中重复维护。

依赖：websocket-client, Pillow, numpy
    pip install websocket-client Pillow numpy
"""
import json, time, sys, io, urllib.request, websocket
import ysb_parser

CDP_URL = "http://127.0.0.1:9222"


# ====================== Windows 窗口激活 ======================
try:
    import ctypes, ctypes.wintypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

_user32 = ctypes.windll.user32
_user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
_user32.SetForegroundWindow.restype = ctypes.c_bool
_user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
_user32.IsWindowVisible.restype = ctypes.c_bool
_user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = ctypes.c_bool
_user32.IsZoomed.argtypes = [ctypes.wintypes.HWND]
_user32.IsZoomed.restype = ctypes.c_bool
_user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
_user32.IsIconic.restype = ctypes.c_bool
# ShowWindow 命令常量
SW_RESTORE = 9       # 还原窗口（如果最小化/最大化）
SW_SHOWMAXIMIZED = 3 # 最大化


def bring_chrome_to_front(maximize=True):
    """用 Windows API 激活 Chrome 窗口。

    仅在窗口未最大化时才执行最大化，避免反复还原→最大化导致页面布局抖动。
    仅在窗口最小化时才执行还原。

    参数：
        maximize: 是否需要最大化窗口（默认 True，滑块验证时推荐最大化）
    """
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def callback(hwnd, lparam):
        if _user32.IsWindowVisible(hwnd):
            length = _user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                _user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if "Chrome" in title or "药师" in title or "ysbang" in title.lower():
                    found.append(hwnd)
        return True

    _user32.EnumWindows(callback, 0)
    if found:
        hwnd = found[0]
        already_maximized = _user32.IsZoomed(hwnd)
        is_minimized = _user32.IsIconic(hwnd)
        # 仅最小化时才还原（还原会取消最大化状态，不要对已最大化窗口调用）
        if is_minimized:
            _user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.2)
        # 仅未最大化时才最大化
        if maximize and not already_maximized:
            _user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
            time.sleep(0.3)
        _user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        return True
    return False


# ====================== CDP 基础操作 ======================
def get_tabs():
    r = urllib.request.urlopen(CDP_URL + "/json", timeout=5)
    return json.loads(r.read())


def find_tab():
    """找到 dian.ysbang.cn 的标签页"""
    for t in get_tabs():
        if t.get("type") == "page" and "dian.ysbang.cn" in t.get("url", ""):
            return t
    for t in get_tabs():
        if t.get("type") == "page":
            return t
    return None


_global_mid = [0]


def cdp_send(ws, method, params=None, timeout=30):
    """发送 CDP 命令并等待匹配响应。
    超时保护：超过 timeout 秒未收到匹配响应则抛出 TimeoutError，
    防止 WebSocket 连接断开导致脚本永久阻塞。
    """
    _global_mid[0] += 1
    msg_id = _global_mid[0]
    msg = {"id": msg_id, "method": method}
    if params:
        msg["params"] = params
    ws.send(json.dumps(msg))
    deadline = time.time() + timeout
    _old_timeout = ws.gettimeout()
    ws.settimeout(5)  # 每次 recv 最多等 5 秒，避免无限阻塞
    try:
        while time.time() < deadline:
            try:
                resp = json.loads(ws.recv())
                if resp.get("id") == msg_id:
                    return resp
            except websocket.WebSocketTimeoutException:
                continue
    finally:
        ws.settimeout(_old_timeout)
    raise TimeoutError("CDP 超时: %s 等待 %ds 未响应" % (method, timeout))


def cdp_eval(ws, expression):
    r = cdp_send(ws, "Runtime.evaluate", {
        "expression": expression,
        "returnByValue": True,
        "awaitPromise": True,
    })
    if "error" in r:
        return None
    result = r.get("result", {}).get("result", {})
    if result.get("type") == "undefined":
        return None
    return result.get("value")


def cdp_mouse(ws, x, y, button="none"):
    return cdp_send(ws, "Input.dispatchMouseEvent", {
        "type": "mouseMoved", "x": x, "y": y, "button": button
    })


def cdp_mouse_press(ws, x, y, button="left"):
    return cdp_send(ws, "Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": x, "y": y, "button": button, "clickCount": 1
    })


def cdp_mouse_release(ws, x, y, button="left"):
    return cdp_send(ws, "Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": x, "y": y, "button": button, "clickCount": 1
    })


# ====================== 图片下载 ======================
def download_image(url, timeout=10):
    """下载图片"""
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ====================== 缺口识别 ======================
def find_gap_numpy(bg_img_data, jigsaw_width=51, bg_display_width=270, log=print):
    """numpy梯度分析识别缺口位置（主方案，成功率~80%）"""
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(io.BytesIO(bg_img_data))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        arr = np.array(img)
        h, w = arr.shape[:2]
        if bg_display_width <= 0 or w <= 0:
            return None
        scale = bg_display_width / w if w != bg_display_width else 1.0
        log("  [numpy] 图片实际宽=%d 显示宽=%d 缩放比=%.3f" % (w, bg_display_width, scale))
        col_diff = np.zeros(w)
        for x in range(1, w):
            diff = np.mean(np.abs(arr[:, x].astype(int) - arr[:, x-1].astype(int)))
            col_diff[x] = diff
        jigsaw_w_actual = int(jigsaw_width / scale) if scale > 0 else jigsaw_width
        search_start = jigsaw_w_actual + 2
        search_end = w - 2
        if search_end <= search_start:
            return None
        threshold = np.max(col_diff[search_start:search_end]) * 0.4
        peaks = []
        for x in range(search_start, search_end):
            if col_diff[x] > threshold:
                peaks.append(x)
        if peaks:
            gap_x = peaks[0]
            best_score = 0
            for i in range(len(peaks)):
                for j in range(i+1, len(peaks)):
                    dist = peaks[j] - peaks[i]
                    if abs(dist - jigsaw_w_actual) < 8:
                        score = col_diff[peaks[i]] + col_diff[peaks[j]]
                        if score > best_score:
                            best_score = score
                            gap_x = peaks[i]
            gap_x_display = int(gap_x * scale)
            log("  [numpy] 缺口位置 原图x=%d -> 显示x=%d" % (gap_x, gap_x_display))
            return gap_x_display
        else:
            log("  [numpy] 未找到明显缺口")
            return None
    except Exception as e:
        log("  [numpy] 分析异常: %s" % e)
        return None


_ddddocr_det = None


def find_gap_ddddocr(bg_img_data, jigsaw_img_data, log=print):
    """ddddocr识别缺口位置（备用方案）"""
    global _ddddocr_det
    if _ddddocr_det is None:
        try:
            import ddddocr
            _ddddocr_det = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
            log("  [ddddocr] 初始化成功")
        except Exception as e:
            log("  [ddddocr] 初始化失败: %s" % e)
            return None
    try:
        result = _ddddocr_det.slide_match(jigsaw_img_data, bg_img_data)
        gap_x = result.get("target", [0])[0]
        log("  [ddddocr] 缺口位置 x=%d" % gap_x)
        return gap_x
    except Exception as e:
        log("  [ddddocr] 异常: %s" % e)
        return None


# ====================== 滑块检测 ======================
def get_slider_info(ws):
    """获取易盾滑块的精确位置（滑块按钮、轨道、背景图、拼图块）
    增加零尺寸检查：元素存在但未渲染时(width<5)返回 found:false。
    """
    js_code = """(() => {
        const slider = document.querySelector('.yidun_slider');
        const control = document.querySelector('.yidun_control');
        const bgImg = document.querySelector('.yidun_bg-img');
        const jigsaw = document.querySelector('.yidun_jigsaw');
        if (!slider || !control) return JSON.stringify({found: false});
        const sRect = slider.getBoundingClientRect();
        const cRect = control.getBoundingClientRect();
        if (sRect.width < 5 || sRect.height < 5 || cRect.width < 50) {
            return JSON.stringify({found: false, reason: 'not_rendered'});
        }
        const bRect = bgImg ? bgImg.getBoundingClientRect() : null;
        const jRect = jigsaw ? jigsaw.getBoundingClientRect() : null;
        return JSON.stringify({
            found: true,
            slider: {x: sRect.x, y: sRect.y, w: sRect.width, h: sRect.height},
            control: {x: cRect.x, y: cRect.y, w: cRect.width, h: cRect.height},
            bgImg: bRect ? {x: bRect.x, y: bRect.y, w: bRect.width, h: bRect.height, src: bgImg.src, complete: bgImg.complete, naturalW: bgImg.naturalWidth} : null,
            jigsaw: jRect ? {x: jRect.x, y: jRect.y, w: jRect.width, h: jRect.height, src: jigsaw.src, complete: jigsaw.complete} : null,
        });
    })()"""
    r = cdp_eval(ws, js_code)
    try:
        return json.loads(r) if r else {"found": False}
    except Exception:
        return {"found": False}


# ====================== 验证弹窗检测 ======================
# VERIFY_JS 统一由 ysb_parser.py 公共模块提供，避免多处重复维护
VERIFY_JS = ysb_parser.VERIFY_JS


def check_verify(ws):
    """检测页面上是否有验证弹窗"""
    try:
        return json.loads(cdp_eval(ws, VERIFY_JS) or "{}")
    except Exception:
        return {"type": None, "hit": ""}


# ====================== 人类轨迹模拟拖拽 ======================
def simulate_drag(ws, start_x, start_y, drag_dist):
    """模拟人类拖拽滑块。
    - 先慢速靠近滑块
    - 按下后 ease-out 轨迹拖拽（先快后慢）
    - y 轴微小抖动（模拟手不稳）
    - 到终点后微小回弹再释放
    """
    end_x = start_x + drag_dist
    # 1. 移动到滑块（逐步靠近）
    cdp_mouse(ws, start_x - 10, start_y - 5)
    time.sleep(0.1)
    cdp_mouse(ws, start_x - 3, start_y - 1)
    time.sleep(0.12)
    cdp_mouse(ws, start_x, start_y)
    time.sleep(0.15)
    # 2. 按下
    cdp_mouse_press(ws, start_x, start_y)
    time.sleep(0.08)
    # 3. 拖拽 - ease-out 轨迹（先快后慢）
    steps = max(25, int(drag_dist / 3))
    for i in range(1, steps + 1):
        progress = 1 - (1 - i / steps) ** 2.5
        x = start_x + drag_dist * progress
        y = start_y + (i % 3 - 1) * 0.4
        cdp_mouse(ws, x, y)
        delay = 0.008 + 0.02 * (i / steps) + (i % 5) * 0.002
        time.sleep(delay)
    # 4. 到达终点，微小回弹
    cdp_mouse(ws, end_x + 3, start_y)
    time.sleep(0.04)
    cdp_mouse(ws, end_x - 2, start_y)
    time.sleep(0.04)
    cdp_mouse(ws, end_x, start_y)
    time.sleep(0.1)
    # 5. 释放
    cdp_mouse_release(ws, end_x, start_y)
    time.sleep(0.1)


# ====================== 滑块验证完整流程 ======================
def solve_slider(ws, max_retries=6, log=print, activate=True):
    """执行完整的滑块验证流程：获取滑块 → 分析缺口 → 拖拽 → 检查结果。
    失败则刷新验证码重试，最多 max_retries 次。

    参数：
        ws: WebSocket 连接
        max_retries: 最大重试次数
        log: 日志输出函数（默认 print，采集脚本可传 sys.stderr.write）
        activate: 是否在拖拽前激活 Chrome 窗口（默认 True）

    返回 True=验证成功，False=失败。
    """
    # 等待滑块完全渲染（check_verify 可能在滑块刚出现、尺寸还为0时就触发）
    log("  [滑块] 等待滑块渲染...")
    slider_info = None
    for wait in range(15):
        time.sleep(0.5)
        slider_info = get_slider_info(ws)
        if slider_info and slider_info.get("found"):
            bg = slider_info.get("bgImg", {})
            jig = slider_info.get("jigsaw", {})
            if (bg and bg.get("w", 0) > 50 and bg.get("complete") and
                jig and jig.get("w", 0) > 10 and jig.get("complete")):
                log("  [滑块] 渲染完成（背景w=%.0f 拼图w=%.0f）" % (bg["w"], jig["w"]))
                break
    if not slider_info or not slider_info.get("found"):
        log("  [滑块] 未检测到滑块元素")
        return False

    # 仅在开始验证前激活窗口一次，避免每次刷新验证码都触发窗口状态变化
    if activate:
        bring_chrome_to_front()
        try:
            cdp_send(ws, "Page.bringToFront")
        except Exception:
            pass

    for attempt in range(max_retries):
        log("  [滑块] 尝试 %d/%d..." % (attempt + 1, max_retries))

        if attempt > 0:
            # 刷新验证码
            cdp_eval(ws, """(() => {
                const refresh = document.querySelector('.yidun_refresh');
                if (refresh) refresh.click();
                return 'ok';
            })()""")
            time.sleep(2)
            # 重新获取滑块信息
            for _ in range(10):
                time.sleep(0.5)
                slider_info = get_slider_info(ws)
                if slider_info and slider_info.get("found"):
                    bg = slider_info.get("bgImg", {})
                    if bg and bg.get("w", 0) > 50 and bg.get("complete"):
                        break
            if not slider_info or not slider_info.get("found"):
                log("  [滑块] 刷新后未检测到滑块")
                continue

        bg_info = slider_info.get("bgImg")
        jigsaw_info = slider_info.get("jigsaw")
        slider_rect = slider_info.get("slider")
        control_rect = slider_info.get("control")

        if not slider_rect or not control_rect:
            log("  [滑块] 滑块尺寸无效，跳过")
            continue

        # 下载背景图分析缺口
        gap_x = None
        if bg_info and bg_info.get("src") and bg_info.get("complete"):
            try:
                bg_data = download_image(bg_info["src"])
                jigsaw_w = int(jigsaw_info["w"]) if jigsaw_info else 51

                # 先用 numpy 方案
                gap_x = find_gap_numpy(bg_data, jigsaw_width=jigsaw_w,
                                       bg_display_width=int(bg_info["w"]), log=log)

                # numpy 失败时用 ddddocr 备用
                if gap_x is None and jigsaw_info and jigsaw_info.get("src"):
                    log("  [滑块] numpy 失败，尝试 ddddocr...")
                    jig_data = download_image(jigsaw_info["src"])
                    natural_w = bg_info.get("naturalW", 300)
                    bg_w = int(bg_info["w"])
                    scale = bg_w / natural_w if natural_w > 0 else 1.0
                    gap_x_raw = find_gap_ddddocr(bg_data, jig_data, log=log)
                    if gap_x_raw is not None:
                        gap_x = int(gap_x_raw * scale)
                        log("  [ddddocr] 缺口显示x=%d (scale=%.3f)" % (gap_x, scale))
            except Exception as e:
                log("  [滑块] 下载/分析失败: %s" % e)
        else:
            log("  [滑块] 背景图未就绪")

        if gap_x is None:
            log("  [滑块] 无法分析缺口，跳过")
            continue

        # 计算拖拽距离
        bg_x = bg_info["x"] if bg_info else slider_rect["x"]
        current_jigsaw_x = jigsaw_info["x"] if jigsaw_info else slider_rect["x"]
        target_jigsaw_x = bg_x + gap_x
        drag_dist = target_jigsaw_x - current_jigsaw_x

        # 限制拖拽距离
        max_drag = control_rect["w"] - slider_rect["w"] - 2
        if drag_dist > max_drag:
            drag_dist = max_drag
        if drag_dist < 10:
            drag_dist = 50

        start_x = slider_rect["x"] + slider_rect["w"] / 2
        start_y = slider_rect["y"] + slider_rect["h"] / 2

        log("  [滑块] 缺口=%d 拖拽距离=%d (attempt %d)" % (gap_x, drag_dist, attempt + 1))

        simulate_drag(ws, start_x, start_y, drag_dist)

        # 等待验证结果
        time.sleep(2)
        v = check_verify(ws)
        if not v.get("type"):
            log("  [滑块] >>> 验证通过！<<<")
            return True

        # 检查滑块是否还在
        slider_still = get_slider_info(ws)
        if not slider_still or not slider_still.get("found"):
            time.sleep(1)
            v = check_verify(ws)
            if not v.get("type"):
                log("  [滑块] >>> 验证通过！<<<")
                return True

        log("  [滑块] 验证未通过，重试...")

    return False


def handle_verify(ws, timeout=120, log=print, reconnect_fn=None):
    """处理验证弹窗：自动验证(numpy+ddddocr) → 失败等待手动。

    参数：
        ws: WebSocket 连接
        timeout: 手动验证等待超时（秒）
        log: 日志输出函数
        reconnect_fn: 断线重连函数（可选），调用后返回新的 ws 或 None

    返回 (success, ws)：
        success=True=验证通过，False=超时未通过
        ws=可能更新后的 WebSocket 连接（重连后替换）
    """
    v = check_verify(ws)
    vtype = v.get("type", "")
    log("")
    log("=" * 64)
    log("[!] 检测到验证弹窗（%s: %s）" % (vtype, v.get("hit", "")))

    if vtype in ("yidun_slider", "captcha"):
        # 激活窗口后自动验证（solve_slider 内部不再重复激活）
        bring_chrome_to_front()
        try:
            cdp_send(ws, "Page.bringToFront")
        except Exception:
            pass
        log("[*] 自动验证（numpy主 + ddddocr备）...")
        if solve_slider(ws, max_retries=6, log=log, activate=False):
            log("[OK] 自动验证通过，继续。")
            log("")
            return True, ws
        log("[!] 自动验证失败，转为等待手动完成")

    log("    >>> 请到 Chrome 窗口手动完成验证 <<<")
    log("    脚本将等待最多 %d 秒，每 2 秒检测一次..." % timeout)
    log("=" * 64)

    deadline = time.time() + timeout
    last_tick = time.time()
    while time.time() < deadline:
        time.sleep(2)
        try:
            v = check_verify(ws)
        except Exception:
            # 连接可能断开，尝试重连
            if reconnect_fn:
                new_ws = reconnect_fn()
                if new_ws:
                    ws = new_ws
                    try:
                        v = check_verify(ws)
                    except Exception:
                        v = {"type": "unknown"}
                else:
                    v = {"type": "unknown"}
            else:
                v = {"type": "unknown"}
        if not v.get("type"):
            log("[OK] 验证弹窗已消失，继续。")
            log("")
            return True, ws
        if time.time() - last_tick >= 10:
            remain = int(deadline - time.time())
            log("    ... 仍在等待（剩余 %d 秒）" % remain)
            last_tick = time.time()

    log("[!] 等待超时，验证弹窗仍未消失。")
    return False, ws

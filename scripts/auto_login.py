# -*- coding: utf-8 -*-
"""自动登录药师帮 + 网易易盾拼图滑块验证（CDP 直连版）
恢复自 GitHub 原始版本 https://github.com/wynet/ysb-wholesale-scraper

1. 填写账号密码登录
2. 下载拼图背景图和滑块图
3. 用图像处理(PIL+numpy)找到缺口位置
4. CDP 模拟人类拖拽到正确位置（ease-out 轨迹 + y 轴抖动）
5. 失败自动重试（刷新验证码重新分析）

运行方式（需已启动 Chrome 9222 调试端口，且有一个 dian.ysbang.cn 标签页）：
    python auto_login.py <手机号> <密码>
依赖：websocket-client, Pillow, numpy
    pip install websocket-client Pillow numpy
退出码：
    0 = 登录成功（或已登录）
    1 = 登录失败（滑块验证未通过或其他错误）
"""
import json, re, time, urllib.request, websocket, sys, io, base64
import ctypes, ctypes.wintypes

CDP_URL = "http://127.0.0.1:9222"


# ====================== Windows 窗口激活 ======================
try:
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


def bring_chrome_to_front():
    """用 Windows API 激活 Chrome 窗口"""
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
        _user32.SetForegroundWindow(found[0])
        time.sleep(0.5)
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
    # 找不到药帮 tab，用任意 page tab
    for t in get_tabs():
        if t.get("type") == "page":
            return t
    return None


# 全局消息 ID 计数器（避免冲突）
_global_mid = [0]


def cdp_send(ws, method, params=None):
    _global_mid[0] += 1
    msg_id = _global_mid[0]
    msg = {"id": msg_id, "method": method}
    if params:
        msg["params"] = params
    ws.send(json.dumps(msg))
    while True:
        resp = json.loads(ws.recv())
        if resp.get("id") == msg_id:
            return resp


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


# ====================== 滑块检测与图像分析 ======================
def get_slider_info(ws):
    """获取易盾滑块的精确位置（滑块按钮、轨道、背景图、拼图块）"""
    js = """(() => {
        const slider = document.querySelector('.yidun_slider');
        const control = document.querySelector('.yidun_control');
        const bgImg = document.querySelector('.yidun_bg-img');
        const jigsaw = document.querySelector('.yidun_jigsaw');
        const panel = document.querySelector('.yidun_panel-placeholder');
        if (!slider || !control) return JSON.stringify({found: false});
        const sRect = slider.getBoundingClientRect();
        const cRect = control.getBoundingClientRect();
        if (sRect.width < 5 || sRect.height < 5 || cRect.width < 50) {
            return JSON.stringify({found: false, reason: 'not_rendered'});
        }
        const bRect = bgImg ? bgImg.getBoundingClientRect() : null;
        const jRect = jigsaw ? jigsaw.getBoundingClientRect() : null;
        const pRect = panel ? panel.getBoundingClientRect() : null;
        return JSON.stringify({
            found: true,
            slider: {x: sRect.x, y: sRect.y, w: sRect.width, h: sRect.height},
            control: {x: cRect.x, y: cRect.y, w: cRect.width, h: cRect.height},
            bgImg: bRect ? {x: bRect.x, y: bRect.y, w: bRect.width, h: bRect.height, src: bgImg.src, complete: bgImg.complete, naturalW: bgImg.naturalWidth} : null,
            jigsaw: jRect ? {x: jRect.x, y: jRect.y, w: jRect.width, h: jRect.height, src: jigsaw.src, complete: jigsaw.complete} : null,
            panel: pRect ? {x: pRect.x, y: pRect.y, w: pRect.width, h: pRect.height} : null,
        });
    })()"""
    r = cdp_eval(ws, js)
    return json.loads(r) if r else {"found": False}


def find_gap_position(bg_img_data, jigsaw_width=51, bg_display_width=270):
    """用图像分析找到拼图缺口位置。
    下载背景图，分析每列的水平梯度（像素差异），找到缺口左右边缘。
    缺口处颜色突变明显，梯度峰值成对出现（间距约等于拼图块宽度）。
    参数：
        bg_img_data: 背景图的二进制数据
        jigsaw_width: 拼图块的显示宽度（像素）
        bg_display_width: 背景图的显示宽度（像素）
    返回：
        缺口左边缘在显示尺寸中的 x 坐标（像素），或 None
    """
    try:
        from PIL import Image
        import numpy as np
        img = Image.open(io.BytesIO(bg_img_data))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        arr = np.array(img)
        h, w = arr.shape[:2]
        # 图片实际宽度可能和显示宽度不同，需要缩放
        scale = bg_display_width / w if w != bg_display_width else 1.0
        print("  图片实际宽=%d 显示宽=%d 缩放比=%.3f" % (w, bg_display_width, scale))
        # 计算每列的梯度（水平方向差异）
        col_diff = np.zeros(w)
        for x in range(1, w):
            diff = np.mean(np.abs(arr[:, x].astype(int) - arr[:, x-1].astype(int)))
            col_diff[x] = diff
        # 在缺口区域寻找梯度最大的位置（跳过左侧拼图块区域）
        jigsaw_w_actual = int(jigsaw_width / scale)  # 拼图块在原图中的宽度
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
            # 找到成对的峰值（缺口左右边缘，间距约等于拼图块宽度）
            gap_x = peaks[0]
            best_score = 0
            for i in range(len(peaks)):
                for j in range(i+1, len(peaks)):
                    dist = peaks[j] - peaks[i]
                    # 缺口宽度应接近拼图块宽度
                    if abs(dist - jigsaw_w_actual) < 8:
                        score = col_diff[peaks[i]] + col_diff[peaks[j]]
                        if score > best_score:
                            best_score = score
                            gap_x = peaks[i]
            # 缩放到显示尺寸
            gap_x_display = int(gap_x * scale)
            print("  图像分析: 缺口位置 原图x=%d -> 显示x=%d" % (gap_x, gap_x_display))
            return gap_x_display
        else:
            print("  图像分析: 未找到明显缺口")
            return None
    except Exception as e:
        print("  图像分析异常: %s" % e)
        return None


def find_gap_ddddocr(bg_img_data, jigsaw_img_data):
    """用 ddddocr 识别缺口位置（备用方案）"""
    try:
        import ddddocr
        det = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
        result = det.slide_match(jigsaw_img_data, bg_img_data)
        gap_x = result.get("target", [0])[0]
        print("  [ddddocr] 缺口位置 x=%d" % gap_x)
        return gap_x
    except Exception as e:
        print("  [ddddocr] 异常: %s" % e)
        return None


def download_image(url, timeout=10):
    """下载图片"""
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ====================== 人类轨迹模拟拖拽 ======================
def simulate_drag(ws, start_x, start_y, drag_dist):
    """模拟人类拖拽滑块。
    - 先慢速靠近滑块
    - 按下后 ease-out 轨迹拖拽（先快后慢）
    - y 轴微小抖动（模拟手不稳）
    - 到终点后微小回弹再释放
    """
    end_x = start_x + drag_dist
    end_y = start_y
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
        # y 轴微小抖动
        y = start_y + (i % 3 - 1) * 0.4
        cdp_mouse(ws, x, y)
        # 速度变化（越接近终点越慢）
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
def solve_slider(ws, slider_info, max_retries=4, use_ddddocr=False):
    """执行完整的滑块验证流程：分析缺口 -> 计算距离 -> 拖拽 -> 检查结果。
    失败则刷新验证码重试，最多 max_retries 次。
    返回 True=验证成功，False=失败。
    """
    for attempt in range(max_retries):
        bg_info = slider_info.get("bgImg")
        jigsaw_info = slider_info.get("jigsaw")
        slider_rect = slider_info["slider"]
        control_rect = slider_info["control"]

        if attempt > 0:
            # 刷新验证码
            print("  刷新验证码重试 (attempt %d)..." % (attempt + 1))
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
                print("  刷新后未检测到滑块")
                continue
            bg_info = slider_info.get("bgImg")
            jigsaw_info = slider_info.get("jigsaw")
            slider_rect = slider_info.get("slider")
            control_rect = slider_info.get("control")
            if not slider_rect or not control_rect:
                print("  滑块尺寸无效，跳过")
                continue

        # 下载背景图分析缺口
        gap_x = None
        if bg_info and bg_info.get("src") and bg_info.get("complete"):
            try:
                bg_data = download_image(bg_info["src"])
                jigsaw_w = int(jigsaw_info["w"]) if jigsaw_info else 51

                if use_ddddocr and jigsaw_info and jigsaw_info.get("src"):
                    # ddddocr 方案
                    jig_data = download_image(jigsaw_info["src"])
                    natural_w = bg_info.get("naturalW", 300)
                    bg_w = int(bg_info["w"])
                    scale = bg_w / natural_w if natural_w > 0 else 1.0
                    gap_x_raw = find_gap_ddddocr(bg_data, jig_data)
                    if gap_x_raw is not None:
                        gap_x = int(gap_x_raw * scale)
                        print("  [ddddocr] 缺口显示x=%d (scale=%.3f)" % (gap_x, scale))
                else:
                    # numpy 方案
                    gap_x = find_gap_position(bg_data, jigsaw_width=jigsaw_w,
                                              bg_display_width=int(bg_info["w"]))
            except Exception as e:
                print("  下载/分析失败: %s" % e)
        else:
            print("  背景图未就绪: src=%s complete=%s" % (
                bg_info.get("src", "")[:50] if bg_info else "None",
                bg_info.get("complete") if bg_info else "None"))

        if gap_x is None:
            print("  无法分析缺口，跳过本次")
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

        print("  缺口=%d 拖拽距离=%d (attempt %d)" % (gap_x, drag_dist, attempt + 1))
        simulate_drag(ws, start_x, start_y, drag_dist)

        # 等待验证结果
        time.sleep(2)
        h = cdp_eval(ws, "location.hash") or ""
        txt = cdp_eval(ws, "document.body.innerText.substring(0,300)") or ""
        if "账户登录" not in txt and "请完成安全验证" not in txt:
            print("  >>> 滑块验证通过！<<<")
            return True

        # 检查滑块是否还在
        slider_still = get_slider_info(ws)
        if not slider_still or not slider_still.get("found"):
            # 滑块消失，可能成功了
            time.sleep(1)
            txt = cdp_eval(ws, "document.body.innerText.substring(0,200)") or ""
            if "账户登录" not in txt and "请完成安全验证" not in txt:
                print("  >>> 滑块验证通过！<<<")
                return True

    return False


# ====================== 主流程 ======================
def main():
    phone = sys.argv[1] if len(sys.argv) > 1 else ""
    password = sys.argv[2] if len(sys.argv) > 2 else ""
    use_ddddocr = "--ddddocr" in sys.argv

    if not phone or not password:
        print("用法: python auto_login.py <手机号> <密码> [--ddddocr]")
        print("  --ddddocr  使用 ddddocr 识别缺口（默认用 numpy 梯度分析）")
        sys.exit(1)

    tab = find_tab()
    if not tab:
        print("ERROR: 没有找到可用的 Chrome 标签页")
        sys.exit(1)

    print("使用 tab: %s" % tab["url"][:80])
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)

    # 将标签页切换到前台（否则 hidden=true 时 getBoundingClientRect 返回 0）
    bring_chrome_to_front()
    try:
        cdp_send(ws, "Page.bringToFront")
        time.sleep(1)
    except:
        pass

    # 检查是否已登录
    h = cdp_eval(ws, "location.hash") or ""
    txt = cdp_eval(ws, "document.body.innerText.substring(0,300)") or ""
    if "账户登录" not in txt and "/login" not in h:
        print("已登录！当前页面: %s" % h[:60])
        ws.close()
        sys.exit(0)

    # 确保在登录页
    if "/login" not in h:
        cdp_eval(ws, "location.hash = '#/login'")
        time.sleep(3)

    print("=== 登录页面就绪 ===")

    # 关闭可能存在的旧滑块
    cdp_eval(ws, """(() => {
        const close = document.querySelector('.yidun_modal__close');
        if (close) close.click();
        return 'ok';
    })()""")
    time.sleep(1)

    # Step 1: 填写手机号
    print("[1] 填写手机号: %s***%s" % (phone[:3], phone[-4:]))
    cdp_eval(ws, """(() => {
        const input = document.querySelector('input[placeholder=手机号码], input[type=text]');
        if (!input) return 'no_input';
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, '%s');
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        return 'ok';
    })()""" % phone)

    # Step 2: 填写密码
    print("[2] 填写密码")
    cdp_eval(ws, """(() => {
        const input = document.querySelector('input[placeholder=密码], input[type=password]');
        if (!input) return 'no_input';
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, '%s');
        input.dispatchEvent(new Event('input', {bubbles: true}));
        input.dispatchEvent(new Event('change', {bubbles: true}));
        return 'ok';
    })()""" % password)

    time.sleep(0.3)

    # Step 3: 勾选自动登录
    print("[3] 勾选5天自动登录")
    cdp_eval(ws, """(() => {
        const cb = document.querySelector('#rememberMe, .el-checkbox');
        if (cb && !cb.classList.contains('is-checked')) cb.click();
        return 'ok';
    })()""")
    time.sleep(0.3)

    # Step 4: 点击登录
    print("[4] 点击登录")
    cdp_eval(ws, """(() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText||'').trim() === '登录');
        if (btn) { btn.click(); return 'ok'; }
        return 'no_btn';
    })()""")

    # Step 5: 等待滑块出现
    print("[5] 等待易盾滑块...")
    bring_chrome_to_front()
    try:
        cdp_send(ws, "Page.bringToFront")
    except:
        pass
    slider_info = None
    for wait in range(15):
        time.sleep(0.5)
        slider_info = get_slider_info(ws)
        if slider_info and slider_info.get("found"):
            print("  滑块已出现！")
            break
        # 检查是否直接登录成功（无滑块）
        h = cdp_eval(ws, "location.hash") or ""
        txt = cdp_eval(ws, "document.body.innerText.substring(0,200)") or ""
        if "账户登录" not in txt and "请完成安全验证" not in txt:
            print("  直接登录成功（无滑块）！")
            ws.close()
            sys.exit(0)

    if not slider_info or not slider_info.get("found"):
        print("ERROR: 未检测到滑块")
        ws.close()
        sys.exit(1)

    # 等待验证码图片加载完成（同时确保元素尺寸>0，排除 hidden 状态）
    print("[5.5] 等待验证码图片加载...")
    for _ in range(20):
        bg = slider_info.get("bgImg", {})
        jig = slider_info.get("jigsaw", {})
        sr = slider_info.get("slider", {})
        if (bg and bg.get("w", 0) > 50 and bg.get("complete") and
            jig and jig.get("w", 0) > 10 and jig.get("complete") and
            sr and sr.get("w", 0) > 5):
            print("  验证码图片已加载 (滑块w=%.0f 背景w=%.0f)" % (sr["w"], bg["w"]))
            break
        time.sleep(0.5)
        slider_info = get_slider_info(ws)

    # Step 6-8: 分析缺口 -> 拖拽 -> 验证
    print("\n=== 滑块信息 ===")
    sr = slider_info["slider"]
    cr = slider_info["control"]
    print("  滑块按钮: x=%.0f y=%.0f w=%.0f h=%.0f" % (sr["x"], sr["y"], sr["w"], sr["h"]))
    print("  轨道: x=%.0f y=%.0f w=%.0f h=%.0f" % (cr["x"], cr["y"], cr["w"], cr["h"]))
    if slider_info.get("bgImg"):
        bg = slider_info["bgImg"]
        print("  背景图: x=%.0f y=%.0f w=%.0f h=%.0f src=%s..." % (
            bg["x"], bg["y"], bg["w"], bg["h"], bg.get("src", "")[:60]))
    if slider_info.get("jigsaw"):
        jig = slider_info["jigsaw"]
        print("  拼图块: x=%.0f y=%.0f w=%.0f h=%.0f" % (jig["x"], jig["y"], jig["w"], jig["h"]))

    method = "ddddocr" if use_ddddocr else "numpy梯度分析"
    print("\n[6] 开始验证（%s，最多重试6次）..." % method)

    success = solve_slider(ws, slider_info, max_retries=6, use_ddddocr=use_ddddocr)

    if success:
        print("\n>>> 登录成功！<<<")
        time.sleep(2)
        h = cdp_eval(ws, "location.hash") or ""
        print("当前页面: %s" % h[:60])
        ws.close()
        sys.exit(0)
    else:
        print("\n>>> 滑块验证未通过 <<<")
        ws.close()
        sys.exit(1)


if __name__ == "__main__":
    main()

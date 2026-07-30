# -*- coding: utf-8 -*-
"""自动登录药师帮 + 网易易盾拼图滑块验证（CDP 直连版）

滑块验证逻辑统一由 ysb_common.py 公共模块提供，避免重复维护。

运行方式（需已启动 Chrome 9222 调试端口，且有一个 dian.ysbang.cn 标签页）：
    python auto_login.py <手机号> <密码>
依赖：websocket-client, Pillow, numpy
    pip install websocket-client Pillow numpy
退出码：
    0 = 登录成功（或已登录）
    1 = 登录失败（滑块验证未通过或其他错误）
"""
import sys, time, websocket
import ysb_common


# ====================== 主流程 ======================
def main():
    phone = sys.argv[1] if len(sys.argv) > 1 else ""
    password = sys.argv[2] if len(sys.argv) > 2 else ""

    if not phone or not password:
        print("用法: python auto_login.py <手机号> <密码>")
        sys.exit(1)

    tab = ysb_common.find_tab()
    if not tab:
        print("ERROR: 没有找到可用的 Chrome 标签页")
        sys.exit(1)

    print("使用 tab: %s" % tab["url"][:80])
    ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)

    # 将标签页切换到前台（否则 hidden=true 时 getBoundingClientRect 返回 0）
    ysb_common.bring_chrome_to_front()
    try:
        ysb_common.cdp_send(ws, "Page.bringToFront")
        time.sleep(1)
    except Exception:
        pass

    # 检查是否已登录
    h = ysb_common.cdp_eval(ws, "location.hash") or ""
    txt = ysb_common.cdp_eval(ws, "document.body.innerText.substring(0,300)") or ""
    if "账户登录" not in txt and "/login" not in h:
        print("已登录！当前页面: %s" % h[:60])
        ws.close()
        sys.exit(0)

    # 确保在登录页
    if "/login" not in h:
        ysb_common.cdp_eval(ws, "location.hash = '#/login'")
        time.sleep(3)

    print("=== 登录页面就绪 ===")

    # 关闭可能存在的旧滑块
    ysb_common.cdp_eval(ws, """(() => {
        const close = document.querySelector('.yidun_modal__close');
        if (close) close.click();
        return 'ok';
    })()""")
    time.sleep(1)

    # Step 1: 填写手机号
    print("[1] 填写手机号: %s***%s" % (phone[:3], phone[-4:]))
    ysb_common.cdp_eval(ws, """(() => {
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
    ysb_common.cdp_eval(ws, """(() => {
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
    ysb_common.cdp_eval(ws, """(() => {
        const cb = document.querySelector('#rememberMe, .el-checkbox');
        if (cb && !cb.classList.contains('is-checked')) cb.click();
        return 'ok';
    })()""")
    time.sleep(0.3)

    # Step 4: 点击登录
    print("[4] 点击登录")
    ysb_common.cdp_eval(ws, """(() => {
        const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText||'').trim() === '登录');
        if (btn) { btn.click(); return 'ok'; }
        return 'no_btn';
    })()""")

    # Step 5: 等待滑块出现
    print("[5] 等待易盾滑块...")
    ysb_common.bring_chrome_to_front()
    try:
        ysb_common.cdp_send(ws, "Page.bringToFront")
    except Exception:
        pass

    slider_found = False
    for wait in range(15):
        time.sleep(0.5)
        slider_info = ysb_common.get_slider_info(ws)
        if slider_info and slider_info.get("found"):
            print("  滑块已出现！")
            slider_found = True
            break
        # 检查是否直接登录成功（无滑块）
        h = ysb_common.cdp_eval(ws, "location.hash") or ""
        txt = ysb_common.cdp_eval(ws, "document.body.innerText.substring(0,200)") or ""
        if "账户登录" not in txt and "请完成安全验证" not in txt:
            print("  直接登录成功（无滑块）！")
            ws.close()
            sys.exit(0)

    if not slider_found:
        print("ERROR: 未检测到滑块")
        ws.close()
        sys.exit(1)

    # Step 6: 调用公共模块执行滑块验证（numpy主 + ddddocr备，自动重试）
    print("\n[6] 开始验证（numpy主 + ddddocr备，最多重试6次）...")
    success = ysb_common.solve_slider(ws, max_retries=6, log=print)

    if success:
        print("\n>>> 登录成功！<<<")
        time.sleep(2)
        h = ysb_common.cdp_eval(ws, "location.hash") or ""
        print("当前页面: %s" % h[:60])
        ws.close()
        sys.exit(0)
    else:
        print("\n>>> 滑块验证未通过 <<<")
        ws.close()
        sys.exit(1)


if __name__ == "__main__":
    main()

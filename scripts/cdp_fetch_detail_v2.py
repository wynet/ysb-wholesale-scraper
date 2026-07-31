# -*- coding: utf-8 -*-
"""CDP 直连版详情页采集脚本 v2

数据解析、正则常量、JS 模板由 ysb_parser.py 公共模块提供。
滑块验证逻辑由 ysb_common.py 公共模块提供，与 auto_login.py 共用同一套代码。

用法: python cdp_fetch_detail_v2.py --input vuex_raw.json --existing detail_data.json --top-n 0
输出: JSON 到 stdout，日志到 stderr
"""
import json, time, sys, argparse, os
import websocket
import ysb_common
import ysb_parser

# ====================== 全局 WebSocket ======================
ws_global = None

def js(expr):
    """CDP 执行 JS 的快捷封装"""
    return ysb_common.cdp_eval(ws_global, expr)

def reconnect():
    """断线重连，返回新的 ws 或 None"""
    global ws_global
    try:
        if ws_global:
            ws_global.close()
    except Exception:
        pass
    tab = ysb_common.find_tab()
    if not tab:
        sys.stderr.write("[重连] 没有 Chrome 标签页\n")
        return None
    ws_global = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
    sys.stderr.write("[重连] 成功\n")
    return ws_global

def check_verify():
    """检测验证弹窗"""
    return ysb_common.check_verify(ws_global)

def _log(msg):
    """带 flush 的日志函数"""
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()

def handle_verify_during_scrape(timeout=120):
    """处理验证弹窗：自动验证(numpy+ddddocr) → 失败等待手动（含断线重连）"""
    global ws_global
    success, ws_global = ysb_common.handle_verify(
        ws_global, timeout=timeout,
        log=_log,
        reconnect_fn=reconnect
    )
    return success

# ====================== 参数解析 ======================
def parse_args():
    p = argparse.ArgumentParser(description="药帮详情页采集(CDP直连+公共滑块模块)")
    p.add_argument("--input", default="vuex_raw.json", help="vuex_raw.json 路径")
    p.add_argument("--existing", default="detail_data.json", help="已有详情路径(断点续传)")
    p.add_argument("--top-n", type=int, default=0, help="只抓前N个(0=全量)")
    p.add_argument("--brand", default="", help="品牌名（用于日志标识）")
    return p.parse_args()

# ====================== CDP 专用辅助 ======================
def current_hash_wid():
    """获取当前页面 hash 中的 wholesaleid（CDP 版，含异常处理）"""
    return ysb_parser.current_hash_wid(js)

def wait_detail(wid, timeout=15):
    """等待详情页加载完成（CDP 版，含断线重连）"""
    for _ in range(timeout):
        time.sleep(1)
        try:
            if current_hash_wid() == str(wid):
                txt = js("document.body.innerText") or ""
                if ysb_parser.has_product_data(txt):
                    return txt
        except Exception:
            if not reconnect():
                time.sleep(2)
                continue
    try:
        txt = js("document.body.innerText") or ""
    except Exception:
        txt = ""
    return txt

# ====================== 主流程 ======================
def main():
    global ws_global
    opts = parse_args()
    INPUT_JSON = opts.input
    EXISTING_JSON = opts.existing
    TOP_N = opts.top_n
    BRAND = opts.brand or "unknown"

    sys.stderr.write("=" * 64 + "\n")
    sys.stderr.write("[%s] 详情页采集 v2 (公共解析+滑块模块)\n" % BRAND)
    sys.stderr.write("=" * 64 + "\n")

    # 读取列表数据
    raw = json.load(open(INPUT_JSON, encoding="utf-8-sig"))
    all_wids = sorted(set(str(r.get('wholesaleid')) for r in raw if r.get('wholesaleid')))

    # 加载已有详情
    existing = {}
    try:
        existing = json.load(open(EXISTING_JSON, encoding="utf-8-sig"))
        if not isinstance(existing, dict):
            existing = {}
    except Exception:
        existing = {}

    # 清除失败记录（调用公共函数）
    WIDS = ysb_parser.cleanup_failed_wids(existing, all_wids)
    if TOP_N > 0:
        WIDS = WIDS[:TOP_N]
    sys.stderr.write("[%s] 总 wid: %d | 已有有效详情: %d | 待抓: %d%s\n" % (
        BRAND, len(all_wids), len(all_wids) - len(WIDS), len(WIDS),
        " (top-%d)" % TOP_N if TOP_N > 0 else ""))

    # wid 信息（调用公共函数）
    wid_info = ysb_parser.build_wid_info(raw)

    # 连接 Chrome
    tab = ysb_common.find_tab()
    if not tab:
        sys.stderr.write("ERROR: 没有 Chrome 标签页\n")
        print(json.dumps(existing, ensure_ascii=False, indent=1))
        sys.exit(1)
    sys.stderr.write("使用 tab: %s\n" % tab["url"][:80])
    ws_global = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)

    # 确认 Vue Router
    _router_ok = js(r"""(()=>{const a=document.querySelector('#app');return (a&&a.__vue__&&a.__vue__.$router)?'ok':'no_router';})()""")
    if _router_ok != 'ok':
        sys.stderr.write("Vue Router 不可用，尝试导航到首页...\n")
        ysb_common.cdp_eval(ws_global, "location.href = 'https://dian.ysbang.cn/#/home'")
        time.sleep(5)
        _router_ok = js(r"""(()=>{const a=document.querySelector('#app');return (a&&a.__vue__&&a.__vue__.$router)?'ok':'no_router';})()""")
        if _router_ok != 'ok':
            sys.stderr.write("ERROR: Vue Router 仍不可用\n")
            print(json.dumps(existing, ensure_ascii=False, indent=1))
            sys.exit(1)
    sys.stderr.write("Vue Router 就绪，开始 SPA 内导航采集\n")

    # 入口页检测滑块
    v = check_verify()
    if v.get("type"):
        sys.stderr.write("入口页检测到验证弹窗，先处理...\n")
        if not handle_verify_during_scrape(timeout=120):
            sys.stderr.write("入口页验证未解决，退出。\n")
            print(json.dumps(existing, ensure_ascii=False, indent=1))
            sys.exit(1)
        time.sleep(2)

    # 逐个采集
    out = {}
    consecutive_fails = 0
    MAX_CONSECUTIVE_FAILS = 5
    save_interval = 10

    for idx, wid in enumerate(WIDS):
        info = wid_info.get(wid, {})
        is_group_buy = info.get('is_group_buy', True)
        name_short = (info.get('name', '') or '')[:30]
        txt = ""
        success = False

        # 优先使用采集到的 detail_url 解析路径和参数（不自行判断 URL 类型）
        collected = ysb_parser.parse_detail_url(info.get('detail_url', ''))
        if collected:
            nav_path = collected['path']
            primary_assemble = collected['isAssemble'] or ("true" if is_group_buy else "false")
            primary_scene = collected['scene'] or "0"
        else:
            # 回退: 用 busiScope/sourceType 判断路径
            is_instrument = info.get('busiScope') in (9, '9') or info.get('sourceType') in (1, '1')
            nav_path = '/instrument/drugDetail' if is_instrument else '/drugInfo'
            primary_assemble = "true" if is_group_buy else "false"
            primary_scene = "0"

        # 主路由用采集参数，回退路由交换 isAssemble
        fallback_assemble = "false" if primary_assemble == "true" else "true"
        routes_to_try = [(nav_path, primary_assemble, primary_scene, "采集"),
                         (nav_path, fallback_assemble, "0", "回退")]

        for route_idx, (path, is_assemble, scene, label) in enumerate(routes_to_try):
            try:
                js(ysb_parser.NAV_HOME_JS)
                time.sleep(2)
                try:
                    js(ysb_parser.DISMISS_JS)
                    time.sleep(1)
                except Exception:
                    pass

                res = js(ysb_parser.NAVIGATE_JS % (path, wid, is_assemble, scene))
                if res and res.startswith('error'):
                    sys.stderr.write("wid=%s router.push 异常(%s): %s\n" % (wid, label, res))

                txt = wait_detail(wid, timeout=12 if route_idx == 0 else 8)

                try:
                    js(ysb_parser.DISMISS_JS)
                    time.sleep(0.5)
                except Exception:
                    pass

                v = check_verify()
                if v.get("type"):
                    sys.stderr.write("[%d/%d] wid=%s 采集途中触发验证弹窗(%s)\n" % (idx + 1, len(WIDS), wid, v.get("type")))
                    if handle_verify_during_scrape(timeout=120):
                        time.sleep(1)
                        txt = js("document.body.innerText") or ""
                    else:
                        sys.stderr.write("wid=%s 验证未解决，跳过\n" % wid)
                        txt = ""
                        continue
                else:
                    txt = js("document.body.innerText") or ""

                if ysb_parser.has_product_data(txt):
                    success = True
                    break
            except Exception as e:
                sys.stderr.write("wid=%s 路由%s 异常: %s\n" % (wid, label, e))
                if "Connection" in str(e) or "timed out" in str(e) or "Broken pipe" in str(e):
                    sys.stderr.write("连接异常，尝试重连...\n")
                    if reconnect():
                        time.sleep(2)
                        continue
                txt = ""
                continue

        # location.href 兜底（使用采集到的路径和参数）
        if not success:
            sys.stderr.write("wid=%s SPA 导航失败，尝试 location.href 兜底\n" % wid)
            try:
                ysb_common.cdp_eval(ws_global,
                    "location.href = 'https://dian.ysbang.cn/#%s?wholesaleid=%s&isAssemble=%s&scene=%s&trafficType=1'"
                    % (nav_path, wid, primary_assemble, primary_scene))
                time.sleep(5)
                js(ysb_parser.DISMISS_JS)
                time.sleep(1)
                txt = js("document.body.innerText") or ""
                v = check_verify()
                if v.get("type"):
                    if handle_verify_during_scrape(timeout=120):
                        time.sleep(1)
                        txt = js("document.body.innerText") or ""
                if ysb_parser.has_product_data(txt):
                    success = True
            except Exception as e:
                sys.stderr.write("wid=%s location.href 兜底也失败: %s\n" % (wid, e))
                if reconnect():
                    pass

        if success:
            consecutive_fails = 0
        else:
            consecutive_fails += 1
            sys.stderr.write("wid=%s [%s] 全部重试失败（连续失败 %d 次）\n" % (wid, name_short, consecutive_fails))

        out[wid] = ysb_parser.parse_detail(txt)
        existing[wid] = out[wid]
        gtype = "拼团" if is_group_buy else "普通"
        sys.stderr.write("[%s %d/%d] wid=%s [%s] %s -> price=%s paid=%s%s stores=%s recs=%s 7d=%s large=%d笔/%d%s\n" % (
            BRAND, idx + 1, len(WIDS), wid, name_short, gtype,
            out[wid].get('detail_price'), out[wid].get('paid_units'), out[wid].get('paid_unit'),
            out[wid].get('stores_joined'), out[wid].get('purchase_records'),
            out[wid].get('last_7_days_sales'),
            out[wid].get('large_orders_count') or 0,
            out[wid].get('large_orders_total_qty') or 0,
            out[wid].get('paid_unit') or ''))

        # 周期保存
        if (idx + 1) % save_interval == 0 or (idx + 1) == len(WIDS):
            sys.stderr.write("--- [%s] 已采集 %d/%d ---\n" % (BRAND, len(existing), len(all_wids)))
            try:
                tmp_path = EXISTING_JSON + ".tmp"
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, ensure_ascii=False, indent=1)
                os.replace(tmp_path, EXISTING_JSON)
                sys.stderr.write("    已保存到 %s\n" % EXISTING_JSON)
            except Exception as e:
                sys.stderr.write("    保存失败: %s\n" % e)

        time.sleep(2)
        if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
            sys.stderr.write("连续 %d 次失败，Chrome 可能已崩溃。保存已有数据并退出。\n" % MAX_CONSECUTIVE_FAILS)
            break

    try:
        ws_global.close()
    except Exception:
        pass
    sys.stderr.write("DONE [%s]: %d details (新增 %d，累计 %d)\n" % (BRAND, len(existing), len(out), len(existing)))
    print(json.dumps(existing, ensure_ascii=False, indent=1))

if __name__ == "__main__":
    main()

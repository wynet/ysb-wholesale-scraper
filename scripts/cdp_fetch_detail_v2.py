# -*- coding: utf-8 -*-
"""CDP 直连版详情页采集脚本 v2

滑块验证逻辑统一由 ysb_common.py 公共模块提供，与 auto_login.py 共用同一套代码：
  1. numpy梯度分析识别缺口（主方案）
  2. ddddocr识别缺口（备用方案）
  3. CDP Input.dispatchMouseEvent 模拟拖拽（ease-out轨迹+y轴抖动）
  4. Windows API激活Chrome窗口（解决hidden标签页坐标为0的问题）
  5. 断线自动重连

用法: python cdp_fetch_detail_v2.py --input vuex_raw.json --existing detail_data.json --top-n 0
输出: JSON 到 stdout，日志到 stderr
"""
import json, base64, re, time, datetime, sys, argparse, os
import websocket
import ysb_common

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

# ====================== 解析逻辑 ======================
def dec(tok):
    if not tok: return None
    b = base64.b64decode(tok)
    for mk in (b'\x12', b'\x3a'):
        i = b.find(mk)
        while i != -1:
            if i + 1 < len(b):
                L = b[i + 1]
                if 1 <= L <= 12:
                    s = b[i + 2:i + 2 + L]
                    if re.match(rb'^\d+\.\d{1,2}$', s) or re.match(rb'^\d+$', s):
                        return float(s.decode('latin-1'))
            i = b.find(mk, i + 1)
    return None

_NOW = None
def _get_now():
    global _NOW
    if _NOW is None:
        _NOW = datetime.datetime.now()
    return _NOW

def parse_time(s):
    s = (s or "").strip()
    if not s: return None
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', s)
    if m:
        return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    units = {"分钟": 0, "小时": 0, "天": 0}
    found = False
    for unit_key in units:
        m = re.search(r'(\d+)\s*' + unit_key + '前', s)
        if m:
            units[unit_key] = int(m.group(1))
            found = True
    if not found: return None
    now = _get_now()
    return now - datetime.timedelta(minutes=units["分钟"], hours=units["小时"], days=units["天"])

def is_last_7_days(dt):
    if dt is None: return False
    now = _get_now()
    delta = now - dt
    return datetime.timedelta(0) <= delta <= datetime.timedelta(days=7)

UNIT_RE = r'([盒瓶支包袋片套罐条贴副双个])'
def parse_detail(txt):
    d = {}
    m = re.search(r'成团价\s*¥\s*([\d.]+)', txt)
    if m:
        d['detail_price'] = float(m.group(1))
    else:
        m = re.search(r'采购价\s*\n?\s*([\d.]+)', txt)
        d['detail_price'] = float(m.group(1)) if m else None
    m = re.search(r'折后约\s*¥?\s*([\d.]+)', txt)
    d['discount_price'] = float(m.group(1)) if m else None
    m = re.search(r'(\d+)店参团', txt); d['stores_joined'] = int(m.group(1)) if m else None
    m = re.search(r'([\d.]+万?)\s*' + UNIT_RE + r'\s*已付款', txt)
    if m:
        v = m.group(1); v = float(v.replace('万','')) * (10000 if '万' in v else 1)
        d['paid_units'] = int(v); d['paid_unit'] = m.group(2)
    else:
        d['paid_units'] = None; d['paid_unit'] = None
    m = re.search(r'采购记录\s*[（(]\s*(\d+)\s*笔\s*[）)]', txt); d['purchase_records'] = int(m.group(1)) if m else None
    m = re.search(r'累计已购买\s*(\d+)\s*' + UNIT_RE, txt)
    if m:
        d['total_purchased'] = int(m.group(1)); d['total_purchased_unit'] = m.group(2)
    else:
        d['total_purchased'] = None; d['total_purchased_unit'] = None
    m = re.search(r'有效期至[：:]\s*([\d-]+)', txt); d['expiry_date'] = m.group(1) if m else None
    m = re.search(r'生产日期[：:]\s*([\d-]+)', txt); d['produce_date'] = m.group(1) if m else None
    m = re.search(r'生产厂家[：:]\s*([^\n]+)', txt); d['manufacturer'] = m.group(1).strip() if m else None
    m = re.search(r'批准文号[：:]\s*([^\n]+)', txt); d['approval_no'] = m.group(1).strip() if m else None
    m = re.search(r'已成团[/\s]*(\d+)\s*' + UNIT_RE + r'起拼', txt)
    if m:
        d['min_qty'] = int(m.group(1)); d['min_unit'] = m.group(2)
    else:
        d['min_qty'] = None; d['min_unit'] = None
    recs = re.findall(r'([一-龥\*\)（]+?)\s*([\d\*]+)\s*(\d+' + UNIT_RE + r')\s*([\d天小时分钟前月\-]+)', txt)
    purchases = [{"buyer": a, "phone": b, "qty": c, "time": tm} for (a, b, c, _u, tm) in recs[:20]]
    d['recent_purchases'] = purchases
    last_7_qty = 0
    for p in purchases:
        dt = parse_time(p.get("time", ""))
        if is_last_7_days(dt):
            try:
                q = int(re.match(r'\d+', p["qty"]).group())
                last_7_qty += q
            except (ValueError, AttributeError): pass
    d['last_7_days_sales'] = last_7_qty if last_7_qty > 0 else None
    large_orders = []
    for p in purchases:
        try:
            q = int(re.match(r'\d+', p["qty"]).group())
            if q >= 50: large_orders.append(q)
        except (ValueError, AttributeError): pass
    d['large_orders_count'] = len(large_orders) if large_orders else None
    d['large_orders_total_qty'] = sum(large_orders) if large_orders else None
    return d

def has_product_data(txt):
    if not txt or len(txt) < 50: return False
    keywords = ['成团价', '参团', '折后约', '采购记录', '生产厂家', '批准文号',
                '有效期', '生产日期', '加入购物车', '立即购买', '立即抢购',
                '起购', '采购价', '已成团', '参与拼团']
    return any(kw in txt for kw in keywords)

# ====================== SPA 导航 ======================
NAVIGATE_JS = r"""(() => {
    const app = document.querySelector('#app');
    if (!app || !app.__vue__ || !app.__vue__.$router) return 'no_router';
    try {
        app.__vue__.$router.push({
            path: '/drugInfo',
            query: { wholesaleid: '%s', isAssemble: '%s', scene: '%s', trafficType: '1' }
        });
        return 'ok';
    } catch(e) { return 'error:' + e.message; }
})()"""

DISMISS_JS = r"""(() => {
    let closed = 0;
    const btns = Array.from(document.querySelectorAll('button'));
    for (const b of btns) {
        const t = (b.innerText||'').trim();
        if (['确认','确定','我知道了','取消','关闭','×','知道了'].includes(t)) { b.click(); closed++; }
    }
    return 'closed_' + closed;
})()"""

NAV_HOME_JS = r"""(() => {
    const app = document.querySelector('#app');
    if (!app || !app.__vue__ || !app.__vue__.$router) return 'no_router';
    try { app.__vue__.$router.push('/home'); return 'ok'; } catch(e) { return 'error:' + e.message; }
})()"""

def current_hash_wid():
    try:
        h = js("location.hash") or ""
        m = re.search(r'wholesaleid=(\w+)', h)
        return m.group(1) if m else ""
    except Exception:
        return ""

def wait_detail(wid, timeout=15):
    for _ in range(timeout):
        time.sleep(1)
        try:
            if current_hash_wid() == str(wid):
                txt = js("document.body.innerText") or ""
                if has_product_data(txt):
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
    sys.stderr.write("[%s] 详情页采集 v2 (公共滑块模块)\n" % BRAND)
    sys.stderr.write("=" * 64 + "\n")
    
    # 读取列表数据
    raw = json.load(open(INPUT_JSON, encoding="utf-8-sig"))
    all_wids = sorted(set(str(r.get('wholesaleid')) for r in raw if r.get('wholesaleid')))
    
    # 加载已有详情
    existing = {}
    try:
        existing = json.load(open(EXISTING_JSON, encoding="utf-8-sig"))
        if not isinstance(existing, dict): existing = {}
    except Exception:
        existing = {}
    
    # 清除失败记录
    WIDS = [w for w in all_wids if w not in existing or
            (existing.get(w, {}).get('paid_units') is None and
             existing.get(w, {}).get('stores_joined') is None and
             existing.get(w, {}).get('purchase_records') is None and
             existing.get(w, {}).get('detail_price') is None)]
    if TOP_N > 0:
        WIDS = WIDS[:TOP_N]
    sys.stderr.write("[%s] 总 wid: %d | 已有有效详情: %d | 待抓: %d%s\n" % (
        BRAND, len(all_wids), len(all_wids) - len(WIDS), len(WIDS), " (top-%d)" % TOP_N if TOP_N > 0 else ""))
    
    # wid 信息
    wid_info = {}
    for r in raw:
        wid = str(r.get('wholesaleid'))
        if not wid: continue
        name = r.get('drugname', '')
        minamount = r.get('minamount', 0)
        is_group_buy = ('包邮' in name) or (minamount and minamount >= 6)
        wid_info[wid] = {'name': name, 'is_group_buy': is_group_buy}
    
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
        # 尝试导航到首页再检查
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
    save_interval = 10  # 每10个保存一次
    
    for idx, wid in enumerate(WIDS):
        info = wid_info.get(wid, {})
        is_group_buy = info.get('is_group_buy', True)
        name_short = (info.get('name', '') or '')[:30]
        txt = ""
        success = False
        
        if is_group_buy:
            routes_to_try = [("true", "0", "拼团"), ("false", "1", "普通")]
        else:
            routes_to_try = [("false", "1", "普通"), ("true", "0", "拼团")]
        
        for route_idx, (is_assemble, scene, label) in enumerate(routes_to_try):
            try:
                js(NAV_HOME_JS)
                time.sleep(2)
                try:
                    js(DISMISS_JS)
                    time.sleep(1)
                except Exception: pass
                
                res = js(NAVIGATE_JS % (wid, is_assemble, scene))
                if res and res.startswith('error'):
                    sys.stderr.write("wid=%s router.push 异常(%s): %s\n" % (wid, label, res))
                
                txt = wait_detail(wid, timeout=12 if route_idx == 0 else 8)
                
                try:
                    js(DISMISS_JS)
                    time.sleep(0.5)
                except Exception: pass
                
                v = check_verify()
                if v.get("type"):
                    sys.stderr.write("[%d/%d] wid=%s 采集途中触发验证弹窗(%s)\n" % (idx+1, len(WIDS), wid, v.get("type")))
                    if handle_verify_during_scrape(timeout=120):
                        time.sleep(1)
                        txt = js("document.body.innerText") or ""
                    else:
                        sys.stderr.write("wid=%s 验证未解决，跳过\n" % wid)
                        txt = ""
                        continue
                else:
                    txt = js("document.body.innerText") or ""
                
                if has_product_data(txt):
                    success = True
                    break
            except Exception as e:
                sys.stderr.write("wid=%s 路由%s 异常: %s\n" % (wid, label, e))
                # 尝试重连
                if "Connection" in str(e) or "timed out" in str(e) or "Broken pipe" in str(e):
                    sys.stderr.write("连接异常，尝试重连...\n")
                    if reconnect():
                        time.sleep(2)
                        continue
                txt = ""
                continue
        
        # new_tab 兜底
        if not success:
            sys.stderr.write("wid=%s SPA 导航失败，尝试 location.href 兜底\n" % wid)
            try:
                fallback_assemble = "true" if is_group_buy else "false"
                fallback_scene = "0" if is_group_buy else "1"
                ysb_common.cdp_eval(ws_global, "location.href = 'https://dian.ysbang.cn/#/drugInfo?wholesaleid=%s&isAssemble=%s&scene=%s&trafficType=1'" % (wid, fallback_assemble, fallback_scene))
                time.sleep(5)
                js(DISMISS_JS)
                time.sleep(1)
                txt = js("document.body.innerText") or ""
                v = check_verify()
                if v.get("type"):
                    if handle_verify_during_scrape(timeout=120):
                        time.sleep(1)
                        txt = js("document.body.innerText") or ""
                if has_product_data(txt):
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
        
        out[wid] = parse_detail(txt)
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
        
        # 周期保存：每 save_interval 个或最后一个时输出到 stderr 提示
        if (idx + 1) % save_interval == 0 or (idx + 1) == len(WIDS):
            sys.stderr.write("--- [%s] 已采集 %d/%d ---\n" % (BRAND, len(existing), len(all_wids)))
            # 同时写一份中间结果到临时文件（防丢失）
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

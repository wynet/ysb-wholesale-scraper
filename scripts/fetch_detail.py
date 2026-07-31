# -*- coding: utf-8 -*-
# 采集商品详情页(drugInfo)的权威销量/资质数据，输出 JSON 到 stdout（按 wholesaleid 索引）。
# 脚本化模式：直接 js() 跑页面 JS，不依赖 LLM。
# 数据解析、JS 模板、验证弹窗处理由 ysb_parser.py 公共模块提供。
#
# 运行方式（browser-use CLI 只接 stdin，不接文件参数）：
#   方式 A（用默认配置）：
#     B="<managed_env>/Scripts/browser-use.exe"
#     "$B" < fetch_detail.py > detail_data.json 2> fetch.err.log
#   方式 B（传参数，推荐）：
#     "$B" > detail_data.json 2> fetch.err.log <<'PY'
#     import sys; sys.argv = ['fetch_detail.py', '--input', 'vuex_raw.json', '--existing', 'detail_data.json', '--top-n', '50']
#     _p = r'<skill_dir>\scripts\fetch_detail.py'
#     exec(compile(open(_p, encoding='utf-8').read(), _p, 'exec'), globals())
#     PY
#
# 重要：本脚本只把最终 JSON print 到 stdout，由外层重定向到 detail_data.json。
import json, re, time, sys, argparse, os, types

# ====================== 加载公共解析模块 ======================
# browser-use 沙箱内无法直接 import，通过 exec 加载 ysb_parser.py
try:
    import ysb_parser
except ImportError:
    _d = os.path.dirname(os.path.abspath(_p)) if '_p' in globals() else (
         os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.')
    ysb_parser = types.ModuleType('ysb_parser')
    exec(compile(open(os.path.join(_d, 'ysb_parser.py'), encoding='utf-8').read(),
                 'ysb_parser', 'exec'), ysb_parser.__dict__)

# ====================== 默认配置 ======================
DEFAULT_INPUT = "vuex_raw.json"
DEFAULT_EXISTING = "detail_data.json"
DEFAULT_TOP_N = 0

def parse_args():
    p = argparse.ArgumentParser(description="药帮详情页权威销量采集（browser-use 沙箱内运行）")
    p.add_argument("--input", default=DEFAULT_INPUT, help="vuex_raw.json 路径（默认 %s）" % DEFAULT_INPUT)
    p.add_argument("--existing", default=DEFAULT_EXISTING, help="已有 detail_data.json 路径，用于断点续传（默认 %s）" % DEFAULT_EXISTING)
    p.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="只抓前 N 个 wid（0=全量，默认 %d）" % DEFAULT_TOP_N)
    return p.parse_args()

def get_opts():
    try:
        return parse_args()
    except SystemExit:
        class _O:
            input = DEFAULT_INPUT
            existing = DEFAULT_EXISTING
            top_n = DEFAULT_TOP_N
        return _O()

opts = get_opts()
INPUT_JSON = opts.input
EXISTING_JSON = opts.existing
TOP_N = opts.top_n

# ====================== 取所有唯一 wholesaleid ======================
raw = json.load(open(INPUT_JSON, encoding="utf-8"))
all_wids = sorted(set(str(r.get('wholesaleid')) for r in raw if r.get('wholesaleid')))

# 加载已有详情，跳过已抓的（断点续传）
try:
    existing = json.load(open(EXISTING_JSON, encoding="utf-8"))
    if not isinstance(existing, dict):
        existing = {}
except Exception:
    existing = {}

# 清除失败记录（调用公共函数）
WIDS = ysb_parser.cleanup_failed_wids(existing, all_wids)
if TOP_N > 0:
    WIDS = WIDS[:TOP_N]
sys.stderr.write("总 wid: %d | 已有有效详情: %d | 待抓: %d%s\n" % (
    len(all_wids), len(all_wids) - len(WIDS), len(WIDS),
    " (top-%d)" % TOP_N if TOP_N > 0 else ""))

# wid 信息（调用公共函数）
wid_info = ysb_parser.build_wid_info(raw)

# ====================== 验证弹窗检测（调用公共函数）======================
def check_verify():
    return ysb_parser.check_verify(js)

def handle_verify_during_scrape(timeout=60):
    return ysb_parser.handle_verify_browser(js, timeout=timeout)

# ====================== SPA 导航辅助 ======================
def current_hash_wid():
    return ysb_parser.current_hash_wid(js)

def wait_detail(wid, timeout=15):
    return ysb_parser.wait_detail(js, wid, timeout)

# ====================== 浏览器采集（SPA 内 Vue Router 导航）======================
ensure_real_tab()

# Step 1: 打开列表页作为 SPA 入口
_first_name = raw[0].get("drugname", "") if raw else ""
_search_key = re.sub(r'^\d+[盒瓶件袋包粒片套罐支]+\s*(包邮|起购)\s*', '', _first_name)
_search_key = re.sub(r'\s+', '', _search_key)[:20]
from urllib.parse import quote
_list_url = "https://dian.ysbang.cn/#/indexContent?page=1&pagesize=60&searchkey=%s&operationtype=1" % quote(_search_key)
sys.stderr.write("打开列表页作为 SPA 入口: %s\n" % _search_key)
new_tab(_list_url)
wait_for_load()
time.sleep(5)

# Step 2: 确认 Vue Router
_router_ok = js(r"""(()=>{const a=document.querySelector('#app');return (a&&a.__vue__&&a.__vue__.$router)?'ok':'no_router';})()""")
if _router_ok != 'ok':
    sys.stderr.write("ERROR: Vue Router 不可用，无法进行 SPA 内导航。\n")
    print(json.dumps(existing, ensure_ascii=False, indent=1))
    sys.exit(1)
sys.stderr.write("Vue Router 就绪，开始 SPA 内导航采集\n")

# 入口页检测滑块
v = check_verify()
if v.get("type"):
    sys.stderr.write("入口页检测到验证弹窗，先处理...\n")
    if not handle_verify_during_scrape(timeout=60):
        sys.stderr.write("入口页验证未解决，退出。\n")
        print(json.dumps(existing, ensure_ascii=False, indent=1))
        sys.exit(1)
    time.sleep(2)

# 逐个采集
out = {}
consecutive_fails = 0
MAX_CONSECUTIVE_FAILS = 5

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
                if handle_verify_during_scrape(timeout=60):
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
            txt = ""
            continue

    # new_tab 兜底（使用采集到的路径和参数）
    if not success:
        sys.stderr.write("wid=%s SPA 导航失败，尝试 new_tab 兜底\n" % wid)
        try:
            new_tab("https://dian.ysbang.cn/#%s?wholesaleid=%s&isAssemble=%s&scene=%s&trafficType=1" % (nav_path, wid, primary_assemble, primary_scene))
            wait_for_load()
            time.sleep(5)
            js(ysb_parser.DISMISS_JS)
            time.sleep(1)
            txt = js("document.body.innerText") or ""
            v = check_verify()
            if v.get("type"):
                if handle_verify_during_scrape(timeout=60):
                    time.sleep(1)
                    txt = js("document.body.innerText") or ""
            if ysb_parser.has_product_data(txt):
                success = True
        except Exception as e:
            sys.stderr.write("wid=%s new_tab 兜底也失败: %s\n" % (wid, e))

    if success:
        consecutive_fails = 0
    else:
        consecutive_fails += 1
        sys.stderr.write("wid=%s [%s] 全部重试失败（连续失败 %d 次）\n" % (wid, name_short, consecutive_fails))

    out[wid] = ysb_parser.parse_detail(txt)
    existing[wid] = out[wid]
    gtype = "拼团" if is_group_buy else "普通"
    sys.stderr.write("[%d/%d] wid=%s [%s] %s -> price=%s paid=%s%s stores=%s recs=%s 7d=%s large=%d笔/%d%s\n" % (
        idx + 1, len(WIDS), wid, name_short, gtype,
        out[wid].get('detail_price'), out[wid].get('paid_units'), out[wid].get('paid_unit'),
        out[wid].get('stores_joined'), out[wid].get('purchase_records'),
        out[wid].get('last_7_days_sales'),
        out[wid].get('large_orders_count') or 0,
        out[wid].get('large_orders_total_qty') or 0,
        out[wid].get('paid_unit') or ''))
    if (idx + 1) % 10 == 0 or (idx + 1) == len(WIDS):
        sys.stderr.write("--- 周期保存: 已采集 %d/%d ---\n" % (len(existing), len(all_wids)))
    time.sleep(2)
    if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
        sys.stderr.write("连续 %d 次失败，Chrome 可能已崩溃。保存已有数据并退出。\n" % MAX_CONSECUTIVE_FAILS)
        break

sys.stderr.write("DONE: %d details (新增 %d，累计 %d)\n" % (len(existing), len(out), len(existing)))
print(json.dumps(existing, ensure_ascii=False, indent=1))

# -*- coding: utf-8 -*-
# 采集商品详情页(drugInfo)的权威销量/资质数据，输出 JSON 到 stdout（按 wholesaleid 索引）。
# 脚本化模式：直接 js() 跑页面 JS，不依赖 LLM。
# 含：近7天销量（从采购记录时戳筛选）、大单统计（数量≥50的笔数+总销量）。
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
# 切勿在脚本内 open('detail_data.json','w') 写文件——browser-use 沙箱里写的文件
# 在真实工作区不可见（会丢）。崩溃保护靠断点续传：重跑时读已有 detail_data.json
# 跳过已抓的 wid，继续抓未抓的。
import json, base64, re, time, datetime, sys, argparse, os

# ====================== 默认配置（无 CLI 参数时使用）======================
DEFAULT_INPUT = "vuex_raw.json"
DEFAULT_EXISTING = "detail_data.json"
DEFAULT_TOP_N = 0   # 0 = 全量
# ========================================================================


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


# ---------- 复用 process.py 的解析逻辑 ----------
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

def sales(dom):
    if not dom: return 0
    m = re.search(r'已拼\s*([\d.]+)\s*万?\+?', dom)
    if not m: return 0
    n = float(m.group(1)); n *= 10000 if '万' in m.group(0) else 1
    return int(n)


# ---------- 解析相对/绝对时间戳，返回 datetime（无时区，视为本地时间）----------
_NOW = None  # 延迟到首次调用时取，避免模块加载时与运行时差距

def _get_now():
    global _NOW
    if _NOW is None:
        _NOW = datetime.datetime.now()
    return _NOW


def parse_time(s):
    """解析采购记录的时间戳，返回 datetime 或 None。
    支持格式：
      - 相对时间：'5分钟前' '21小时前' '3天前'
      - 绝对日期：'2026-07-27' '2026-07-26'
    """
    s = (s or "").strip()
    if not s:
        return None
    # 绝对日期 YYYY-MM-DD
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', s)
    if m:
        return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # 相对时间
    units = {"分钟": 0, "小时": 0, "天": 0}
    found = False
    for unit_key in units:
        m = re.search(r'(\d+)\s*' + unit_key + '前', s)
        if m:
            units[unit_key] = int(m.group(1))
            found = True
    if not found:
        return None
    now = _get_now()
    dt = now - datetime.timedelta(minutes=units["分钟"], hours=units["小时"], days=units["天"])
    return dt


def is_last_7_days(dt):
    """判断 datetime 是否在最近 7 天内（含当天）。"""
    if dt is None:
        return False
    now = _get_now()
    delta = now - dt
    return datetime.timedelta(0) <= delta <= datetime.timedelta(days=7)

# ---------- 取所有唯一 wholesaleid（全量模式，跳过已有详情）----------
raw = json.load(open(INPUT_JSON, encoding="utf-8"))
all_wids = sorted(set(str(r.get('wholesaleid')) for r in raw if r.get('wholesaleid')))

# 加载已有详情，跳过已抓的（断点续传）
try:
    existing = json.load(open(EXISTING_JSON, encoding="utf-8"))
    if not isinstance(existing, dict):
        existing = {}
except Exception:
    existing = {}

# 清除之前失败的记录（关键字段全为 None 的重新抓）
WIDS = [w for w in all_wids if w not in existing or
        (existing.get(w, {}).get('paid_units') is None and
         existing.get(w, {}).get('stores_joined') is None and
         existing.get(w, {}).get('purchase_records') is None and
         existing.get(w, {}).get('detail_price') is None)]
if TOP_N > 0:
    WIDS = WIDS[:TOP_N]
sys.stderr.write("总 wid: %d | 已有有效详情: %d | 待抓: %d%s\n" % (
    len(all_wids), len(all_wids) - len(WIDS), len(WIDS), " (top-%d)" % TOP_N if TOP_N > 0 else ""))

# 提取每个 wid 的商品类型信息（拼团/普通），用于选择正确的路由参数
wid_info = {}
for r in raw:
    wid = str(r.get('wholesaleid'))
    if not wid:
        continue
    name = r.get('drugname', '')
    minamount = r.get('minamount', 0)
    is_group_buy = ('包邮' in name) or (minamount and minamount >= 6)
    wid_info[wid] = {'name': name, 'is_group_buy': is_group_buy}

# ---------- 解析详情页 innerText ----------
UNIT_RE = r'([盒瓶支包袋片套罐条贴副双个])'
def parse_detail(txt):
    d = {}
    # 成团价（拼团页）
    m = re.search(r'成团价\s*¥\s*([\d.]+)', txt)
    if m:
        d['detail_price'] = float(m.group(1))
    else:
        # 采购价（普通页）
        m = re.search(r'采购价\s*\n?\s*([\d.]+)', txt)
        d['detail_price'] = float(m.group(1)) if m else None

    # 折后价（普通页有，拼团页可能没有）
    m = re.search(r'折后约\s*¥?\s*([\d.]+)', txt)
    d['discount_price'] = float(m.group(1)) if m else None
    m = re.search(r'(\d+)店参团', txt);                  d['stores_joined'] = int(m.group(1)) if m else None
    m = re.search(r'([\d.]+万?)\s*' + UNIT_RE + r'\s*已付款', txt)
    if m:
        v = m.group(1); v = float(v.replace('万','')) * (10000 if '万' in v else 1)
        d['paid_units'] = int(v); d['paid_unit'] = m.group(2)
    else:
        d['paid_units'] = None; d['paid_unit'] = None
    m = re.search(r'采购记录\s*[（(]\s*(\d+)\s*笔\s*[）)]', txt); d['purchase_records'] = int(m.group(1)) if m else None

    # 累计已购买（普通页）
    m = re.search(r'累计已购买\s*(\d+)\s*' + UNIT_RE, txt)
    if m:
        d['total_purchased'] = int(m.group(1)); d['total_purchased_unit'] = m.group(2)
    else:
        d['total_purchased'] = None; d['total_purchased_unit'] = None
    m = re.search(r'有效期至[：:]\s*([\d-]+)', txt);      d['expiry_date'] = m.group(1) if m else None
    m = re.search(r'生产日期[：:]\s*([\d-]+)', txt);      d['produce_date'] = m.group(1) if m else None
    m = re.search(r'生产厂家[：:]\s*([^\n]+)', txt);       d['manufacturer'] = m.group(1).strip() if m else None
    m = re.search(r'批准文号[：:]\s*([^\n]+)', txt);       d['approval_no'] = m.group(1).strip() if m else None
    m = re.search(r'已成团[/\s]*(\d+)\s*' + UNIT_RE + r'起拼', txt)
    if m:
        d['min_qty'] = int(m.group(1)); d['min_unit'] = m.group(2)
    else:
        d['min_qty'] = None; d['min_unit'] = None
    # 最近采购明细（买家/数量/时间），最多取 20 条
    recs = re.findall(r'([一-龥\*\)（]+?)\s*([\d\*]+)\s*(\d+' + UNIT_RE + r')\s*([\d天小时分钟前月\-]+)', txt)
    purchases = [{"buyer": a, "phone": b, "qty": c, "time": tm} for (a, b, c, _u, tm) in recs[:20]]
    d['recent_purchases'] = purchases

    # ---- 近 7 天销量：从最近采购记录中按时间筛选 ----
    last_7_qty = 0
    for p in purchases:
        dt = parse_time(p.get("time", ""))
        if is_last_7_days(dt):
            try:
                q = int(re.match(r'\d+', p["qty"]).group())
                last_7_qty += q
            except (ValueError, AttributeError):
                pass
    d['last_7_days_sales'] = last_7_qty if last_7_qty > 0 else None

    # ---- 大单统计（数量 ≥ 50）：笔数 + 总销量 ----
    large_orders = []
    for p in purchases:
        try:
            q = int(re.match(r'\d+', p["qty"]).group())
            if q >= 50:
                large_orders.append(q)
        except (ValueError, AttributeError):
            pass
    d['large_orders_count'] = len(large_orders) if large_orders else None
    d['large_orders_total_qty'] = sum(large_orders) if large_orders else None

    return d

# ---------- 判断页面是否含商品数据（支持拼团和普通两种格式）----------
def has_product_data(txt):
    if not txt or len(txt) < 50:
        return False

    keywords = ['成团价', '参团', '折后约', '采购记录', '生产厂家', '批准文号',
                '有效期', '生产日期', '加入购物车', '立即购买', '立即抢购',
                '起购', '采购价', '已成团', '参与拼团']
    return any(kw in txt for kw in keywords)


# ---------- 滑块验证检测与处理（采集过程中风控触发时用）----------

# 验证弹窗检测 JS（检测易盾滑块 + 通用验证码 + 关键词弹窗）
VERIFY_JS = r"""(() => {
    // 1. 易盾滑块
    const yidunSels = ['.yidun_slider', '.yidun_control', '.yidun_panel',
                       '.yidun_bg-img', '.yidun_jigsaw', '.yidun_modal'];
    for (const s of yidunSels) {
        const el = document.querySelector(s);
        if (el) {
            const cs = window.getComputedStyle(el);
            if (cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0')
                return JSON.stringify({type:'yidun_slider', hit:s});
        }
    }
    // 2. 通用验证码组件
    const direct = ['.nc_iconfont','.nc_wrapper','.geetest_slider_button',
        '.geetest_widget','.geetest_panel','.gt_slider_knob',
        'iframe[src*="captcha"]','iframe[src*="verify"]','iframe[src*="tcaptcha"]',
        '.captcha-container','.verify-container','.slider-verify'];
    for (const s of direct) {
        const el = document.querySelector(s);
        if (el) {
            const cs = window.getComputedStyle(el);
            if (cs.display !== 'none' && cs.visibility !== 'hidden')
                return JSON.stringify({type:'captcha', hit:s});
        }
    }
    // 3. 关键词弹窗
    const kws = ['拖动滑块','完成验证','请完成下方验证','安全验证','操作过于频繁',
                 '请验证身份','滑动验证','人机验证','请拖动','请按住滑块','验证失败','请重新验证'];
    const overlays = document.querySelectorAll(
        '.modal,.dialog,.popup,.mask,.overlay,.toast,' +
        '[class*="modal"],[class*="dialog"],[class*="popup"],' +
        '[class*="verify"],[class*="captcha"],[class*="slider"]');
    for (const o of overlays) {
        const cs = window.getComputedStyle(o);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const t = o.innerText || '';
        if (!t || t.length > 500) continue;
        for (const k of kws) { if (t.indexOf(k) !== -1) return JSON.stringify({type:'modal', hit:k}); }
    }
    return JSON.stringify({type:null, hit:''});
})()"""

def check_verify():
    """检测页面是否有验证弹窗。返回 dict: {type, hit}，type=None 表示无。"""
    try:
        return json.loads(js(VERIFY_JS))
    except Exception:
        return {"type": None, "hit": ""}


# JS 自动解滑块：canvas 图像分析找缺口 + setTimeout 调度鼠标事件模拟人类拖拽
SOLVE_SLIDER_JS = r"""(() => {
    const bgImg = document.querySelector('.yidun_bg-img');
    const jigsaw = document.querySelector('.yidun_jigsaw');
    const slider = document.querySelector('.yidun_slider');
    const control = document.querySelector('.yidun_control');
    if (!bgImg || !slider || !control) return JSON.stringify({ok:false, reason:'no_slider_elements'});

    // 用 canvas 分析缺口位置
    let gapX = -1;
    try {
        const w = bgImg.naturalWidth || 300;
        const h = bgImg.naturalHeight || 160;
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(bgImg, 0, 0, w, h);
        const data = ctx.getImageData(0, 0, w, h).data;

        // 每列水平梯度（缺口处颜色突变）
        const colDiff = new Array(w).fill(0);
        for (let x = 1; x < w; x++) {
            let sum = 0;
            for (let y = 0; y < h; y++) {
                const idx = (y*w+x)*4, idxP = (y*w+(x-1))*4;
                sum += Math.abs(data[idx]-data[idxP]) + Math.abs(data[idx+1]-data[idxP+1]) + Math.abs(data[idx+2]-data[idxP+2]);
            }
            colDiff[x] = sum / h;
        }

        const bgRect = bgImg.getBoundingClientRect();
        const jigRect = jigsaw ? jigsaw.getBoundingClientRect() : bgRect;
        const scale = bgRect.width / w;
        const jigsawW = (jigRect.width || 51) / scale;

        // 在拼图块右侧搜索成对峰值（间距≈拼图块宽度=缺口左右边缘）
        let maxDiff = 0;
        for (let x = Math.floor(jigsawW)+2; x < w-2; x++) maxDiff = Math.max(maxDiff, colDiff[x]);
        const threshold = maxDiff * 0.4;
        const peaks = [];
        for (let x = Math.floor(jigsawW)+2; x < w-2; x++) { if (colDiff[x] > threshold) peaks.push(x); }

        let bestScore = 0;
        for (let i = 0; i < peaks.length; i++) {
            for (let j = i+1; j < peaks.length; j++) {
                const dist = peaks[j]-peaks[i];
                if (Math.abs(dist-jigsawW) < 8) {
                    const score = colDiff[peaks[i]]+colDiff[peaks[j]];
                    if (score > bestScore) { bestScore = score; gapX = peaks[i]; }
                }
            }
        }
    } catch(e) {
        return JSON.stringify({ok:false, reason:'canvas_error:'+e.message});
    }

    if (gapX < 0) return JSON.stringify({ok:false, reason:'no_gap_found'});

    const bgRect = bgImg.getBoundingClientRect();
    const jigRect = jigsaw ? jigsaw.getBoundingClientRect() : bgRect;
    const scale = bgRect.width / (bgImg.naturalWidth || 300);
    const gapXDisplay = gapX * scale;
    const targetX = bgRect.x + gapXDisplay;
    const dragDist = Math.round(targetX - jigRect.x);
    if (dragDist < 10) return JSON.stringify({ok:false, reason:'drag_too_small:'+dragDist});

    // 调度鼠标事件（异步执行，函数立即返回）
    const sliderRect = slider.getBoundingClientRect();
    const startX = sliderRect.x + sliderRect.width/2;
    const startY = sliderRect.y + sliderRect.height/2;

    // mousedown
    slider.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, clientX:startX, clientY:startY}));

    // ease-out 拖拽轨迹（先快后慢，25步）
    const steps = 25;
    let totalDelay = 0;
    for (let i = 1; i <= steps; i++) {
        const progress = 1 - Math.pow(1-i/steps, 2.5);
        const x = startX + dragDist * progress;
        const y = startY + (i%3-1)*0.4;
        const delay = 8 + 20*(i/steps) + (i%5)*2;
        totalDelay += delay;
        setTimeout(() => document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, cancelable:true, clientX:x, clientY:y})), totalDelay);
    }
    // 到终点后微小回弹再释放
    totalDelay += 40;
    setTimeout(() => document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, cancelable:true, clientX:startX+dragDist+3, clientY:startY})), totalDelay);
    totalDelay += 40;
    setTimeout(() => document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, cancelable:true, clientX:startX+dragDist-2, clientY:startY})), totalDelay);
    totalDelay += 40;
    setTimeout(() => document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true, cancelable:true, clientX:startX+dragDist, clientY:startY})), totalDelay);
    totalDelay += 100;
    setTimeout(() => document.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, clientX:startX+dragDist, clientY:startY})), totalDelay);

    return JSON.stringify({ok:true, dragDist:dragDist, finishMs:totalDelay});
})()"""

def try_auto_solve_slider(max_retries=3):
    """尝试自动解决滑块验证（JS 图像分析+模拟拖拽，刷新重试）。
    返回 True=验证通过，False=失败（需人工处理）。"""
    for attempt in range(max_retries):
        sys.stderr.write("  [滑块自动解决] 尝试 %d/%d...\n" % (attempt+1, max_retries))

        # 非首次尝试先刷新验证码
        if attempt > 0:
            js(r"""(() => {
                const refresh = document.querySelector('.yidun_refresh');
                if (refresh) refresh.click();
                return 'ok';
            })()""")
            time.sleep(2)

        # 等待滑块图片加载完成
        for _ in range(10):
            time.sleep(0.5)
            info = js(r"""(() => {
                const bg = document.querySelector('.yidun_bg-img');
                const jig = document.querySelector('.yidun_jigsaw');
                return JSON.stringify({
                    bgW: bg ? bg.getBoundingClientRect().width : 0,
                    jigW: jig ? jig.getBoundingClientRect().width : 0
                });
            })()""")
            try:
                d = json.loads(info)
                if d.get("bgW", 0) > 50 and d.get("jigW", 0) > 10:
                    break
            except Exception:
                pass

        # 执行 JS 自动解滑块
        result = js(SOLVE_SLIDER_JS)
        try:
            r = json.loads(result)
            if not r.get("ok"):
                sys.stderr.write("  [滑块自动解决] 失败: %s\n" % r.get("reason", "unknown"))
                continue
            sys.stderr.write("  [滑块自动解决] 拖拽距离=%s 预计完成=%sms\n" % (r.get("dragDist"), r.get("finishMs")))
        except Exception as e:
            sys.stderr.write("  [滑块自动解决] JS执行异常: %s\n" % e)
            continue

        # 等待拖拽完成 + 验证结果
        time.sleep(3)

        # 检查滑块是否消失
        v = check_verify()
        if not v.get("type"):
            sys.stderr.write("  [滑块自动解决] >>> 验证通过！<<<\n")
            return True
        sys.stderr.write("  [滑块自动解决] 验证未通过，重试...\n")

    return False


def handle_verify_during_scrape(timeout=60):
    """采集过程中检测到验证弹窗时的处理：
    1. 先尝试自动解决（易盾滑块用 JS 图像分析+拖拽）
    2. 自动解决失败 → 等待用户手动完成（每2秒检测，超时退出）
    返回 True=已解决可继续，False=未解决需停止。"""
    v = check_verify()
    vtype = v.get("type", "")
    sys.stderr.write("\n" + "=" * 64 + "\n")
    sys.stderr.write("[!] 采集过程中检测到验证弹窗（%s: %s）\n" % (vtype, v.get("hit", "")))

    # 易盾滑块：先尝试自动解决
    if vtype == "yidun_slider":
        sys.stderr.write("[*] 尝试自动解决滑块（图像分析+模拟拖拽）...\n")
        sys.stderr.flush()
        if try_auto_solve_slider(max_retries=3):
            sys.stderr.write("[OK] 滑块自动解决成功，继续采集。\n\n")
            return True
        sys.stderr.write("[!] 自动解决失败，转为等待手动完成\n")

    # 等待手动完成
    sys.stderr.write("    >>> 请到 9222 Chrome 窗口手动完成验证 <<<\n")
    sys.stderr.write("    脚本将等待最多 %d 秒，每 2 秒检测一次...\n" % timeout)
    sys.stderr.write("=" * 64 + "\n")
    sys.stderr.flush()
    deadline = time.time() + timeout
    last_tick = time.time()
    while time.time() < deadline:
        time.sleep(2)
        v = check_verify()
        if not v.get("type"):
            sys.stderr.write("[OK] 验证弹窗已消失，继续采集。\n\n")
            return True
        if time.time() - last_tick >= 10:
            remain = int(deadline - time.time())
            sys.stderr.write("    ... 仍在等待（剩余 %d 秒，当前: %s）\n" % (remain, v.get("hit", "")))
            last_tick = time.time()
    sys.stderr.write("[!] 等待超时，验证弹窗仍未消失。建议手动完成后重跑。\n")
    return False


# ---------- 浏览器采集（SPA 内 Vue Router 导航，不 new_tab + reload）----------
# 旧方案用 new_tab(手动构造URL) + location.reload() 打开详情页，
# 导致 SPA 数据不刷新（所有 wid 共用第一个商品数据）+ Chrome 频繁崩溃。
# 新方案：在同一个 SPA 实例内用 Vue Router push 导航，浏览器自然处理路由和数据加载。
ensure_real_tab()

# Step 1: 打开列表页作为 SPA 入口（让 Vue 应用初始化，获取 router 实例）
# 从 vuex_raw.json 第一条记录提取搜索关键词
_first_name = raw[0].get("drugname", "") if raw else ""
# 用 drugname 的核心部分作为搜索词（去掉"N盒包邮"前缀）
_search_key = re.sub(r'^\d+[盒瓶件袋包粒片套罐支]+\s*(包邮|起购)\s*', '', _first_name)
_search_key = re.sub(r'\s+', '', _search_key)[:20]
from urllib.parse import quote
_list_url = "https://dian.ysbang.cn/#/indexContent?page=1&pagesize=60&searchkey=%s&operationtype=1" % quote(_search_key)
sys.stderr.write("打开列表页作为 SPA 入口: %s\n" % _search_key)
new_tab(_list_url)
wait_for_load()
time.sleep(5)

# Step 2: 确认 Vue Router 可用
_router_ok = js(r"""(()=>{const a=document.querySelector('#app');return (a&&a.__vue__&&a.__vue__.$router)?'ok':'no_router';})()""")
if _router_ok != 'ok':
    sys.stderr.write("ERROR: Vue Router 不可用，无法进行 SPA 内导航。请检查页面是否正常加载。\n")
    print(json.dumps(existing, ensure_ascii=False, indent=1))
    sys.exit(1)
sys.stderr.write("Vue Router 就绪，开始 SPA 内导航采集\n")

# 入口页加载后检测滑块验证（首次进入可能触发风控）
v = check_verify()
if v.get("type"):
    sys.stderr.write("入口页检测到验证弹窗，先处理...\n")
    if not handle_verify_during_scrape(timeout=60):
        sys.stderr.write("入口页验证未解决，退出。请手动完成验证后重跑。\n")
        print(json.dumps(existing, ensure_ascii=False, indent=1))
        sys.exit(1)
    time.sleep(2)

# SPA 内导航 JS 模板：用 Vue Router push 到详情页（不刷新页面，不 new_tab）
# 参数：%s=wholesaleid, %s=isAssemble, %s=scene
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

# 关闭弹窗 JS（支持多种弹窗按钮）
DISMISS_JS = r"""(() => {
    let closed = 0;
    const btns = Array.from(document.querySelectorAll('button'));
    for (const b of btns) {
        const t = (b.innerText||'').trim();
        if (['确认','确定','我知道了','取消','关闭','×','知道了'].includes(t)) {
            b.click(); closed++;
        }
    }
    document.querySelectorAll('.y-dialog-modal, [class*=dialog-modal]').forEach(m => { m.style.display = 'none'; });
    return 'closed_' + closed;
})()"""

# 回首页 JS（每次导航前先回 /home，强制 Vue 卸载组件再重新挂载，避免 SPA 缓存不刷新）
NAV_HOME_JS = r"""(() => {
    const app = document.querySelector('#app');
    if (!app || !app.__vue__ || !app.__vue__.$router) return 'no_router';
    try { app.__vue__.$router.push('/home'); return 'ok'; } catch(e) { return 'error:' + e.message; }
})()"""

def current_hash_wid():
    """获取当前页面 hash 中的 wholesaleid。"""
    try:
        h = js("location.hash") or ""
        m = re.search(r'wholesaleid=(\w+)', h)
        return m.group(1) if m else ""
    except Exception:
        return ""

def wait_detail(wid, timeout=15):
    """等待详情页加载完成：hash 匹配 + 页面含商品数据。"""
    for _ in range(timeout):
        time.sleep(1)
        if current_hash_wid() == str(wid):
            txt = js("document.body.innerText") or ""
            if has_product_data(txt):
                return txt
    # 超时后再读一次
    txt = js("document.body.innerText") or ""
    if has_product_data(txt):
        return txt
    return txt

out = {}
consecutive_fails = 0
MAX_CONSECUTIVE_FAILS = 5

for idx, wid in enumerate(WIDS):
    info = wid_info.get(wid, {})
    is_group_buy = info.get('is_group_buy', True)
    name_short = (info.get('name', '') or '')[:30]
    txt = ""
    success = False

    # 策略：根据商品类型选择路由顺序
    # 拼团商品(包邮)先尝试 isAssemble=true,scene=0（拼团页，有更多数据）
    # 普通商品(起购)先尝试 isAssemble=false,scene=1（普通页）
    # 失败则尝试另一种路由
    if is_group_buy:
        routes_to_try = [("true", "0", "拼团"), ("false", "1", "普通")]
    else:
        routes_to_try = [("false", "1", "普通"), ("true", "0", "拼团")]

    for route_idx, (is_assemble, scene, label) in enumerate(routes_to_try):
        try:
            # 先回首页重置路由状态（强制 Vue 卸载组件，避免 SPA 缓存不刷新）
            js(NAV_HOME_JS)
            time.sleep(2)
            try:
                js(DISMISS_JS)
                time.sleep(1)
            except Exception:
                pass

            # 导航到商品详情页（使用动态路由参数）
            res = js(NAVIGATE_JS % (wid, is_assemble, scene))
            if res and res.startswith('error'):
                sys.stderr.write("wid=%s router.push 异常(%s): %s\n" % (wid, label, res))

            # 等待详情页加载
            txt = wait_detail(wid, timeout=12 if route_idx == 0 else 8)

            # 关闭普通弹窗
            try:
                js(DISMISS_JS)
                time.sleep(0.5)
            except Exception:
                pass

            # ★ 检测滑块验证弹窗（采集过程中风控触发）
            v = check_verify()
            if v.get("type"):
                sys.stderr.write("[%d/%d] wid=%s 采集途中触发验证弹窗(%s)\n" % (idx+1, len(WIDS), wid, v.get("type")))
                if handle_verify_during_scrape(timeout=60):
                    # 验证通过后重新读取页面
                    time.sleep(1)
                    txt = js("document.body.innerText") or ""
                else:
                    # 验证未解决，跳过此商品
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
            txt = ""
            continue

    # 最后兜底：SPA 导航完全失败，用 new_tab 重新打开
    if not success:
        sys.stderr.write("wid=%s SPA 导航失败，尝试 new_tab 兜底\n" % wid)
        try:
            fallback_assemble = "true" if is_group_buy else "false"
            fallback_scene = "0" if is_group_buy else "1"
            new_tab("https://dian.ysbang.cn/#/drugInfo?wholesaleid=%s&isAssemble=%s&scene=%s&trafficType=1" % (wid, fallback_assemble, fallback_scene))
            wait_for_load()
            time.sleep(5)
            js(DISMISS_JS)
            time.sleep(1)
            txt = js("document.body.innerText") or ""
            # 兜底也检测滑块
            v = check_verify()
            if v.get("type"):
                if handle_verify_during_scrape(timeout=60):
                    time.sleep(1)
                    txt = js("document.body.innerText") or ""
            if has_product_data(txt):
                success = True
        except Exception as e:
            sys.stderr.write("wid=%s new_tab 兜底也失败: %s\n" % (wid, e))

    if success:
        consecutive_fails = 0
    else:
        consecutive_fails += 1
        sys.stderr.write("wid=%s [%s] 全部重试失败（连续失败 %d 次）\n" % (wid, name_short, consecutive_fails))
    out[wid] = parse_detail(txt)
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
    # 每 10 条输出一次累计 JSON（防崩溃丢数据：最后一次完整输出会被外层重定向捕获）
    if (idx + 1) % 10 == 0 or (idx + 1) == len(WIDS):
        sys.stderr.write("--- 周期保存: 已采集 %d/%d ---\n" % (len(existing), len(all_wids)))
    # 每个商品之间延迟（避免频繁请求触发滑块验证）
    time.sleep(2)
    # 连续失败过多：保存并退出
    if consecutive_fails >= MAX_CONSECUTIVE_FAILS:
        sys.stderr.write("连续 %d 次失败，Chrome 可能已崩溃。保存已有数据并退出。\n" % MAX_CONSECUTIVE_FAILS)
        break

# 合并新旧数据，print 到 stdout（由外层重定向到 detail_data.json）
sys.stderr.write("DONE: %d details (新增 %d，累计 %d)\n" % (len(existing), len(out), len(existing)))
print(json.dumps(existing, ensure_ascii=False, indent=1))

# -*- coding: utf-8 -*-
"""药师帮公共解析模块 — 纯 Python，无外部依赖

收拢 process.py / cdp_fetch_detail_v2.py / fetch_detail.py / extract.py 中
重复的数据解析函数、正则常量、JS 模板和通用辅助函数。

被以下脚本调用：
  - process.py          (普通 Python，直接 import)
  - cdp_fetch_detail_v2.py (普通 Python，直接 import)
  - fetch_detail.py     (browser-use 沙箱，通过 exec 加载)
  - extract.py          (browser-use 沙箱，通过 exec 加载)

不依赖 websocket / numpy / PIL，仅用标准库。
"""
import json, base64, re, time, datetime, sys

# ====================== 常量 ======================
UNIT_RE = r'([盒瓶支包袋片套罐条贴副双个])'

# 三种详情页 URL 类型（scene 始终为 0）：
#   1. 医械城: /instrument/drugDetail  (busiScope=9 或 sourceType=1)
#   2. 拼团:   /drugInfo?isAssemble=true&scene=0   (activitytype=7/8, busiScope≠9)
#   3. 普通:   /drugInfo?isAssemble=false&scene=0  (activitytype=1, busiScope≠9)
PRODUCT_URL_INSTRUMENT_GROUP = "https://dian.ysbang.cn/#/instrument/drugDetail?wholesaleid=%s&isAssemble=true&scene=0&trafficType=1"
PRODUCT_URL_INSTRUMENT_REGULAR = "https://dian.ysbang.cn/#/instrument/drugDetail?wholesaleid=%s&isAssemble=false&scene=0&trafficType=1"
PRODUCT_URL_GROUP = "https://dian.ysbang.cn/#/drugInfo?wholesaleid=%s&isAssemble=true&scene=0&trafficType=1"
PRODUCT_URL_REGULAR = "https://dian.ysbang.cn/#/drugInfo?wholesaleid=%s&isAssemble=false&scene=0&trafficType=1"

# 列表采集 JS（共享）：读取 Vuex store + 匹配卡片 DOM + 采集详情链接
# 采集方式：从卡片 Vue 组件获取 sourceType/activitytype + router.resolve 生成 URL
# 路径从 Vue Router 路由表获取（不手动拼接），sourceType=1 → /instrument/drugDetail
# 被 cdp_extract.py 和 extract.py 共同调用
READ_JS = r"""(() => {
  const app = document.querySelector('#app');
  if (!app || !app.__vue__ || !app.__vue__.$store) return JSON.stringify({error:'no_vue'});
  const list = app.__vue__.$store.state.drugList.drugList || [];
  const wraps = Array.from(document.querySelectorAll('.all-goods-wrapper'));
  const router = app.__vue__.$router;
  const used = new Set();
  const res = [];
  for (const it of list) {
    let block = '';
    let matchedWrap = null;
    if (it.provider_name) {
      for (let i = 0; i < wraps.length; i++) {
        if (used.has(i)) continue;
        const t = wraps[i].innerText;
        if (t.indexOf(it.drugname) !== -1 && t.indexOf(it.provider_name) !== -1) {
          block = t; used.add(i); matchedWrap = wraps[i]; break;
        }
      }
    }
    if (!block) {
      for (let i = 0; i < wraps.length; i++) {
        if (used.has(i)) continue;
        if (wraps[i].innerText.indexOf(it.drugname) !== -1) {
          block = wraps[i].innerText; used.add(i); matchedWrap = wraps[i]; break;
        }
      }
    }
    // 从卡片 Vue 组件获取 sourceType + activitytype，用 router.resolve 生成 URL
    // sourceType=1 → 医械城(/instrument/drugDetail)，sourceType=0 → 药品(/drugInfo)
    // activitytype=7/8 → 拼团(isAssemble=true)，activitytype=1 → 普通(isAssemble=false)
    let detailUrl = '';
    let activitytype = null;
    let busiScope = null;
    let sourceType = null;
    if (matchedWrap) {
      const vue = matchedWrap.__vue__;
      if (vue && vue.$props && vue.$props.goodsInfo) {
        const g = vue.$props.goodsInfo;
        activitytype = g.activitytype;
        busiScope = g.busiScope;
        sourceType = g.sourceType;
        const wid = g.wholesaleid || it.wholesaleid;
        if (wid && router) {
          const isInstrument = g.sourceType === 1;
          const isGroup = g.activitytype === 7 || g.activitytype === 8;
          const path = isInstrument ? '/instrument/drugDetail' : '/drugInfo';
          try {
            const resolved = router.resolve({
              path: path,
              query: { wholesaleid: String(wid), isAssemble: isGroup ? 'true' : 'false', scene: '0', trafficType: '1' }
            });
            detailUrl = 'https://dian.ysbang.cn/' + resolved.href;
          } catch(e) {}
        }
      }
    }
    // Vuex item 可能含 isassemble 字段（布尔/字符串），用于判断拼团/普通
    let isAssemble = null;
    for (const k of ['isassemble','isAssemble','is_assemble','isassemble']) {
      if (it[k] !== undefined && it[k] !== null) { isAssemble = it[k]; break; }
    }
    res.push({
      drugname: it.drugname, specification: it.specification, minamount: it.minamount,
      drugimageurl: it.drugimageurl, brand: it.brand, provider_name: it.provider_name,
      unit: it.unit, wholesaleAmount: it.wholesaleAmount, priceToken: it.priceToken,
      alreadysales: it.alreadysales, wholesaleid: it.wholesaleid,
      detail_url: detailUrl, isAssemble: isAssemble, activitytype: activitytype,
      busiScope: busiScope, sourceType: sourceType,
      domText: (block||'').replace(/\s+/g,' ').slice(0,1500)
    });
  }
  return JSON.stringify({count: res.length, items: res, matched: used.size, total_cards: wraps.length});
})()"""


def build_detail_url(wholesaleid, name, detail_url=None, activitytype=None, busiScope=None, sourceType=None):
    """构建商品详情页 URL。
    优先使用从列表页直接采集的 detail_url；
    其次用 busiScope/sourceType 判断医械城/药品 + activitytype 判断拼团/普通；
    最后回退到商品名含「包邮」判断。
    """
    if detail_url:
        # 确保是完整 URL
        if detail_url.startswith('#'):
            return 'https://dian.ysbang.cn/' + detail_url
        return detail_url
    wid = str(wholesaleid or '').strip()
    if not wid or wid == 'None':
        return ''
    # 判断是否医械城: sourceType=1
    is_instrument = sourceType in (1, '1')
    # 判断是否拼团: activitytype=7/8
    if activitytype is not None:
        is_group = activitytype in (7, 8, '7', '8')
    else:
        is_group = is_group_buy_name(name)
    if is_instrument:
        url = PRODUCT_URL_INSTRUMENT_GROUP if is_group else PRODUCT_URL_INSTRUMENT_REGULAR
    else:
        url = PRODUCT_URL_GROUP if is_group else PRODUCT_URL_REGULAR
    return url % wid


def parse_detail_url(detail_url):
    """从采集到的 detail_url 解析出 path, isAssemble, scene。
    用于详情页采集时直接使用采集到的真实 URL 参数，而非自行判断。
    返回: dict {path, isAssemble, scene, wholesaleid} 或 None
    """
    if not detail_url:
        return None
    try:
        hash_part = detail_url.split('#', 1)[1] if '#' in detail_url else detail_url
        if '?' not in hash_part:
            return None
        path, query_str = hash_part.split('?', 1)
        params = {}
        for pair in query_str.split('&'):
            if '=' in pair:
                k, v = pair.split('=', 1)
                params[k] = v
        return {
            'path': path,
            'isAssemble': params.get('isAssemble', ''),
            'scene': params.get('scene', '0'),
            'wholesaleid': params.get('wholesaleid', ''),
        }
    except Exception:
        return None


# SPA 导航 JS 模板（Vue Router push）
# 参数: path, wholesaleid, isAssemble, scene
NAVIGATE_JS = r"""(() => {
    const app = document.querySelector('#app');
    if (!app || !app.__vue__ || !app.__vue__.$router) return 'no_router';
    try {
        app.__vue__.$router.push({
            path: '%s',
            query: { wholesaleid: '%s', isAssemble: '%s', scene: '%s', trafficType: '1' }
        });
        return 'ok';
    } catch(e) { return 'error:' + e.message; }
})()"""

# 关闭弹窗 JS
DISMISS_JS = r"""(() => {
    let closed = 0;
    const btns = Array.from(document.querySelectorAll('button'));
    for (const b of btns) {
        const t = (b.innerText||'').trim();
        if (['确认','确定','我知道了','取消','关闭','×','知道了'].includes(t)) { b.click(); closed++; }
    }
    document.querySelectorAll('.y-dialog-modal, [class*=dialog-modal]').forEach(m => { m.style.display = 'none'; });
    return 'closed_' + closed;
})()"""

# 回首页 JS（每次导航前先回 /home，强制 Vue 卸载组件再重新挂载）
NAV_HOME_JS = r"""(() => {
    const app = document.querySelector('#app');
    if (!app || !app.__vue__ || !app.__vue__.$router) return 'no_router';
    try { app.__vue__.$router.push('/home'); return 'ok'; } catch(e) { return 'error:' + e.message; }
})()"""

# 验证弹窗检测 JS（合并 extract.py 的完整选择器 + ysb_common.py 的尺寸检查）
VERIFY_JS = r"""(() => {
    const yidunSels = ['.yidun_slider', '.yidun_control', '.yidun_panel',
                       '.yidun_bg-img', '.yidun_jigsaw', '.yidun_modal'];
    for (const s of yidunSels) {
        const el = document.querySelector(s);
        if (el) {
            const cs = window.getComputedStyle(el);
            if (cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0') {
                const r = el.getBoundingClientRect();
                if (r.width > 5 || r.height > 5)
                    return JSON.stringify({type:'yidun_slider', hit:s});
            }
        }
    }
    const direct = ['.nc_iconfont','.nc-lang-cnt','.scale_text','#nc_1_wrapper','.nc_wrapper',
        '.geetest_slider_button','.geetest_widget','.geetest_panel','.geetest_btn','.geetest_popup',
        '.gt_slider_knob','.gt_widget','.gt_cut_wrap',
        '#tcaptcha_iframe','.tcaptcha-transform','.tcaptcha-action',
        'iframe[src*="captcha"]','iframe[src*="verify"]','iframe[src*="validate"]','iframe[src*="tcaptcha"]','iframe[src*="geetest"]',
        '.captcha-container','.verify-container','.slider-verify','[class*="captcha-modal"]','[class*="verify-modal"]'];
    for (const s of direct) {
        const el = document.querySelector(s);
        if (el) {
            const cs = window.getComputedStyle(el);
            if (cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0')
                return JSON.stringify({type:'captcha', hit:s});
        }
    }
    const kws = ['拖动滑块','完成验证','请完成下方验证','请完成验证','安全验证','操作过于频繁',
                 '请验证身份','滑动验证','人机验证','请拖动','拖动完成验证','请按住滑块','验证失败','请重新验证'];
    const overlays = document.querySelectorAll(
        '.modal,.dialog,.popup,.mask,.overlay,.toast,' +
        '[class*="modal"],[class*="dialog"],[class*="popup"],' +
        '[class*="mask"],[class*="verify"],[class*="captcha"],[class*="slider"]');
    for (const o of overlays) {
        const cs = window.getComputedStyle(o);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const r = o.getBoundingClientRect();
        if (r.width < 5 && r.height < 5) continue;
        const t = o.innerText || '';
        if (!t || t.length > 500) continue;
        for (const k of kws) { if (t.indexOf(k) !== -1) return JSON.stringify({type:'modal', hit:k}); }
    }
    return JSON.stringify({type:null, hit:''});
})()"""

# JS 自动解滑块（browser-use 沙箱内用：canvas 图像分析 + MouseEvent 模拟拖拽）
SOLVE_SLIDER_JS = r"""(() => {
    const bgImg = document.querySelector('.yidun_bg-img');
    const jigsaw = document.querySelector('.yidun_jigsaw');
    const slider = document.querySelector('.yidun_slider');
    const control = document.querySelector('.yidun_control');
    if (!bgImg || !slider || !control) return JSON.stringify({ok:false, reason:'no_slider_elements'});

    let gapX = -1;
    try {
        const w = bgImg.naturalWidth || 300;
        const h = bgImg.naturalHeight || 160;
        const canvas = document.createElement('canvas');
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(bgImg, 0, 0, w, h);
        const data = ctx.getImageData(0, 0, w, h).data;

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

    const sliderRect = slider.getBoundingClientRect();
    const startX = sliderRect.x + sliderRect.width/2;
    const startY = sliderRect.y + sliderRect.height/2;

    slider.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, clientX:startX, clientY:startY}));

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


# ====================== 价格解码 ======================
def decode_price(tok):
    """解码 priceToken (base64 protobuf) 得到真实单价。"""
    if not tok:
        return None
    try:
        b = base64.b64decode(tok)
    except Exception:
        return None
    for marker in (b'\x12', b'\x3a'):
        i = b.find(marker)
        while i != -1:
            if i + 1 < len(b):
                L = b[i + 1]
                if 1 <= L <= 12:
                    s = b[i + 2: i + 2 + L]
                    if re.match(rb'^\d+\.\d{1,2}$', s) or re.match(rb'^\d+$', s):
                        return s.decode('latin-1')
            i = b.find(marker, i + 1)
    m = re.search(rb'\d+\.\d{2}', b)
    return m.group(0).decode('latin-1') if m else None


# dec 是 decode_price 的别名，保持向后兼容
dec = decode_price


# ====================== 销量解析 ======================
def parse_sales(dom):
    """从卡片文本解析「已拼N」销量（支持「N万+」）。"""
    if not dom:
        return 0
    m = re.search(r'已拼\s*([\d.]+)\s*万?\+?', dom)
    if not m:
        return 0
    num = float(m.group(1))
    if '万' in m.group(0):
        num *= 10000
    return int(num)


# sales 是 parse_sales 的别名，保持向后兼容
sales = parse_sales


# ====================== 时间解析 ======================
_NOW = None

def _get_now():
    """延迟获取当前时间，避免模块加载时与运行时差距。"""
    global _NOW
    if _NOW is None:
        _NOW = datetime.datetime.now()
    return _NOW


def parse_time(s):
    """解析采购记录时间戳，返回 datetime 或 None。
    支持：'5分钟前' '21小时前' '3天前' '2026-07-27'
    """
    s = (s or "").strip()
    if not s:
        return None
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
    if not found:
        return None
    now = _get_now()
    return now - datetime.timedelta(minutes=units["分钟"], hours=units["小时"], days=units["天"])


def is_last_7_days(dt):
    """判断 datetime 是否在最近 7 天内（含当天）。"""
    if dt is None:
        return False
    now = _get_now()
    delta = now - dt
    return datetime.timedelta(0) <= delta <= datetime.timedelta(days=7)


# ====================== 详情页解析 ======================
def parse_detail(txt):
    """解析详情页 innerText，提取价格/销量/资质/采购记录等字段。
    同时支持拼团页（成团价/店参团/已付款）和普通页（采购价/折后约/累计已购买）。
    """
    d = {}
    # 成团价（拼团页）或采购价（普通页）
    m = re.search(r'成团价\s*¥\s*([\d.]+)', txt)
    if m:
        d['detail_price'] = float(m.group(1))
    else:
        m = re.search(r'采购价\s*\n?\s*([\d.]+)', txt)
        d['detail_price'] = float(m.group(1)) if m else None
    # 折后价
    m = re.search(r'折后约\s*¥?\s*([\d.]+)', txt)
    d['discount_price'] = float(m.group(1)) if m else None
    # 店参团
    m = re.search(r'(\d+)店参团', txt)
    d['stores_joined'] = int(m.group(1)) if m else None
    # 已付款件数（权威销量）
    m = re.search(r'([\d.]+万?)\s*' + UNIT_RE + r'\s*已付款', txt)
    if m:
        v = m.group(1)
        v = float(v.replace('万', '')) * (10000 if '万' in v else 1)
        d['paid_units'] = int(v)
        d['paid_unit'] = m.group(2)
    else:
        d['paid_units'] = None
        d['paid_unit'] = None
    # 采购记录笔数
    m = re.search(r'采购记录\s*[（(]\s*(\d+)\s*笔\s*[）)]', txt)
    d['purchase_records'] = int(m.group(1)) if m else None
    # 累计已购买（普通页）
    m = re.search(r'累计已购买\s*(\d+)\s*' + UNIT_RE, txt)
    if m:
        d['total_purchased'] = int(m.group(1))
        d['total_purchased_unit'] = m.group(2)
    else:
        d['total_purchased'] = None
        d['total_purchased_unit'] = None
    # 资质信息
    m = re.search(r'有效期至[：:]\s*([\d-]+)', txt)
    d['expiry_date'] = m.group(1) if m else None
    m = re.search(r'生产日期[：:]\s*([\d-]+)', txt)
    d['produce_date'] = m.group(1) if m else None
    m = re.search(r'生产厂家[：:]\s*([^\n]+)', txt)
    d['manufacturer'] = m.group(1).strip() if m else None
    m = re.search(r'批准文号[：:]\s*([^\n]+)', txt)
    d['approval_no'] = m.group(1).strip() if m else None
    # 起拼量
    m = re.search(r'已成团[/\s]*(\d+)\s*' + UNIT_RE + r'起拼', txt)
    if m:
        d['min_qty'] = int(m.group(1))
        d['min_unit'] = m.group(2)
    else:
        d['min_qty'] = None
        d['min_unit'] = None
    # 最近采购明细（最多 20 条）
    recs = re.findall(
        r'([一-龥\*\)（]+?)\s*([\d\*]+)\s*(\d+' + UNIT_RE + r')\s*([\d天小时分钟前月\-]+)', txt)
    purchases = [{"buyer": a, "phone": b, "qty": c, "time": tm}
                 for (a, b, c, _u, tm) in recs[:20]]
    d['recent_purchases'] = purchases
    # 近 7 天销量
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
    # 大单统计（≥50）
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


def has_product_data(txt):
    """判断页面文本是否含商品数据（支持拼团和普通两种格式）。"""
    if not txt or len(txt) < 50:
        return False
    keywords = ['成团价', '参团', '折后约', '采购记录', '生产厂家', '批准文号',
                '有效期', '生产日期', '加入购物车', '立即购买', '立即抢购',
                '起购', '采购价', '已成团', '参与拼团']
    return any(kw in txt for kw in keywords)


# ====================== 商品类型判断 ======================
def is_group_buy_name(name):
    """根据商品名判断是否为拼团商品（包邮）。"""
    return '包邮' in (name or '')


def build_wid_info(raw):
    """从 vuex_raw.json 数据构建 wid -> info 字典。
    info 含: name, is_group_buy, detail_url。
    is_group_buy 判断优先级：activitytype 字段(7/8=拼团) > Vuex isAssemble 字段 > 商品名含「包邮」 > minamount>=6。
    """
    wid_info = {}
    for r in raw:
        wid = str(r.get('wholesaleid'))
        if not wid:
            continue
        name = r.get('drugname', '')
        minamount = r.get('minamount', 0)
        # 优先使用 activitytype 字段（从卡片 Vue 组件采集）
        activitytype = r.get('activitytype')
        if activitytype is not None:
            is_group_buy = activitytype in (7, 8, '7', '8')
        elif r.get('isAssemble') is not None:
            is_assemble = r.get('isAssemble')
            if isinstance(is_assemble, str):
                is_group_buy = is_assemble.lower() in ('true', '1', 'yes')
            else:
                is_group_buy = bool(is_assemble)
        else:
            is_group_buy = is_group_buy_name(name) or (minamount and minamount >= 6)
        wid_info[wid] = {
            'name': name,
            'is_group_buy': is_group_buy,
            'detail_url': r.get('detail_url', ''),
            'activitytype': activitytype,
            'busiScope': r.get('busiScope'),
            'sourceType': r.get('sourceType'),
        }
    return wid_info


def cleanup_failed_wids(existing, all_wids):
    """从 existing 中清除关键字段全为 None 的失败记录，返回需要重抓的 wid 列表。"""
    return [w for w in all_wids if w not in existing or
            (existing.get(w, {}).get('paid_units') is None and
             existing.get(w, {}).get('stores_joined') is None and
             existing.get(w, {}).get('purchase_records') is None and
             existing.get(w, {}).get('detail_price') is None)]


# ====================== 验证弹窗检测（需要 js_fn）======================
def check_verify(js_fn):
    """检测页面是否有验证弹窗。js_fn 是执行 JS 的回调函数。"""
    try:
        return json.loads(js_fn(VERIFY_JS) or "{}")
    except Exception:
        return {"type": None, "hit": ""}


# ====================== SPA 导航辅助（需要 js_fn）======================
def current_hash_wid(js_fn):
    """获取当前页面 hash 中的 wholesaleid。"""
    try:
        h = js_fn("location.hash") or ""
        m = re.search(r'wholesaleid=(\w+)', h)
        return m.group(1) if m else ""
    except Exception:
        return ""


def wait_detail(js_fn, wid, timeout=15, has_data_fn=None):
    """等待详情页加载完成：hash 匹配 + 页面含商品数据。
    js_fn: 执行 JS 的回调
    has_data_fn: 判断文本是否含商品数据的回调（默认用 has_product_data）
    """
    if has_data_fn is None:
        has_data_fn = has_product_data
    for _ in range(timeout):
        time.sleep(1)
        if current_hash_wid(js_fn) == str(wid):
            txt = js_fn("document.body.innerText") or ""
            if has_data_fn(txt):
                return txt
    txt = js_fn("document.body.innerText") or ""
    return txt


# ====================== browser-use 沙箱内滑块自动解决 ======================
def try_auto_solve_slider(js_fn, max_retries=3, log_fn=None):
    """browser-use 沙箱内自动解决滑块验证（JS canvas 分析 + MouseEvent 模拟拖拽）。
    js_fn: 执行 JS 的回调（browser-use 的 js() 函数）
    log_fn: 日志输出回调（默认 sys.stderr.write）
    返回 True=验证通过，False=失败。
    """
    if log_fn is None:
        log_fn = lambda msg: sys.stderr.write(msg + "\n")
    for attempt in range(max_retries):
        log_fn("  [滑块自动解决] 尝试 %d/%d..." % (attempt + 1, max_retries))
        if attempt > 0:
            js_fn(r"""(() => {
                const refresh = document.querySelector('.yidun_refresh');
                if (refresh) refresh.click();
                return 'ok';
            })()""")
            time.sleep(2)
        # 等待滑块图片加载
        for _ in range(10):
            time.sleep(0.5)
            info = js_fn(r"""(() => {
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
        result = js_fn(SOLVE_SLIDER_JS)
        try:
            r = json.loads(result)
            if not r.get("ok"):
                log_fn("  [滑块自动解决] 失败: %s" % r.get("reason", "unknown"))
                continue
            log_fn("  [滑块自动解决] 拖拽距离=%s 预计完成=%sms" % (r.get("dragDist"), r.get("finishMs")))
        except Exception as e:
            log_fn("  [滑块自动解决] JS执行异常: %s" % e)
            continue
        time.sleep(3)
        v = check_verify(js_fn)
        if not v.get("type"):
            log_fn("  [滑块自动解决] >>> 验证通过！<<<")
            return True
        log_fn("  [滑块自动解决] 验证未通过，重试...")
    return False


def handle_verify_browser(js_fn, timeout=60, log_fn=None):
    """browser-use 沙箱内的验证弹窗处理：先自动解决，失败转人工等待。
    js_fn: 执行 JS 的回调
    timeout: 手动验证等待超时（秒）
    log_fn: 日志输出回调
    返回 True=已解决，False=未解决。
    """
    if log_fn is None:
        log_fn = lambda msg: sys.stderr.write(msg + "\n")
    v = check_verify(js_fn)
    vtype = v.get("type", "")
    log_fn("")
    log_fn("=" * 64)
    log_fn("[!] 检测到验证弹窗（%s: %s）" % (vtype, v.get("hit", "")))
    if vtype in ("yidun_slider", "captcha"):
        log_fn("[*] 尝试自动解决滑块（图像分析+模拟拖拽）...")
        if try_auto_solve_slider(js_fn, max_retries=3, log_fn=log_fn):
            log_fn("[OK] 滑块自动解决成功，继续采集。")
            log_fn("")
            return True
        log_fn("[!] 自动解决失败，转为等待手动完成")
    log_fn("    >>> 请到 9222 Chrome 窗口手动完成验证 <<<")
    log_fn("    脚本将等待最多 %d 秒，每 2 秒检测一次..." % timeout)
    log_fn("=" * 64)
    deadline = time.time() + timeout
    last_tick = time.time()
    while time.time() < deadline:
        time.sleep(2)
        v = check_verify(js_fn)
        if not v.get("type"):
            log_fn("[OK] 验证弹窗已消失，继续采集。")
            log_fn("")
            return True
        if time.time() - last_tick >= 10:
            remain = int(deadline - time.time())
            log_fn("    ... 仍在等待（剩余 %d 秒，当前: %s）" % (remain, v.get("hit", "")))
            last_tick = time.time()
    log_fn("[!] 等待超时，验证弹窗仍未消失。建议手动完成后重跑。")
    return False

# -*- coding: utf-8 -*-
"""CDP 直连版详情页采集脚本 v2 - 集成 ddddocr 滑块识别
从 cdp_fetch_detail.py 增强：
  1. ddddocr 精准识别滑块缺口位置（替代 canvas 梯度分析）
  2. CDP Input.dispatchMouseEvent 模拟拖拽（比 JS MouseEvent 更可靠）
  3. 人类轨迹模拟（缓动+抖动+过冲回修）
  4. 断线自动重连
用法: python cdp_fetch_detail_v2.py --input vuex_raw.json --existing detail_data.json --top-n 0
输出: JSON 到 stdout，日志到 stderr
"""
import json, base64, re, time, datetime, sys, argparse, os, math, random
import urllib.request, websocket
from urllib.parse import quote

CDP_URL = "http://127.0.0.1:9222"

# ====================== ddddocr ======================
_ddddocr_det = None
def get_ddddocr():
    global _ddddocr_det
    if _ddddocr_det is None:
        try:
            import ddddocr
            _ddddocr_det = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
            sys.stderr.write("[ddddocr] 初始化成功\n")
        except Exception as e:
            sys.stderr.write("[ddddocr] 初始化失败: %s\n" % e)
    return _ddddocr_det

# ====================== CDP 基础 ======================
def get_tabs():
    r = urllib.request.urlopen(CDP_URL + "/json", timeout=5)
    return json.loads(r.read())

def find_tab():
    for t in get_tabs():
        if t.get("type") == "page" and "dian.ysbang.cn" in t.get("url", ""):
            return t
    for t in get_tabs():
        if t.get("type") == "page":
            return t
    return None

_mid = [0]
def cdp_send(ws, method, params=None):
    _mid[0] += 1
    msg_id = _mid[0]
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

ws_global = None
def js(expr):
    return cdp_eval(ws_global, expr)

def reconnect():
    """断线重连"""
    global ws_global
    try:
        if ws_global:
            ws_global.close()
    except Exception:
        pass
    tab = find_tab()
    if not tab:
        sys.stderr.write("[重连] 没有 Chrome 标签页\n")
        return False
    ws_global = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
    sys.stderr.write("[重连] 成功\n")
    return True

# ====================== 参数解析 ======================
def parse_args():
    p = argparse.ArgumentParser(description="药帮详情页采集(CDP直连+ddddocr版)")
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

# ====================== 验证弹窗检测 ======================
VERIFY_JS = r"""(() => {
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
    try:
        return json.loads(js(VERIFY_JS))
    except Exception:
        return {"type": None, "hit": ""}

# ====================== ddddocr 滑块解决 ======================
def get_slider_images_b64():
    """从页面获取背景图和拼图块的 base64 数据"""
    result = js(r"""(() => {
        const bgImg = document.querySelector('.yidun_bg-img');
        const jigsaw = document.querySelector('.yidun_jigsaw');
        if (!bgImg || !bgImg.complete) return JSON.stringify({error:'bg_not_ready'});
        if (!jigsaw || !jigsaw.complete) return JSON.stringify({error:'jig_not_ready'});
        try {
            // 背景图
            const bgCanvas = document.createElement('canvas');
            bgCanvas.width = bgImg.naturalWidth || 300;
            bgCanvas.height = bgImg.naturalHeight || 160;
            const bgCtx = bgCanvas.getContext('2d');
            bgCtx.drawImage(bgImg, 0, 0);
            const bgB64 = bgCanvas.toDataURL('image/png').split(',')[1];
            
            // 拼图块
            const jigCanvas = document.createElement('canvas');
            jigCanvas.width = jigsaw.naturalWidth || 68;
            jigCanvas.height = jigsaw.naturalHeight || 160;
            const jigCtx = jigCanvas.getContext('2d');
            jigCtx.drawImage(jigsaw, 0, 0);
            const jigB64 = jigCanvas.toDataURL('image/png').split(',')[1];
            
            // 坐标信息
            const bgRect = bgImg.getBoundingClientRect();
            const jigRect = jigsaw.getBoundingClientRect();
            const slider = document.querySelector('.yidun_slider');
            const sliderRect = slider ? slider.getBoundingClientRect() : null;
            
            return JSON.stringify({
                bgB64: bgB64,
                jigB64: jigB64,
                naturalW: bgImg.naturalWidth || 300,
                bgX: bgRect.x, bgY: bgRect.y, bgW: bgRect.width, bgH: bgRect.height,
                jigX: jigRect.x, jigW: jigRect.width, jigH: jigRect.height,
                sliderX: sliderRect ? sliderRect.x + sliderRect.width/2 : 0,
                sliderY: sliderRect ? sliderRect.y + sliderRect.height/2 : 0
            });
        } catch(e) {
            return JSON.stringify({error:'canvas_error:'+e.message});
        }
    })()""")
    try:
        return json.loads(result)
    except Exception:
        return {"error": "parse_error"}

def generate_human_trajectory(drag_dist, duration=1.2):
    """生成人类拖拽轨迹：缓动+抖动+过冲回修"""
    steps = max(30, int(drag_dist / 3))
    trajectory = []
    
    for i in range(steps + 1):
        progress = i / steps
        # 缓动函数：先快后慢（ease-out）
        eased = 1 - math.pow(1 - progress, 2.8)
        x = drag_dist * eased
        # Y 轴轻微抖动
        y = random.gauss(0, 0.8)
        # 速度变化：开始快，中间匀速，结尾减速
        base_delay = duration / steps
        if progress < 0.2:
            delay = base_delay * random.uniform(0.6, 0.9)
        elif progress < 0.8:
            delay = base_delay * random.uniform(0.8, 1.2)
        else:
            delay = base_delay * random.uniform(1.5, 2.5)
        trajectory.append((x, y, delay))
    
    # 过冲
    overshoot = random.uniform(3, 8)
    trajectory.append((drag_dist + overshoot, random.uniform(-1, 1), random.uniform(0.03, 0.08)))
    # 回修
    trajectory.append((drag_dist - random.uniform(1, 3), random.uniform(-1, 1), random.uniform(0.05, 0.1)))
    # 定位
    trajectory.append((drag_dist, 0, random.uniform(0.1, 0.2)))
    
    return trajectory

def cdp_mouse_drag(start_x, start_y, drag_dist, duration=1.2):
    """用 CDP Input.dispatchMouseEvent 模拟人类拖拽"""
    trajectory = generate_human_trajectory(drag_dist, duration)
    
    # 先随机移动几下（模拟鼠标接近）
    for _ in range(random.randint(2, 4)):
        rx = start_x + random.uniform(-30, 10)
        ry = start_y + random.uniform(-15, 15)
        cdp_send(ws_global, "Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": rx, "y": ry
        })
        time.sleep(random.uniform(0.04, 0.1))
    
    # 移到滑块
    cdp_send(ws_global, "Input.dispatchMouseEvent", {
        "type": "mouseMoved",
        "x": start_x, "y": start_y
    })
    time.sleep(random.uniform(0.08, 0.15))
    
    # 按下
    cdp_send(ws_global, "Input.dispatchMouseEvent", {
        "type": "mousePressed",
        "x": start_x, "y": start_y,
        "button": "left",
        "clickCount": 1
    })
    time.sleep(random.uniform(0.05, 0.1))
    
    # 沿轨迹拖拽
    for x_off, y_off, delay in trajectory:
        cdp_send(ws_global, "Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": start_x + x_off,
            "y": start_y + y_off,
            "button": "left"
        })
        time.sleep(delay)
    
    # 释放前短暂停顿
    time.sleep(random.uniform(0.05, 0.12))
    
    # 释放
    cdp_send(ws_global, "Input.dispatchMouseEvent", {
        "type": "mouseReleased",
        "x": start_x + drag_dist,
        "y": start_y,
        "button": "left",
        "clickCount": 1
    })
    
    sys.stderr.write("  [CDP拖拽] %d 步, 距离=%dpx, 耗时≈%.1fs\n" % (len(trajectory), drag_dist, duration))

def try_solve_ddddocr(max_retries=3):
    """用 ddddocr 识别缺口 + CDP 鼠标事件拖拽"""
    det = get_ddddocr()
    if not det:
        return False
    
    for attempt in range(max_retries):
        sys.stderr.write("  [ddddocr滑块] 尝试 %d/%d...\n" % (attempt + 1, max_retries))
        
        if attempt > 0:
            js(r"""(() => {const r=document.querySelector('.yidun_refresh');if(r)r.click();return 'ok';})()""")
            time.sleep(2)
        
        # 等待图片加载
        for _ in range(10):
            time.sleep(0.5)
            info = js(r"""(() => {
                const bg=document.querySelector('.yidun_bg-img');
                const jig=document.querySelector('.yidun_jigsaw');
                return JSON.stringify({
                    bgComplete: bg?bg.complete:false,
                    bgW: bg?bg.getBoundingClientRect().width:0,
                    jigW: jig?jig.getBoundingClientRect().width:0
                });
            })()""")
            try:
                d = json.loads(info)
                if d.get("bgComplete") and d.get("bgW", 0) > 50 and d.get("jigW", 0) > 10:
                    break
            except Exception:
                pass
        
        # 获取图片和坐标
        img_data = get_slider_images_b64()
        if "error" in img_data:
            sys.stderr.write("  [ddddocr滑块] 图片获取失败: %s\n" % img_data["error"])
            continue
        
        try:
            bg_bytes = base64.b64decode(img_data["bgB64"])
            jig_bytes = base64.b64decode(img_data["jigB64"])
        except Exception as e:
            sys.stderr.write("  [ddddocr滑块] base64解码失败: %s\n" % e)
            continue
        
        # ddddocr 识别缺口
        try:
            result = det.slide_match(jig_bytes, bg_bytes)
            gap_x = result.get("target", [0])[0]
            sys.stderr.write("  [ddddocr滑块] 缺口X=%d (原始图片坐标)\n" % gap_x)
        except Exception as e:
            sys.stderr.write("  [ddddocr滑块] slide_match失败: %s\n" % e)
            continue
        
        if gap_x <= 0:
            sys.stderr.write("  [ddddocr滑块] 缺口位置无效\n")
            continue
        
        # 计算显示坐标和拖拽距离
        natural_w = img_data.get("naturalW", 300)
        bg_w = img_data.get("bgW", 300)
        scale = bg_w / natural_w if natural_w > 0 else 1.0
        gap_display_x = gap_x * scale
        jig_x = img_data.get("jigX", 0)
        drag_dist = int(gap_display_x - (jig_x - img_data.get("bgX", 0)))
        
        if drag_dist < 10:
            sys.stderr.write("  [ddddocr滑块] 拖拽距离太小: %d (gap=%d, jigX=%.1f, bgX=%.1f, scale=%.3f)\n" % (
                drag_dist, gap_x, jig_x, img_data.get("bgX", 0), scale))
            continue
        
        sys.stderr.write("  [ddddocr滑块] 拖拽距离=%dpx (scale=%.3f)\n" % (drag_dist, scale))
        
        # CDP 鼠标拖拽
        slider_x = img_data.get("sliderX", 0)
        slider_y = img_data.get("sliderY", 0)
        cdp_mouse_drag(slider_x, slider_y, drag_dist, duration=random.uniform(1.0, 1.5))
        
        # 等待验证结果
        time.sleep(3)
        v = check_verify()
        if not v.get("type"):
            sys.stderr.write("  [ddddocr滑块] >>> 验证通过！<<<\n")
            return True
        sys.stderr.write("  [ddddocr滑块] 验证未通过，重试...\n")
    
    return False

# ====================== Canvas 备用滑块解决 ======================
SOLVE_SLIDER_JS = r"""(() => {
    const bgImg = document.querySelector('.yidun_bg-img');
    const jigsaw = document.querySelector('.yidun_jigsaw');
    const slider = document.querySelector('.yidun_slider');
    const control = document.querySelector('.yidun_control');
    if (!bgImg || !slider || !control) return JSON.stringify({ok:false, reason:'no_slider_elements'});
    let gapX = -1;
    try {
        const w = bgImg.naturalWidth || 300, h = bgImg.naturalHeight || 160;
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
    } catch(e) { return JSON.stringify({ok:false, reason:'canvas_error:'+e.message}); }
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
    return JSON.stringify({ok:true, dragDist:dragDist, gapX:gapX, finishMs:totalDelay});
})()"""

def try_solve_canvas(max_retries=2):
    """Canvas 梯度分析 + JS MouseEvent（备用方案）"""
    for attempt in range(max_retries):
        sys.stderr.write("  [Canvas滑块] 尝试 %d/%d...\n" % (attempt + 1, max_retries))
        if attempt > 0:
            js(r"""(() => {const r=document.querySelector('.yidun_refresh');if(r)r.click();return 'ok';})()""")
            time.sleep(2)
        for _ in range(10):
            time.sleep(0.5)
            info = js(r"""(() => {const bg=document.querySelector('.yidun_bg-img');const jig=document.querySelector('.yidun_jigsaw');return JSON.stringify({bgW:bg?bg.getBoundingClientRect().width:0,jigW:jig?jig.getBoundingClientRect().width:0});})()""")
            try:
                d = json.loads(info)
                if d.get("bgW", 0) > 50 and d.get("jigW", 0) > 10: break
            except Exception: pass
        result = js(SOLVE_SLIDER_JS)
        try:
            r = json.loads(result)
            if not r.get("ok"):
                sys.stderr.write("  [Canvas滑块] 失败: %s\n" % r.get("reason", "unknown"))
                continue
            sys.stderr.write("  [Canvas滑块] 拖拽距离=%s\n" % r.get("dragDist"))
        except Exception as e:
            sys.stderr.write("  [Canvas滑块] JS异常: %s\n" % e)
            continue
        time.sleep(3)
        v = check_verify()
        if not v.get("type"):
            sys.stderr.write("  [Canvas滑块] >>> 验证通过！<<<\n")
            return True
        sys.stderr.write("  [Canvas滑块] 验证未通过，重试...\n")
    return False

def handle_verify_during_scrape(timeout=120):
    """处理验证弹窗：先ddddocr，再Canvas，最后等待手动"""
    v = check_verify()
    vtype = v.get("type", "")
    sys.stderr.write("\n" + "=" * 64 + "\n")
    sys.stderr.write("[!] 检测到验证弹窗（%s: %s）\n" % (vtype, v.get("hit", "")))
    
    if vtype in ("yidun_slider", "captcha"):
        # 方案1：ddddocr + CDP 鼠标事件
        sys.stderr.write("[*] 方案1: ddddocr + CDP鼠标拖拽...\n")
        sys.stderr.flush()
        if try_solve_ddddocr(max_retries=3):
            sys.stderr.write("[OK] ddddocr 验证通过，继续采集。\n\n")
            return True
        
        # 方案2：Canvas 梯度分析 + JS 事件
        sys.stderr.write("[*] 方案2: Canvas梯度分析 + JS事件...\n")
        sys.stderr.flush()
        if try_solve_canvas(max_retries=2):
            sys.stderr.write("[OK] Canvas 验证通过，继续采集。\n\n")
            return True
        
        sys.stderr.write("[!] 自动解决失败，转为等待手动完成\n")
    
    sys.stderr.write("    >>> 请到 Chrome 窗口手动完成验证 <<<\n")
    sys.stderr.write("    脚本将等待最多 %d 秒，每 2 秒检测一次...\n" % timeout)
    sys.stderr.write("=" * 64 + "\n")
    sys.stderr.flush()
    deadline = time.time() + timeout
    last_tick = time.time()
    while time.time() < deadline:
        time.sleep(2)
        try:
            v = check_verify()
        except Exception:
            # 连接可能断开，尝试重连
            if reconnect():
                try:
                    v = check_verify()
                except Exception:
                    v = {"type": "unknown"}
            else:
                v = {"type": "unknown"}
        if not v.get("type"):
            sys.stderr.write("[OK] 验证弹窗已消失，继续采集。\n\n")
            return True
        if time.time() - last_tick >= 10:
            remain = int(deadline - time.time())
            sys.stderr.write("    ... 仍在等待（剩余 %d 秒）\n" % remain)
            last_tick = time.time()
    sys.stderr.write("[!] 等待超时，验证弹窗仍未消失。\n")
    return False

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
    sys.stderr.write("[%s] 详情页采集 v2 (ddddocr增强版)\n" % BRAND)
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
    tab = find_tab()
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
        cdp_eval(ws_global, "location.href = 'https://dian.ysbang.cn/#/home'")
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
                cdp_eval(ws_global, "location.href = 'https://dian.ysbang.cn/#/drugInfo?wholesaleid=%s&isAssemble=%s&scene=%s&trafficType=1'" % (wid, fallback_assemble, fallback_scene))
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

# -*- coding: utf-8 -*-
# browser-use harness: 采集药帮忙(YSB)批发搜索结果，输出 JSON 到 stdout。
# 已修复分页 bug：翻页靠点「下一页」按钮(.pagination-next)，排序靠点「销量」控件；
# 关键等待策略：列表必须「非空且数量稳定」才读取（避免站点重请求时的瞬间空列表导致误读 0 条）。
#
# 运行方式（browser-use CLI 只接 stdin，不接文件参数）：
#   方式 A（用默认配置，最简单）：
#     B="<managed_env>/Scripts/browser-use.exe"
#     "$B" < extract.py > vuex_raw.json 2> extract.err.log
#   方式 B（传参数，推荐）：
#     "$B" > vuex_raw.json 2> extract.err.log <<'PY'
#     import sys; sys.argv = ['extract.py', '--brand', '妇炎洁', '--pages', '3']
#     _p = r'<skill_dir>\scripts\extract.py'
#     exec(compile(open(_p, encoding='utf-8').read(), _p, 'exec'), globals())
#     PY
#
# 登录态与验证弹窗处理：
#   - 每页加载后主动检测登录失效（URL 跳登录页 / 页面文本含「登录失效」「请重新登录」等）。
#   - 每页加载后主动检测验证弹窗（滑块/极验/腾讯防水墙/含 captcha 的 iframe/可见弹窗+验证关键词）。
#   - 检测到验证弹窗 → 暂停等待用户在 9222 Chrome 手动完成，每 2 秒轮询，弹窗消失则继续；
#     超时（--verify-wait，默认 60s）则停止并提示重跑。
#
# 注意：本脚本只把结果 print 到 stdout，切勿在此直接写文件（沙箱隔离会丢）。
import json, time, sys, argparse
from urllib.parse import quote

# ====================== 默认配置（无 CLI 参数时使用）======================
DEFAULT_BRAND = "汤臣倍健"
DEFAULT_PAGES = 15
DEFAULT_VERIFY_WAIT = 60
# ========================================================================


def parse_args():
    p = argparse.ArgumentParser(description="药帮忙批发搜索采集（browser-use 沙箱内运行）")
    p.add_argument("--brand", default=DEFAULT_BRAND, help="品牌名，仅用于日志（默认 %s）" % DEFAULT_BRAND)
    p.add_argument("--search-key", default=None, help="搜索关键词，默认同 --brand，自动 URL 编码")
    p.add_argument("--pages", type=int, default=DEFAULT_PAGES, help="抓取页数（默认 %d，每页约 60 条）" % DEFAULT_PAGES)
    p.add_argument("--sort-by-sales", default="true", help="是否按销量排序（true/false，默认 true）")
    p.add_argument("--verify-wait", type=int, default=DEFAULT_VERIFY_WAIT, help="验证弹窗等待秒数（默认 %d）" % DEFAULT_VERIFY_WAIT)
    return p.parse_args()


# browser-use 沙箱里 sys.argv 可能只有脚本名（stdin 重定向模式），argparse 会 SystemExit。
# 提供回退：parse_args 失败时用默认值。
def get_opts():
    try:
        return parse_args()
    except SystemExit:
        class _O:
            brand = DEFAULT_BRAND
            search_key = None
            pages = DEFAULT_PAGES
            sort_by_sales = "true"
            verify_wait = DEFAULT_VERIFY_WAIT
        return _O()


opts = get_opts()
BRAND = opts.brand
SEARCH_KEY_RAW = opts.search_key if opts.search_key else opts.brand
SEARCH_KEY = quote(SEARCH_KEY_RAW)
TOTAL_PAGES = opts.pages
SORT_BY_SALES = str(opts.sort_by_sales).lower() in ("true", "1", "yes")
VERIFY_WAIT = opts.verify_wait

READ_JS = r"""(() => {
  const app = document.querySelector('#app');
  if (!app || !app.__vue__ || !app.__vue__.$store) return JSON.stringify({error:'no_vue'});
  const list = app.__vue__.$store.state.drugList.drugList || [];
  const wraps = Array.from(document.querySelectorAll('.all-goods-wrapper'));
  const used = new Set();
  const res = [];
  for (const it of list) {
    let block = '';
    if (it.provider_name) {
      for (let i = 0; i < wraps.length; i++) {
        if (used.has(i)) continue;
        const t = wraps[i].innerText;
        if (t.indexOf(it.drugname) !== -1 && t.indexOf(it.provider_name) !== -1) {
          block = t; used.add(i); break;
        }
      }
    }
    if (!block) {
      for (let i = 0; i < wraps.length; i++) {
        if (used.has(i)) continue;
        if (wraps[i].innerText.indexOf(it.drugname) !== -1) {
          block = wraps[i].innerText; used.add(i); break;
        }
      }
    }
    res.push({
      drugname: it.drugname, specification: it.specification, minamount: it.minamount,
      drugimageurl: it.drugimageurl, brand: it.brand, provider_name: it.provider_name,
      unit: it.unit, wholesaleAmount: it.wholesaleAmount, priceToken: it.priceToken,
      alreadysales: it.alreadysales, wholesaleid: it.wholesaleid,
      domText: (block||'').replace(/\s+/g,' ').slice(0,1500)
    });
  }
  return JSON.stringify({count: res.length, items: res, matched: used.size, total_cards: wraps.length});
})()"""


def store_len():
    try:
        return int(js(r"""(()=>{const s=document.querySelector('#app').__vue__.$store;return (s.state.drugList.drugList||[]).length;})()"""))
    except Exception:
        return 0

def first_name():
    try:
        return js(r"""(()=>{const l=document.querySelector('#app').__vue__.$store.state.drugList.drugList||[];return l.length?l[0].drugname:'';})()""")
    except Exception:
        return ""

def wait_ready(timeout=25):
    """等列表非空且数量连续稳定 2 次（说明一次请求已完成）。"""
    last = -1; stable = 0
    for _ in range(timeout):
        n = store_len()
        if n > 0:
            if n == last:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
            last = n
        time.sleep(1)
    return store_len() > 0

def click_next():
    return js(r"""(()=>{const b=Array.from(document.querySelectorAll('button,li,a')).find(x=>{const c=(x.className||'').toString();const t=(x.innerText||'').trim();return c.indexOf('pagination-next')!==-1||t==='下一页';});if(b){b.click();return true;}return false;})()""")

def click_sales_sort():
    return js(r"""(()=>{const e=Array.from(document.querySelectorAll('*')).find(x=>{const t=(x.innerText||'').trim();const c=(x.className||'').toString();return c.indexOf('sort-item')!==-1&&t==='销量';});if(e){e.click();return true;}return false;})()""")


# ---------- 登录态检测 ----------
def check_login():
    r = js(r"""(() => {
        const u = location.href || '';
        const body = (document.body && document.body.innerText) || '';
        const urlLogin = /[#/]login\b|\/login\.html/i.test(u);
        const kws = ['请先登录', '登录失效', '登录已过期', '请重新登录', '重新登录', '登录态已失效', '未登录', '请登录后'];
        let kw = null;
        for (const k of kws) { if (body.indexOf(k) !== -1) { kw = k; break; } }
        return JSON.stringify({url: u, url_login: urlLogin, kw: kw});
    })()""")
    try:
        d = json.loads(r)
        d["on_login"] = bool(d.get("url_login") or d.get("kw"))
        return d
    except Exception:
        return {"on_login": False}


# ---------- 验证弹窗检测 ----------
VERIFY_JS = r"""(() => {
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
            if (cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0') return JSON.stringify({type:'selector', hit:s});
        }
    }
    const kws = ['拖动滑块','完成验证','请完成下方验证','请完成验证','安全验证','操作过于频繁','请验证身份','滑动验证','人机验证','请拖动','拖动完成验证','请按住滑块','验证失败','请重新验证'];
    const overlays = document.querySelectorAll('.modal,.dialog,.popup,.mask,.overlay,.toast,[class*="modal"],[class*="dialog"],[class*="popup"],[class*="mask"],[class*="verify"],[class*="captcha"],[class*="slider"]');
    for (const o of overlays) {
        const cs = window.getComputedStyle(o);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const t = o.innerText || '';
        if (!t || t.length > 500) continue;
        for (const k of kws) { if (t.indexOf(k) !== -1) return JSON.stringify({type:'modal+kw', hit:k}); }
    }
    return JSON.stringify({type:null, hit:''});
})()"""

def check_verify():
    try:
        return json.loads(js(VERIFY_JS))
    except Exception:
        return {"type": None, "hit": ""}

# ---------- JS 自动解滑块（采集过程中风控触发时用）----------
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


def try_auto_solve_slider(max_retries=3):
    """尝试自动解决滑块验证（JS 图像分析+模拟拖拽，刷新重试）。
    返回 True=验证通过，False=失败。"""
    for attempt in range(max_retries):
        sys.stderr.write("  [滑块自动解决] 尝试 %d/%d...\n" % (attempt+1, max_retries))
        if attempt > 0:
            js(r"""(() => {
                const refresh = document.querySelector('.yidun_refresh');
                if (refresh) refresh.click();
                return 'ok';
            })()""")
            time.sleep(2)
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
        time.sleep(3)
        v = check_verify()
        if not v.get("type"):
            sys.stderr.write("  [滑块自动解决] >>> 验证通过！<<<\n")
            return True
        sys.stderr.write("  [滑块自动解决] 验证未通过，重试...\n")
    return False


def handle_verify(timeout):
    sys.stderr.write("\n" + "=" * 64 + "\n")
    sys.stderr.write("[!] 检测到验证弹窗（滑块/验证码/风控）。\n")
    # 先尝试自动解决（易盾滑块用 JS 图像分析+模拟拖拽）
    v = check_verify()
    if v.get("type") == "yidun_slider":
        sys.stderr.write("[*] 尝试自动解决滑块...\n")
        sys.stderr.flush()
        if try_auto_solve_slider(max_retries=3):
            sys.stderr.write("[OK] 滑块自动解决成功，继续采集。\n\n")
            return True
        sys.stderr.write("[!] 自动解决失败，转为等待手动完成\n")

    sys.stderr.write("    >>> 请到 9222 Chrome 窗口手动完成验证 <<<\n")
    sys.stderr.write("    脚本将等待最多 %d 秒，每 2 秒检测一次弹窗是否消失...\n" % timeout)
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
            sys.stderr.write("    ... 仍在等待（剩余 %d 秒，当前弹窗: %s）\n" % (remain, v.get("hit", "")))
            last_tick = time.time()
    sys.stderr.write("[!] 等待超时，验证弹窗仍未消失。请手动完成后重跑。\n")
    return False


# ---------- 主流程 ----------
ensure_real_tab()
url = "https://dian.ysbang.cn/#/indexContent?page=1&pagesize=60&searchkey=%s&operationtype=1" % SEARCH_KEY
new_tab(url)
wait_for_load()

# 首次加载后检测验证弹窗 + 登录态
v = check_verify()
if v.get("type"):
    if not handle_verify(VERIFY_WAIT):
        sys.exit(1)

if not wait_ready():
    # 列表没加载：先查验证弹窗，再查登录态
    v = check_verify()
    if v.get("type"):
        if not handle_verify(VERIFY_WAIT):
            sys.exit(1)
        wait_ready()
    else:
        li = check_login()
        if li.get("on_login"):
            sys.stderr.write("[%s] 登录态失效（%s），请重新登录 9222 Chrome 后重试。\n" % (BRAND, li.get("kw") or li.get("url") or ""))
            sys.exit(1)
        sys.stderr.write("ERROR: 初始列表未加载（非登录/验证问题），可能站点异常\n")
        sys.exit(1)

if SORT_BY_SALES:
    click_sales_sort()
    wait_ready()
    sys.stderr.write("[%s] 已按销量排序\n" % BRAND)

all_items = []
prev_first = first_name()
stopped = False
for page in range(1, TOTAL_PAGES + 1):
    if not wait_ready():
        sys.stderr.write("page %d: 列表未就绪，停止\n" % page); stopped = True; break
    # 每页读取后检测验证弹窗（风控可能在翻页时触发）
    v = check_verify()
    if v.get("type"):
        if not handle_verify(VERIFY_WAIT):
            stopped = True; break
        if not wait_ready():
            sys.stderr.write("page %d: 验证后列表未恢复，停止\n" % page); stopped = True; break
    r = js(READ_JS)
    try:
        data = json.loads(r)
    except Exception as e:
        sys.stderr.write("page %d parse err: %s\n" % (page, e)); continue
    if "error" in data:
        sys.stderr.write("page %d error: %s\n" % (page, data["error"])); stopped = True; break
    items = data["items"]
    all_items.extend(items)
    sys.stderr.write("[%s] page %d got %d (total %d)\n" % (BRAND, page, len(items), len(all_items)))
    if page >= TOTAL_PAGES:
        break
    if not click_next():
        sys.stderr.write("page %d: 未找到下一页按钮，停止\n" % page); stopped = True; break
    # 等翻页生效：列表重新出现且首条变化（排除重请求时的瞬间空列表）
    changed = False
    for _ in range(25):
        time.sleep(1)
        f = first_name()
        if f and f != prev_first:
            changed = True; break
    if not changed:
        sys.stderr.write("page %d: 点击后列表未变化，停止\n" % page); stopped = True; break
    prev_first = first_name()

sys.stderr.write("[%s] TOTAL %d (pages=%d%s)\n" % (BRAND, len(all_items), TOTAL_PAGES, ", stopped early" if stopped else ""))
print(json.dumps(all_items, ensure_ascii=False))

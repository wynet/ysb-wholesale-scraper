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
import json, time, sys, argparse, os, types
from urllib.parse import quote

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
    p.add_argument("--start-page", type=int, default=1, help="起始页码（断点续传，默认 1）")
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
            start_page = 1
        return _O()


opts = get_opts()
BRAND = opts.brand
SEARCH_KEY_RAW = opts.search_key if opts.search_key else opts.brand
SEARCH_KEY = quote(SEARCH_KEY_RAW)
TOTAL_PAGES = opts.pages
SORT_BY_SALES = str(opts.sort_by_sales).lower() in ("true", "1", "yes")
VERIFY_WAIT = opts.verify_wait
START_PAGE = max(1, opts.start_page)

READ_JS = ysb_parser.READ_JS


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


# ---------- 验证弹窗检测（调用公共模块）----------
def check_verify():
    return ysb_parser.check_verify(js)

def handle_verify(timeout):
    """处理验证弹窗：自动解决(JS canvas分析) → 失败转人工等待。"""
    def _log(msg):
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    return ysb_parser.handle_verify_browser(js, timeout=timeout, log_fn=_log)


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
    # 断点续传：跳过已采集的页（仅翻页不采集）
    if page < START_PAGE:
        sys.stderr.write("[%s] page %d: 跳过（断点续传从 page %d 开始）\n" % (BRAND, page, START_PAGE))
        if not click_next():
            sys.stderr.write("page %d: 未找到下一页按钮，停止\n" % page); stopped = True; break
        for _ in range(25):
            time.sleep(1)
            f = first_name()
            if f and f != prev_first:
                break
        prev_first = first_name()
        continue
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

sys.stderr.write("[%s] TOTAL %d (pages=%d-%d%s)\n" % (BRAND, len(all_items), START_PAGE, TOTAL_PAGES, ", stopped early" if stopped else ""))
print(json.dumps(all_items, ensure_ascii=False))

# -*- coding: utf-8 -*-
"""CDP 直连版列表采集脚本（不依赖 browser-use）

用 websocket-client 直连 Chrome 9222，搜索关键词并读取 Vuex store。
用法: python cdp_extract.py --brand 云南白药 --pages 1
输出: JSON 到 stdout，日志到 stderr
"""
import json, time, sys, argparse, os
import websocket
import ysb_common
import ysb_parser

ws_global = None

def js(expr):
    return ysb_common.cdp_eval(ws_global, expr)

def parse_args():
    p = argparse.ArgumentParser(description="药帮列表采集(CDP直连)")
    p.add_argument("--brand", default="云南白药", help="品牌名/搜索关键词")
    p.add_argument("--pages", type=int, default=1, help="抓取页数")
    p.add_argument("--sort-by-sales", default="true", help="按销量排序")
    return p.parse_args()

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

def check_verify():
    return ysb_parser.check_verify(js)

def handle_verify_during_scrape(timeout=60):
    def _log(msg):
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
    return ysb_parser.handle_verify_browser(js, timeout=timeout, log_fn=_log)

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

def main():
    global ws_global
    opts = parse_args()
    BRAND = opts.brand
    TOTAL_PAGES = opts.pages
    SORT_BY_SALES = str(opts.sort_by_sales).lower() in ("true", "1", "yes")

    from urllib.parse import quote
    SEARCH_KEY = quote(BRAND)

    sys.stderr.write("=" * 64 + "\n")
    sys.stderr.write("[%s] 列表采集 (CDP直连, %d页)\n" % (BRAND, TOTAL_PAGES))
    sys.stderr.write("=" * 64 + "\n")

    # 连接 Chrome
    tab = ysb_common.find_tab()
    if not tab:
        sys.stderr.write("ERROR: 没有 Chrome 标签页\n")
        sys.exit(1)
    sys.stderr.write("使用 tab: %s\n" % tab["url"][:80])
    ws_global = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)

    # 导航到搜索页
    url = "https://dian.ysbang.cn/#/indexContent?page=1&pagesize=60&searchkey=%s&operationtype=1" % SEARCH_KEY
    sys.stderr.write("导航到搜索页: %s\n" % BRAND)
    js("location.hash = '%s'" % url.split('#')[1])
    time.sleep(5)

    # 检测验证弹窗
    v = check_verify()
    if v.get("type"):
        sys.stderr.write("检测到验证弹窗，处理中...\n")
        if not handle_verify_during_scrape(timeout=60):
            sys.stderr.write("验证未解决，退出\n")
            sys.exit(1)
        time.sleep(2)

    # 等待列表加载
    if not wait_ready():
        v = check_verify()
        if v.get("type"):
            if not handle_verify_during_scrape(timeout=60):
                sys.exit(1)
            wait_ready()
        else:
            li = check_login()
            if li.get("on_login"):
                sys.stderr.write("登录态失效，请重新登录\n")
                sys.exit(1)
            sys.stderr.write("ERROR: 列表未加载\n")
            sys.exit(1)

    # 按销量排序
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
        v = check_verify()
        if v.get("type"):
            if not handle_verify_during_scrape(timeout=60):
                stopped = True; break
            if not wait_ready():
                stopped = True; break
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
        changed = False
        for _ in range(25):
            time.sleep(1)
            f = first_name()
            if f and f != prev_first:
                changed = True; break
        if not changed:
            sys.stderr.write("page %d: 点击后列表未变化，停止\n" % page); stopped = True; break
        prev_first = first_name()

    sys.stderr.write("[%s] TOTAL %d (pages=1-%d%s)\n" % (BRAND, len(all_items), TOTAL_PAGES, ", stopped early" if stopped else ""))
    try:
        ws_global.close()
    except Exception:
        pass
    print(json.dumps(all_items, ensure_ascii=False))

if __name__ == "__main__":
    main()

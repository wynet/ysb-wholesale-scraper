# -*- coding: utf-8 -*-
"""全自动编排脚本 — 一条命令完成 采集→详情→出表 全流程

用法:
    python run.py --brand 云南白药 --pages 1 --top-n 5
    python run.py --brand 妇炎洁 --pages 3 --top-n 0
    python run.py --brand 云南白药 --pages 1 --top-n 5 --phone 18975626916 --password Ab123456

特性:
    - 自动检测 Chrome 9222 调试端口
    - 自动检测登录态，未登录则从 profile.json 读取凭证自动登录
    - 自动检测 browser-use 可用性（不可用则用 CDP 直连版）
    - 自动串联：登录→列表采集→详情采集→出表
    - 中间文件自动管理，不需要手动 Copy-Item 或重定向
    - 最终产物直接输出到当前工作目录

首次使用:
    需要通过 --phone 和 --password 提供登录凭证，
    之后自动保存到 profile.json，后续运行无需再传。
"""
import json, os, sys, time, argparse, subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
PROFILE_PATH = os.path.join(SKILL_DIR, "profile.json")

PYTHON = sys.executable  # 当前 Python 解释器


def log(msg):
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def load_profile():
    try:
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_profile(profile):
    try:
        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log("[warn] 保存 profile.json 失败: %s" % e)


def check_chrome_9222():
    """检测 Chrome 9222 调试端口是否可用"""
    import urllib.request
    try:
        r = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5)
        tabs = json.loads(r.read())
        return len(tabs) > 0
    except Exception:
        return False


def check_login_status():
    """通过 CDP 检测当前是否已登录"""
    import urllib.request, websocket
    try:
        r = urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=5)
        tabs = json.loads(r.read())
        tab = None
        for t in tabs:
            if t.get("type") == "page" and "dian.ysbang.cn" in t.get("url", ""):
                tab = t
                break
        if not tab:
            for t in tabs:
                if t.get("type") == "page":
                    tab = t
                    break
        if not tab:
            return False, "no_tab"
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
            "expression": "(() => { const h = location.hash || ''; const t = (document.body && document.body.innerText.substring(0,300)) || ''; const isLogin = h.indexOf('/login') !== -1 || t.indexOf('账户登录') !== -1; return JSON.stringify({isLogin: isLogin, hash: h.substring(0,60)}); })()",
            "returnByValue": True
        }}))
        resp = json.loads(ws.recv())
        ws.close()
        result = resp.get("result", {}).get("result", {})
        info = json.loads(result.get("value", "{}"))
        return not info.get("isLogin", True), info.get("hash", "")
    except Exception as e:
        return False, str(e)


def check_browser_use():
    """检测 browser-use 是否可用"""
    try:
        result = subprocess.run(
            [PYTHON, "-c", "import browser_use; print('ok')"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0 and "ok" in result.stdout
    except Exception:
        return False


def run_login(phone, password):
    """执行自动登录"""
    log("\n[步骤0] 自动登录...")
    result = subprocess.run(
        [PYTHON, os.path.join(SCRIPT_DIR, "auto_login.py"), phone, password],
        capture_output=True, text=True, encoding="utf-8", timeout=180
    )
    log(result.stdout)
    if result.stderr:
        log(result.stderr)
    if result.returncode != 0:
        log("[ERROR] 登录失败 (exit code: %d)" % result.returncode)
        return False
    log("[OK] 登录成功")
    return True


def run_extract(brand, pages, use_browser_use):
    """执行列表采集，返回输出文件路径"""
    output_file = os.path.join(os.getcwd(), "vuex_raw.json")
    log("\n[步骤1] 列表采集 (%s, %d页)..." % (brand, pages))

    if use_browser_use:
        # browser-use 方式：通过 stdin 传参
        # 查找 browser-use 可执行文件
        bu_path = None
        for p in [
            os.path.join(os.path.dirname(PYTHON), "Scripts", "browser-use.exe"),
            os.path.join(os.path.dirname(os.path.dirname(PYTHON)), "Scripts", "browser-use.exe"),
        ]:
            if os.path.exists(p):
                bu_path = p
                break
        if not bu_path:
            log("[warn] browser-use 可导入但未找到可执行文件，回退到 CDP 模式")
            use_browser_use = False

    if use_browser_use:
        extract_script = os.path.join(SCRIPT_DIR, "extract.py")
        stdin_code = (
            "import sys; sys.argv = ['extract.py', '--brand', '%s', '--pages', '%d'];"
            " _p = r'%s';"
            " exec(compile(open(_p, encoding='utf-8').read(), _p, 'exec'), globals())"
        ) % (brand, pages, extract_script)
        result = subprocess.run(
            [bu_path],
            input=stdin_code,
            capture_output=True, text=True, encoding="utf-8", timeout=300
        )
    else:
        # CDP 直连方式
        result = subprocess.run(
            [PYTHON, os.path.join(SCRIPT_DIR, "cdp_extract.py"),
             "--brand", brand, "--pages", str(pages)],
            capture_output=True, text=True, encoding="utf-8", timeout=300
        )

    if result.stderr:
        log(result.stderr)
    if result.returncode != 0:
        log("[ERROR] 列表采集失败 (exit code: %d)" % result.returncode)
        return None

    # 写入文件（避免 PowerShell 重定向编码问题）
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(result.stdout)

    # 验证
    try:
        data = json.loads(result.stdout)
        log("[OK] 列表采集成功: %d 条" % len(data))
    except Exception:
        log("[ERROR] 列表采集输出不是有效 JSON")
        return None
    return output_file


def run_detail(input_file, brand, top_n):
    """执行详情页采集，返回输出文件路径"""
    existing_file = os.path.join(os.getcwd(), "detail_data.json")
    output_file = os.path.join(os.getcwd(), "detail_data.json")
    log("\n[步骤2] 详情页采集 (top-%s)..." % (top_n if top_n > 0 else "all"))

    args = [
        PYTHON, os.path.join(SCRIPT_DIR, "cdp_fetch_detail_v2.py"),
        "--input", input_file,
        "--existing", existing_file,
        "--brand", brand,
    ]
    if top_n > 0:
        args.extend(["--top-n", str(top_n)])

    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", timeout=1800
    )

    if result.stderr:
        log(result.stderr)
    if result.returncode != 0:
        log("[ERROR] 详情采集失败 (exit code: %d)" % result.returncode)
        # 即使失败也继续出表（部分数据也比没有强）
        log("[warn] 继续用已有数据出表...")

    # cdp_fetch_detail_v2.py 输出到 stdout，但 --existing 文件也有数据
    # 优先用 stdout 的完整数据写入文件
    if result.stdout and result.stdout.strip().startswith("{"):
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result.stdout)
    elif os.path.exists(existing_file):
        output_file = existing_file
    else:
        log("[warn] 无详情数据，跳过详情整合")
        return None

    try:
        with open(output_file, encoding="utf-8-sig") as f:
            data = json.load(f)
        log("[OK] 详情采集完成: %d 条" % len(data))
    except Exception:
        log("[warn] 详情数据解析失败，跳过")
        return None
    return output_file


def run_process(brand, input_file, detail_file):
    """执行出表，返回 (xlsx_path, html_path)"""
    log("\n[步骤3] 生成 Excel + HTML 报表...")

    args = [
        PYTHON, os.path.join(SCRIPT_DIR, "process.py"),
        "--brand", brand,
        "--input", input_file,
    ]
    if detail_file:
        args.extend(["--detail", detail_file])

    result = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", timeout=120
    )

    if result.stdout:
        log(result.stdout)
    if result.stderr:
        log(result.stderr)
    if result.returncode != 0:
        log("[ERROR] 出表失败 (exit code: %d)" % result.returncode)
        return None, None

    # 查找生成的文件
    import datetime
    today = datetime.date.today().strftime("%Y-%m-%d")
    xlsx = os.path.join(os.getcwd(), "%s_热销统计_系列合并_%s.xlsx" % (brand, today))
    html = os.path.join(os.getcwd(), "%s_热销采购分析_%s.html" % (brand, today))
    return xlsx, html


def main():
    parser = argparse.ArgumentParser(description="药帮采集全流程一键执行")
    parser.add_argument("--brand", required=True, help="品牌名/搜索关键词")
    parser.add_argument("--pages", type=int, default=1, help="列表采集页数（默认1）")
    parser.add_argument("--top-n", type=int, default=0, help="详情页采集数（0=全量）")
    parser.add_argument("--phone", default="", help="登录手机号（首次需提供，之后自动记忆）")
    parser.add_argument("--password", default="", help="登录密码（首次需提供，之后自动记忆）")
    parser.add_argument("--skip-login", action="store_true", help="跳过登录检测（确认已登录时用）")
    args = parser.parse_args()

    log("=" * 64)
    log("药帮采集全流程 | 品牌: %s | 页数: %d | 详情: %s" % (
        args.brand, args.pages, "top-%d" % args.top_n if args.top_n > 0 else "全量"))
    log("=" * 64)

    # 1. 检测 Chrome 9222
    if not check_chrome_9222():
        log("[ERROR] Chrome 9222 调试端口不可用")
        log("请启动: chrome.exe --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=<目录> --no-first-run")
        sys.exit(1)
    log("[OK] Chrome 9222 可用")

    # 2. 凭证管理
    profile = load_profile()
    creds = profile.get("credentials", {})
    phone = args.phone or creds.get("phone", "")
    password = args.password or creds.get("password", "")

    # 3. 登录检测
    if not args.skip_login:
        logged_in, info = check_login_status()
        if logged_in:
            log("[OK] 已登录 (当前: %s)" % info)
        else:
            if not phone or not password:
                log("[ERROR] 未登录且无保存的凭证")
                log("请提供: --phone <手机号> --password <密码>")
                log("（首次提供后自动保存，后续无需再传）")
                sys.exit(1)
            # 保存凭证
            if not creds:
                profile["credentials"] = {}
            profile["credentials"]["phone"] = phone
            profile["credentials"]["password"] = password
            save_profile(profile)
            log("[OK] 凭证已保存到 profile.json")

            if not run_login(phone, password):
                sys.exit(1)
    else:
        log("[SKIP] 跳过登录检测")

    # 4. 检测 browser-use
    has_bu = check_browser_use()
    log("[INFO] browser-use: %s" % ("可用" if has_bu else "不可用，使用 CDP 直连模式"))

    # 5. 列表采集
    vuex_file = run_extract(args.brand, args.pages, has_bu)
    if not vuex_file:
        sys.exit(1)

    # 6. 详情采集
    detail_file = run_detail(vuex_file, args.brand, args.top_n)

    # 7. 出表
    xlsx, html = run_process(args.brand, vuex_file, detail_file)

    # 8. 汇总
    log("\n" + "=" * 64)
    log("全流程完成！")
    log("=" * 64)
    if xlsx and os.path.exists(xlsx):
        log("  Excel: %s" % xlsx)
    if html and os.path.exists(html):
        log("  HTML:  %s" % html)
    if not xlsx or not os.path.exists(xlsx):
        log("[WARN] Excel 文件未生成")
    if not html or not os.path.exists(html):
        log("[WARN] HTML 文件未生成")


if __name__ == "__main__":
    main()

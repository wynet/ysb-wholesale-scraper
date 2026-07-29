# -*- coding: utf-8 -*-
# 查看skill成长报告：运行次数、累计采集、各品牌历史、数据质量、价格趋势。
#
# 用法：
#   python scripts/grow.py                  # 全局成长摘要（含趋势/质量/配置建议）
#   python scripts/grow.py --brand 妇炎洁    # 某品牌详细历史
#   python scripts/grow.py --runs 10         # 最近 N 次运行
#   python scripts/grow.py --trend           # TOP产品价格/销量趋势
#   python scripts/grow.py --quality         # 数据质量追踪
#   python scripts/grow.py --html            # 生成HTML可视化报告
import json, os, sys, argparse
from datetime import datetime

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(SKILL_DIR, "profile.json")
HISTORY = os.path.join(os.getcwd(), "sales_history.json")


def load():
    try:
        with open(PROFILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("尚无成长记录（profile.json 不存在）。跑一次 process.py 即自动创建。")
        sys.exit(0)
    except Exception as e:
        print("读取 profile.json 失败:", e)
        sys.exit(1)


def load_history():
    try:
        with open(HISTORY, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def fmt_date(s):
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s


def fmt_date_short(s):
    try:
        return datetime.fromisoformat(s).strftime("%m-%d %H:%M")
    except Exception:
        return s


# ====================== 全局摘要 ======================
def print_summary(prof):
    print("=" * 64)
    print("  skill 成长报告（v%s）" % prof.get("version", 1))
    print("=" * 64)
    print("首次运行: %s" % fmt_date(prof.get("first_run", "")))
    print("最近运行: %s" % fmt_date(prof.get("last_run", "")))
    print("总运行次数: %d" % prof.get("total_runs", 0))
    print("累计采集商品: %d 条" % prof.get("total_items", 0))
    print()

    brands = prof.get("brands", {})
    if brands:
        print("-- 品牌排行（按运行次数）--")
        ranked = sorted(brands.items(), key=lambda kv: kv[1]["runs"], reverse=True)
        for i, (name, b) in enumerate(ranked, 1):
            lr = b.get("last_result", {})
            avg_t = b.get("avg_elapsed", 0)
            best_q = b.get("best_quality", 0)
            print("  %d. %s -- 跑 %d 次 . 累计 %d 条 . 上次 %s 条/%d 产品" %
                  (i, name, b["runs"], b.get("total_items", 0),
                   lr.get("items", 0), lr.get("products", 0)))
            print("     TOP1: %s (销量 %s)" %
                  ((lr.get("top1_name", "") or "-")[:35], lr.get("top1_sales", "-")))
            if avg_t:
                print("     平均耗时: %.1fs . 最佳质量: %.1f%%" % (avg_t, best_q))
        print()

    # 配置记忆
    last_cfg = prof.get("last_config", {})
    if last_cfg:
        print("-- 上次运行配置 --")
        print("  品牌: %s . 输入: %s . 有详情: %s" %
              (last_cfg.get("brand", "-"), last_cfg.get("input", "-"),
               "是" if last_cfg.get("has_detail") else "否"))
        print()

    # 数据质量
    qh = prof.get("quality_history", [])
    if qh:
        latest = qh[-1]
        avg_rate = sum(q.get("rate", 0) for q in qh) / len(qh) if qh else 0
        print("-- 数据质量 --")
        print("  最近: %.1f%% (%d/%d 已采集 . 待补采 %d)" %
              (latest.get("rate", 0), latest.get("collected", 0),
               latest.get("collected", 0) + latest.get("pending", 0),
               latest.get("pending", 0)))
        print("  历史: %d 次记录 . 平均质量 %.1f%%" % (len(qh), avg_rate))
        if len(qh) >= 2:
            prev = qh[-2]
            delta = latest.get("rate", 0) - prev.get("rate", 0)
            arrow = "+" if delta > 0 else ("-" if delta < 0 else "=")
            print("  趋势: %s %.1f%%（对比上次 %s）" % (arrow, abs(delta), fmt_date_short(prev.get("date", ""))))
        print()

    # 趋势快报
    hist = load_history()
    if len(hist) >= 2:
        print("-- 价格/销量趋势快报 --")
        print_trend(hist, limit=3)


# ====================== 趋势分析 ======================
def print_trend(hist, limit=5):
    if len(hist) < 2:
        print("  趋势分析需要至少 2 次快照（当前 %d 次），多跑几次 process.py 即可积累。" % len(hist))
        return
    prev = hist[-2]
    curr = hist[-1]
    print("  对比: %s -> %s" % (prev.get("date", ""), curr.get("date", "")))
    print()

    prev_names = {p["name"]: p for p in prev.get("top10", [])}
    curr_names = {p["name"]: p for p in curr.get("top10", [])}

    shown = 0
    for name in list(curr_names.keys())[:limit]:
        if name not in prev_names:
            continue
        p_prev = prev_names[name]
        p_curr = curr_names[name]

        sales_delta = p_curr.get("total_sales", 0) - p_prev.get("total_sales", 0)
        prev_prices = p_prev.get("lowest3_prices", [])
        curr_prices = p_curr.get("lowest3_prices", [])

        sales_arrow = "+" if sales_delta > 0 else ("-" if sales_delta < 0 else "=")
        sales_str = "%s%d" % (sales_arrow, abs(sales_delta)) if sales_delta != 0 else "= 持平"

        price_str = "-"
        if prev_prices and curr_prices:
            price_delta = curr_prices[0] - prev_prices[0]
            price_arrow = "+" if price_delta > 0 else ("-" if price_delta < 0 else "=")
            price_str = "%s%.2f (%.2f->%.2f)" % (price_arrow, abs(price_delta),
                                                  prev_prices[0], curr_prices[0])

        rank_change = p_prev.get("rank", 0) - p_curr.get("rank", 0)
        rank_str = ""
        if rank_change > 0:
            rank_str = " (排名+%d)" % rank_change
        elif rank_change < 0:
            rank_str = " (排名-%d)" % abs(rank_change)

        print("  #%d %s%s" % (p_curr.get("rank", 0), name[:35], rank_str))
        print("       销量: %d -> %d (%s)" %
              (p_prev.get("total_sales", 0), p_curr.get("total_sales", 0), sales_str))
        print("       最低价: %s" % price_str)
        print()
        shown += 1

    if shown == 0:
        print("  （两次快照无共同产品，无法对比）")


# ====================== 数据质量追踪 ======================
def print_quality(prof):
    qh = prof.get("quality_history", [])
    if not qh:
        print("尚无数据质量记录。")
        return
    print("=" * 64)
    print("  数据质量追踪（共 %d 次记录）" % len(qh))
    print("=" * 64)
    print("%-18s %-12s %-8s %-8s %-8s %s" % ("时间", "品牌", "采集率", "已采集", "待补采", "趋势"))
    print("-" * 64)
    recent = qh[-20:]
    for i, q in enumerate(recent):
        rate = q.get("rate", 0)
        ok = q.get("collected", 0)
        pending = q.get("pending", 0)
        total = ok + pending
        arrow = ""
        if i > 0:
            prev_rate = recent[i - 1].get("rate", 0)
            if rate > prev_rate:
                arrow = "+"
            elif rate < prev_rate:
                arrow = "-"
            else:
                arrow = "="
        print("%-18s %-12s %5.1f%%  %4d/%-4d %4d     %s" %
              (fmt_date_short(q.get("date", "")), q.get("brand", ""),
               rate, ok, total, pending, arrow))

    avg = sum(q.get("rate", 0) for q in qh) / len(qh) if qh else 0
    best = max(q.get("rate", 0) for q in qh) if qh else 0
    worst = min(q.get("rate", 0) for q in qh) if qh else 0
    print("-" * 64)
    print("平均: %.1f%% . 最高: %.1f%% . 最低: %.1f%%" % (avg, best, worst))


# ====================== 品牌历史 ======================
def print_brand_history(prof, brand):
    brands = prof.get("brands", {})
    if brand not in brands:
        print("品牌「%s」尚无运行记录。" % brand)
        return
    b = brands[brand]
    print("=" * 64)
    print("  %s 的运行历史" % brand)
    print("=" * 64)
    print("首次: %s . 最近: %s . 共 %d 次 . 累计 %d 条" %
          (fmt_date(b.get("first_run", "")), fmt_date(b.get("last_run", "")),
           b["runs"], b.get("total_items", 0)))
    if b.get("avg_elapsed"):
        print("平均耗时: %.1fs . 最佳质量: %.1f%%" % (b["avg_elapsed"], b.get("best_quality", 0)))
    print()
    print("-- 历次运行 --")
    runs = [r for r in prof.get("runs", []) if r.get("brand") == brand]
    for r in runs:
        res = r.get("result", {})
        q = r.get("quality", {})
        elapsed = r.get("elapsed_sec", 0)
        q_str = " . 质量 %.1f%%" % q.get("rate", 0) if q else ""
        t_str = " . %.1fs" % elapsed if elapsed else ""
        print("  %s . %s 条/%d 产品%s%s . TOP1: %s (销量 %s)" %
              (fmt_date(r.get("date", "")), res.get("items", 0),
               res.get("products", 0), t_str, q_str,
               (res.get("top1_name", "") or "-")[:30],
               res.get("top1_sales", "-")))


# ====================== 最近运行 ======================
def print_recent_runs(prof, n):
    runs = prof.get("runs", [])[-n:]
    print("=" * 64)
    print("  最近 %d 次运行" % len(runs))
    print("=" * 64)
    for r in runs:
        res = r.get("result", {})
        q = r.get("quality", {})
        elapsed = r.get("elapsed_sec", 0)
        q_str = " . 质量 %.1f%%" % q.get("rate", 0) if q else ""
        t_str = " . %.1fs" % elapsed if elapsed else ""
        print("  %s . %s . %s 条/%d 产品%s%s" %
              (fmt_date(r.get("date", "")), r.get("brand", ""),
               res.get("items", 0), res.get("products", 0), t_str, q_str))


# ====================== HTML 可视化报告 ======================
def generate_html(prof, hist):
    html_path = os.path.join(os.getcwd(), "skill_成长报告.html")
    brands = prof.get("brands", {})
    qh = prof.get("quality_history", [])
    runs = prof.get("runs", [])

    avg_quality = 0
    if qh:
        avg_quality = round(sum(q.get("rate", 0) for q in qh) / len(qh), 1)

    html = []
    html.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">')
    html.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    html.append('<title>skill 成长报告</title><style>')
    html.append('* { margin: 0; padding: 0; box-sizing: border-box; }')
    html.append('body { font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; background: #f0f2f5; color: #333; }')
    html.append('.container { max-width: 1100px; margin: 0 auto; padding: 20px; }')
    html.append('.header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: #fff; padding: 30px; border-radius: 12px; margin-bottom: 20px; }')
    html.append('.header h1 { font-size: 24px; margin-bottom: 8px; }')
    html.append('.header .meta { font-size: 14px; opacity: 0.9; }')
    html.append('.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }')
    html.append('.stat-card { background: #fff; padding: 20px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }')
    html.append('.stat-card .label { font-size: 13px; color: #999; margin-bottom: 6px; }')
    html.append('.stat-card .value { font-size: 28px; font-weight: 700; }')
    html.append('.stat-card .sub { font-size: 12px; color: #888; margin-top: 4px; }')
    html.append('.stat-card.accent .value { color: #667eea; }')
    html.append('.stat-card.success .value { color: #52c41a; }')
    html.append('.stat-card.warn .value { color: #faad14; }')
    html.append('.section { background: #fff; border-radius: 10px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }')
    html.append('.section h2 { font-size: 18px; margin-bottom: 16px; padding-bottom: 10px; border-bottom: 2px solid #f0f0f0; }')
    html.append('table { width: 100%; border-collapse: collapse; font-size: 14px; }')
    html.append('th { text-align: left; padding: 10px 8px; color: #666; font-weight: 600; border-bottom: 2px solid #f0f0f0; }')
    html.append('td { padding: 8px; border-bottom: 1px solid #f5f5f5; }')
    html.append('tr:hover td { background: #fafafa; }')
    html.append('.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }')
    html.append('.badge-up { background: #f6ffed; color: #52c41a; }')
    html.append('.badge-down { background: #fff2f0; color: #ff4d4f; }')
    html.append('.badge-flat { background: #f0f0f0; color: #999; }')
    html.append('</style></head><body><div class="container">')

    # Header
    html.append('<div class="header"><h1>skill 成长报告</h1>')
    html.append('<div class="meta">首次运行: %s . 最近运行: %s . 版本 v%s</div></div>' % (
        fmt_date(prof.get("first_run", "")), fmt_date(prof.get("last_run", "")),
        prof.get("version", 1)))

    # Stats cards
    html.append('<div class="stats-grid">')
    html.append('<div class="stat-card accent"><div class="label">总运行次数</div><div class="value">%d</div></div>' % prof.get("total_runs", 0))
    html.append('<div class="stat-card"><div class="label">累计采集商品</div><div class="value">%d</div><div class="sub">条</div></div>' % prof.get("total_items", 0))
    html.append('<div class="stat-card success"><div class="label">品牌覆盖</div><div class="value">%d</div><div class="sub">个</div></div>' % len(brands))
    html.append('<div class="stat-card warn"><div class="label">平均数据质量</div><div class="value">%.1f</div><div class="sub">%%</div></div>' % avg_quality)
    html.append('</div>')

    # Brand ranking
    html.append('<div class="section"><h2>品牌排行</h2><table>')
    html.append('<thead><tr><th>#</th><th>品牌</th><th>运行次数</th><th>累计采集</th><th>平均耗时</th><th>最佳质量</th><th>TOP1 产品</th></tr></thead><tbody>')
    for i, (name, b) in enumerate(sorted(brands.items(), key=lambda kv: kv[1]["runs"], reverse=True), 1):
        lr = b.get("last_result", {})
        html.append('<tr><td>%d</td><td><b>%s</b></td><td>%d</td><td>%d</td><td>%.1fs</td><td>%.1f%%</td><td>%s</td></tr>' % (
            i, name, b["runs"], b.get("total_items", 0),
            b.get("avg_elapsed", 0), b.get("best_quality", 0),
            (lr.get("top1_name", "") or "-")[:30]))
    html.append('</tbody></table></div>')

    # Quality history
    if qh:
        html.append('<div class="section"><h2>数据质量追踪</h2><table>')
        html.append('<thead><tr><th>时间</th><th>品牌</th><th>采集率</th><th>已采集</th><th>待补采</th></tr></thead><tbody>')
        for q in qh[-15:]:
            html.append('<tr><td>%s</td><td>%s</td><td>%.1f%%</td><td>%d</td><td>%d</td></tr>' % (
                fmt_date_short(q.get("date", "")), q.get("brand", ""),
                q.get("rate", 0), q.get("collected", 0), q.get("pending", 0)))
        html.append('</tbody></table></div>')

    # Trend
    if len(hist) >= 2:
        html.append('<div class="section"><h2>TOP1 产品价格/销量趋势</h2><table>')
        html.append('<thead><tr><th>日期</th><th>产品</th><th>销量</th><th>最低价</th><th>销量变化</th></tr></thead><tbody>')
        for i in range(max(0, len(hist) - 10), len(hist)):
            h = hist[i]
            top1 = h.get("top10", [{}])[0] if h.get("top10") else {}
            prices = top1.get("lowest3_prices", [])
            price_str = "Y%.2f" % prices[0] if prices else "-"
            delta_str = ""
            if i > 0:
                prev_top1 = hist[i-1].get("top10", [{}])[0] if hist[i-1].get("top10") else {}
                delta = top1.get("total_sales", 0) - prev_top1.get("total_sales", 0)
                if delta > 0:
                    delta_str = '<span class="badge badge-up">+%d</span>' % delta
                elif delta < 0:
                    delta_str = '<span class="badge badge-down">%d</span>' % delta
                else:
                    delta_str = '<span class="badge badge-flat">持平</span>'
            html.append('<tr><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td></tr>' % (
                h.get("date", ""), (top1.get("name", "") or "-")[:30],
                top1.get("total_sales", 0), price_str, delta_str))
        html.append('</tbody></table></div>')

    # Recent runs
    html.append('<div class="section"><h2>最近运行记录</h2><table>')
    html.append('<thead><tr><th>时间</th><th>品牌</th><th>采集条数</th><th>产品数</th><th>耗时</th><th>数据质量</th><th>TOP1</th></tr></thead><tbody>')
    for r in runs[-15:]:
        res = r.get("result", {})
        q = r.get("quality", {})
        elapsed_str = "%.1fs" % r.get("elapsed_sec", 0) if r.get("elapsed_sec") else "-"
        q_str = "%.1f%%" % q.get("rate", 0) if q else "-"
        html.append('<tr><td>%s</td><td>%s</td><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
            fmt_date_short(r.get("date", "")), r.get("brand", ""),
            res.get("items", 0), res.get("products", 0), elapsed_str, q_str,
            (res.get("top1_name", "") or "-")[:25]))
    html.append('</tbody></table></div>')

    html.append('</div></body></html>')

    with open(html_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))
    print("HTML 成长报告已生成: %s" % html_path)


# ====================== 主入口 ======================
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="查看 skill 成长报告")
    p.add_argument("--brand", default=None, help="查看某品牌的历史运行记录")
    p.add_argument("--runs", type=int, default=0, help="查看最近 N 次运行")
    p.add_argument("--trend", action="store_true", help="查看价格/销量趋势")
    p.add_argument("--quality", action="store_true", help="查看数据质量追踪")
    p.add_argument("--html", action="store_true", help="生成 HTML 可视化报告")
    args = p.parse_args()

    prof = load()

    if args.html:
        hist = load_history()
        generate_html(prof, hist)
    elif args.trend:
        hist = load_history()
        print("=" * 64)
        print("  价格/销量趋势分析")
        print("=" * 64)
        if not hist:
            print("尚无历史快照（sales_history.json 不存在）。多跑几次 process.py 即可积累。")
        else:
            print("历史快照数: %d" % len(hist))
            print()
            print_trend(hist, limit=10)
    elif args.quality:
        print_quality(prof)
    elif args.brand:
        print_brand_history(prof, args.brand)
    elif args.runs > 0:
        print_recent_runs(prof, args.runs)
    else:
        print_summary(prof)

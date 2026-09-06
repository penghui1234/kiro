from __future__ import annotations

import json
from html import escape

from app.report_service import MonthlyReport, UserMonthlyUsage


def _fmt_number(value: float, digits: int = 1) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{value:,.0f}"
    return f"{value:,.{digits}f}"


def _tier_label(value: str) -> str:
    return {
        "PRO": "PRO",
        "PRO_PLUS": "PRO+",
        "PRO_MAX": "PRO MAX",
        "POWER": "POWER",
    }.get(value, value.replace("_", " "))


def _tier_class(value: str) -> str:
    return {
        "PRO": "tag-pro",
        "PRO_PLUS": "tag-proplus",
        "PRO_MAX": "tag-promax",
        "POWER": "tag-power",
    }.get(value, "tag-pro")


def _usage_bar(value: float, maximum: float, label: str) -> str:
    percent = value / maximum * 100 if maximum else 0
    width = min(80, max(0, percent * 0.8))
    color = "bar-danger" if percent > 100 else "bar-warning" if percent >= 80 else "bar-normal"
    return (
        '<div class="bar-wrap">'
        f'<div class="bar {color}" style="width:{width:.1f}px"></div>'
        f'<span class="bar-label">{escape(label)}</span></div>'
    )


def _user_label(user: UserMonthlyUsage) -> str:
    return user.email.split("@", 1)[0] or user.email


def _model_text(model_messages: dict[str, int]) -> str:
    return (
        ", ".join(
            f"{escape(model)}:{count:,}"
            for model, count in sorted(
                model_messages.items(), key=lambda item: item[1], reverse=True
            )
        )
        or "-"
    )


def _safe_json(data: object) -> str:
    return (
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )


def build_monthly_report_chart_data(report: MonthlyReport) -> dict[str, object]:
    dates = list(report.daily_credits)
    clients = list(report.client_credits)
    daily_series = [
        {
            "name": client,
            "data": [
                round(report.daily_client_credits.get(date, {}).get(client, 0), 4) for date in dates
            ],
        }
        for client in clients
    ]
    quota_data = []
    for user in report.users:
        utilization = user.credits / user.usage_limit * 100 if user.usage_limit else 0
        color = "#de350b" if utilization > 100 else "#FF991F" if utilization >= 80 else "#0052CC"
        quota_data.append({"y": round(utilization, 1), "color": color})

    overage_users = [user for user in report.users if user.overage_credits > 0]
    return {
        "daily": {
            "categories": [date[-2:] for date in dates],
            "series": daily_series,
        },
        "models": [{"name": model, "y": count} for model, count in report.model_messages.items()],
        "clients": [
            {"name": client, "y": round(credits, 4)}
            for client, credits in report.client_credits.items()
        ],
        "quota": {
            "categories": [_user_label(user) for user in report.users],
            "data": quota_data,
        },
        "overage": {
            "categories": [
                (
                    f"{_user_label(user)}<br/>"
                    f"({_tier_label(user.tier)}, 配额{_fmt_number(user.usage_limit, 0)})"
                )
                for user in overage_users
            ],
            "credits": [round(user.overage_credits, 4) for user in overage_users],
            "costs": [
                round(user.overage_credits * report.overage_price_per_credit, 2)
                for user in overage_users
            ],
            "price": report.overage_price_per_credit,
        },
    }


def render_monthly_report(report: MonthlyReport) -> str:
    if report.status != "ok":
        title = {
            "unconfigured": "月度用量报告未配置",
            "no_data": "暂无月度用量报告",
            "unavailable": "月度用量报告暂不可用",
        }[report.status]
        guidance = (
            "请在服务环境中设置 REPORT_BUCKET 和可选的 REPORT_PREFIX。"
            if report.status == "unconfigured"
            else "系统不会展示任何历史静态报告；请检查当前账号的报告投递配置后重试。"
        )
        return (
            '<section class="report-empty" aria-live="polite">'
            f"<h2>{escape(title)}</h2><p>{escape(report.message)}</p>"
            f"<small>{escape(guidance)}</small></section>"
        )

    total = report.total_credits
    quota = report.total_quota
    utilization = total / quota * 100 if quota else 0
    overage_users = [user for user in report.users if user.overage_credits > 0]
    overage_cost = report.total_overage * report.overage_price_per_credit
    ide_credits = report.client_credits.get("IDE", 0)
    cli_credits = report.client_credits.get("CLI", 0)
    ide_percent = ide_credits / total * 100 if total else 0
    cli_percent = cli_credits / total * 100 if total else 0
    overage_cap = max((user.overage_cap for user in report.users), default=0)
    tier_counts: dict[str, int] = {}
    for user in report.users:
        label = _tier_label(user.tier)
        tier_counts[label] = tier_counts.get(label, 0) + 1
    tier_detail = " / ".join(f"{tier}: {count}" for tier, count in tier_counts.items())

    rows = []
    for user in report.users:
        usage = user.credits / user.usage_limit * 100 if user.usage_limit else 0
        overage = (
            f'<span class="overage-warn">{_fmt_number(user.overage_credits)}</span>'
            if user.overage_credits
            else "0"
        )
        cost = user.overage_credits * report.overage_price_per_credit
        cost_html = (
            f'<span class="overage-warn">${cost:,.2f}</span>' if user.overage_credits else "-"
        )
        rows.append(
            "<tr>"
            f"<td>{escape(user.email)}</td>"
            f'<td><span class="tag {_tier_class(user.tier)}">'
            f"{escape(_tier_label(user.tier))}</span></td>"
            f'<td class="num">{_fmt_number(user.usage_limit, 0)}</td>'
            f'<td class="num">{_fmt_number(user.credits)}</td>'
            f"<td>{_usage_bar(user.credits, user.usage_limit, f'{usage:.1f}%')}</td>"
            f'<td class="num">{overage}</td>'
            f'<td class="num">{cost_html}</td>'
            f'<td class="num">{user.messages:,}</td>'
            f'<td class="model">{_model_text(user.model_messages)}</td>'
            "</tr>"
        )

    top_overage = max(overage_users, key=lambda user: user.overage_credits, default=None)
    top_overage_text = (
        f"最高：{escape(_user_label(top_overage))} "
        f"(${top_overage.overage_credits * report.overage_price_per_credit:,.2f})"
        if top_overage
        else "本月暂未产生超额"
    )
    highest_usage = max(
        report.users,
        key=lambda user: user.credits / user.usage_limit if user.usage_limit else 0,
    )
    highest_percent = (
        highest_usage.credits / highest_usage.usage_limit * 100 if highest_usage.usage_limit else 0
    )
    top_models = list(report.model_messages.items())[:3]
    model_lines = (
        "".join(f"<p>{escape(model)}：{count:,}</p>" for model, count in top_models)
        or "<p>当前报告未提供模型明细</p>"
    )
    primary_client = next(iter(report.client_credits), "-")
    top_three_credits = sum(user.credits for user in report.users[:3])
    top_three_percent = top_three_credits / total * 100 if total else 0
    chart_json = _safe_json(build_monthly_report_chart_data(report))
    account = escape(report.account_id or "-")
    period = escape(report.period or "-")
    through = escape(report.data_through or "-")
    rows_html = "".join(rows)
    recommendation = "建议关注套餐升级" if highest_percent >= 80 else "当前套餐余量充足"

    return f"""<div class="monthly-report">
<div class="header"><h1>Kiro 企业版月度用量报告</h1>
<span class="powered">Generated by Kiro Script</span></div>
<p class="subtitle">账号 {account} | 统计周期：{period}（截至 {through}）</p>
<p class="data-notice">每次刷新都会从当前 AWS 账号的 Kiro User Activity Reports
重新聚合；S3 报告可能存在投递延迟，请以顶部“截至”日期为准。</p>

<div class="cards">
  <div class="card"><div class="card-label">总 Credits 消耗</div>
  <div class="card-value">{_fmt_number(total)}</div>
  <div class="card-detail">团队基础配额合计 {_fmt_number(quota, 0)}</div></div>
  <div class="card"><div class="card-label">配额利用率</div>
  <div class="card-value">{utilization:.1f}%</div>
  <div class="card-detail">已用 / 基础配额</div></div>
  <div class="card"><div class="card-label">IDE / CLI</div>
  <div class="card-value">{ide_percent:.1f}% / {cli_percent:.1f}%</div>
  <div class="card-detail">IDE {_fmt_number(ide_credits)} +
  CLI {_fmt_number(cli_credits)}</div></div>
  <div class="card"><div class="card-label">活跃用户</div>
  <div class="card-value">{len(report.users)}</div>
  <div class="card-detail">当月有用量 · {escape(tier_detail)}</div></div>
</div>
<div class="cards-2">
  <div class="card card-warn"><div class="card-label">⚠️ 超额消耗（Overage）</div>
  <div class="card-value card-value-warn">{_fmt_number(report.total_overage)}</div>
  <div class="card-detail">{len(overage_users)} 位用户超出基础配额</div></div>
  <div class="card card-warn"><div class="card-label">⚠️ 估算超额费用</div>
  <div class="card-value card-value-warn">${overage_cost:,.2f}</div>
  <div class="card-detail">按 ${report.overage_price_per_credit:.2f}/credit 估算</div></div>
  <div class="card"><div class="card-label">超额上限（Overage Cap）</div>
  <div class="card-value">{_fmt_number(overage_cap, 0)}</div>
  <div class="card-detail">每用户最多可超额</div></div>
  <div class="card"><div class="card-label">总消息数</div>
  <div class="card-value">{report.total_messages:,}</div>
  <div class="card-detail">本月累计</div></div>
</div>

<div class="tier-legend">
  <span>套餐月度配额：</span>
  <span><span class="dot dot-pro"></span>PRO = 1,000</span>
  <span><span class="dot dot-proplus"></span>PRO+ = 2,000</span>
  <span><span class="dot dot-promax"></span>PRO MAX = 5,000</span>
  <span><span class="dot dot-power"></span>POWER = 10,000</span>
  <span class="tier-extra">| 超额：${report.overage_price_per_credit:.2f}/credit |
  Overage Cap：{_fmt_number(overage_cap, 0)}</span>
</div>

<div class="charts-row-3">
  <div class="chart-box"><div id="chart-daily" class="chart-300"></div></div>
  <div class="chart-box"><div id="chart-model" class="chart-300"></div></div>
  <div class="chart-box"><div id="chart-ide-cli" class="chart-300"></div></div>
</div>
<div class="charts-row">
  <div class="chart-box"><div id="chart-user-quota" class="chart-420"></div></div>
  <div class="chart-box"><div id="chart-overage" class="chart-420"></div></div>
</div>

<div class="section-title">用户明细（按 Credits 降序）</div>
<div class="table-wrap"><table><thead><tr>
  <th>用户</th><th>套餐</th><th class="num">基础配额</th>
  <th class="num">已用 Credits</th><th>配额利用率</th>
  <th class="num">超额 Credits</th><th class="num">估算超额费用</th>
  <th class="num">消息数</th><th>模型使用</th>
</tr></thead><tbody>{rows_html}</tbody></table></div>

<div class="section-title">关键洞察</div>
<div class="insights">
  <div class="insight-box insight-box-warn"><h3>⚠️ 超额预警</h3>
  <p>{len(overage_users)} 位用户超出基础配额</p>
  <p>估算超额费用：${overage_cost:,.2f}/月</p><p>{top_overage_text}</p>
  <p>建议：评估升级套餐 vs 超额付费</p></div>
  <div class="insight-box"><h3>💰 套餐优化建议</h3>
  <p>最高利用率：{escape(_user_label(highest_usage))}</p>
  <p>当前利用率：{highest_percent:.1f}%</p>
  <p>{recommendation}</p><p>费用按配置单价估算</p></div>
  <div class="insight-box"><h3>🤖 模型偏好</h3>{model_lines}
  <p>模型消息数据来自 S3 报告</p></div>
  <div class="insight-box"><h3>📊 总览</h3>
  <p>团队配额利用率：{utilization:.1f}%</p>
  <p>{escape(primary_client)} 为主力客户端</p>
  <p>Top 3 用户占比 {top_three_percent:.1f}%</p>
  <p>{len(report.users) - len(overage_users)} 位用户未超额</p></div>
</div>
<p class="footer">数据来源：S3 Kiro User Activity Reports | 账号 {account} |
当前账号实时聚合数据</p>
<script id="monthly-report-data" type="application/json">{chart_json}</script>
</div>"""

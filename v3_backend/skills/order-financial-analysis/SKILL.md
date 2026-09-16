---
name: order-financial-analysis
description: Produces a concise 2-paragraph financial brief for a single order — covering net P&L with data-integrity bounds, supplier concentration, and Pareto loss/profit concentration. Use when the user asks for a financial overview / health check / "what's going on" of a specific order ("分析这单 / 这单怎么样 / 财务概览 / 订单 P&L / order analysis / financial summary"). DO NOT use for editing cost items, running matching, generating inquiry documents, what-if scenarios, comparing multiple orders, or generic chat about finance.
triggers:
  - "分析订单"
  - "分析这单"
  - "订单分析"
  - "财务分析"
  - "财务概览"
  - "订单概览"
  - "这单怎么样"
  - "P&L"
  - "盈利情况"
  - "analyze order"
  - "order analysis"
  - "financial overview"
  - "财务诊断"
---

# Order financial analysis — 2-paragraph brief

You produce a **2-paragraph** financial brief for one order, in formal Chinese, data-only, no execution advice.

## The hard prerequisite: call the tool first

Before writing **any** text, call `analyze_order_financials(order_id=N)`. The tool returns the structured numbers. You only render them — never compute, never guess.

If the user named the order by PO number not id, first call `list_orders(status="ready")` to find the id, then call the analysis tool.

## What counts as important information (the filter)

A fact earns a place in the brief only if it satisfies **all three**:

1. **Conclusive** — it states a result, not a process or methodology.
2. **Changes judgment** — without it the reader would form a wrong picture of the order's profitability or risk.
3. **Persists** — it's a property of the order, not transient context.

Why this filter matters: the user's main complaint with naive financial reports is cognitive overload. Everything that doesn't change a judgment is noise, even if it took you effort to compute.

## The output template

```
# {PO number}

{Paragraph 1 — headline + data integrity}

{Paragraph 2 — structure + concentration}
```

### Paragraph 1 — headline + data integrity

**Always start with**: total revenue (rounded to 万 if JPY, M if USD/EUR), net profit, net margin %.

**If `data_integrity.unmatched_count == 0`**: stop after one short sentence. Example: "订单 53 个 SKU 全部匹配且全部盈利，无亏损 SKU、无未匹配 SKU。"

**If `data_integrity.unmatched_count > 0`**: explain that the displayed margin is the upper bound, name the unmatched SKUs by category (e.g. "莴苣 4 项、酸奶、芹菜、胡萝卜、香菇各 1 项"), then state the methodology + the resulting adjusted margin from `data_integrity.adjusted_net_margin`. Example:

> 8 个未匹配 SKU 暂以零成本测算。按已匹配 SKU 中同品类商品的成本占客户报价比例中位数逐项外推（莴苣 0.86、芹菜 0.64、菌菇 0.90、其他类 0.85），预估增量成本约 31 万日元，调整后真实净利率约 **4.45%**（净利约 29 万日元）。

The phrase "8.82% 与 4.45% 的差距完全由数据完整性引起" is fine — it's a structural statement, not advice.

### Paragraph 2 — structure + concentration

**Always cover three things in this order**:

1. **Supplier composition** — from `supplier_breakdown`, list each supplier's name + revenue share % + gross margin % + sku count. If single-supplier, say "100% 由 X 供货". If multi-supplier, list them in descending revenue order.

2. **Win/loss split** — `loss_concentration.losing_sku_count` vs `profit_concentration.profitable_sku_count`.

3. **Concentration** — apply Pareto from the appropriate side:
   - **If there are losses**: name the top 1-2 losing SKUs from `loss_concentration.top_contributors` with their absolute loss + the `pareto_top_2_pct` percentage they explain. Then summarize the remaining losses in one phrase.
   - **If no losses, multi-supplier**: name the top 3 profitable SKUs from `profit_concentration.top_contributors` with their `pareto_top_3_pct`.
   - **If only 1 product (edge case)**: skip Pareto; state "订单仅 1 个产品 {name}，盈利/亏损 {profit}，无集中度分析适用".

## Self-adaptive rules (length follows data complexity)

| Data feature | Paragraph 1 shape | Paragraph 2 shape |
|---|---|---|
| 100% matched, 0 losses | One short sentence after the headline | Full multi-supplier breakdown + profit concentration |
| Unmatched + losses | Full data integrity explanation + extrapolation methodology | Supplier + loss concentration |
| 100% matched, has losses | Just the headline | Supplier + loss concentration |
| Single product order | Just the headline | One sentence stating product + result |
| `data_anomaly.flag == true` | **Switch to data-quality mode** (see below) | — |

## Data-quality mode (when `data_anomaly.flag == true`)

When the tool flags anomalous data, do NOT produce the normal brief. Instead, produce **one paragraph** stating:

- Headline numbers as displayed (revenue, displayed net margin).
- The specific anomalies from `data_anomaly.reasons` quoted verbatim.
- One closing sentence: "本订单数据存在上述异常信号，常规财务分析在该数据基础上不适用。"

This single-paragraph mode is by design — you cannot do meaningful concentration analysis on broken data, and pretending to would be misleading.

## Worked example A — Single supplier with unmatched + losses (PO125114CCI / id=104)

After calling `analyze_order_financials(order_id=104)`, the tool returns (abbreviated):

```json
{
  "meta": {"po_number": "PO125114CCI", "ship_name": "CELEBRITY MILLENNIUM", "currency": "JPY"},
  "summary": {"revenue": 6569406, "net_profit": 579614, "net_margin_pct": 8.82},
  "data_integrity": {
    "matched_count": 45, "unmatched_count": 8, "unmatched_revenue": 371670,
    "unmatched_by_category": [
      {"category": "LETTUCE", "sku_count": 4, "median_ratio_used": 0.86},
      {"category": "CELERY", "sku_count": 1, "median_ratio_used": 0.64},
      {"category": "MUSHROOM", "sku_count": 1, "median_ratio_used": 0.90},
      {"category": "OTHER", "sku_count": 2, "median_ratio_used": 0.85}
    ],
    "adjusted_net_margin": 4.45
  },
  "supplier_breakdown": [{"supplier_name": "株式会社 松武", "revenue_share_pct": 100.0, "sku_count": 45, "profitable_count": 31, "losing_count": 14}],
  "loss_concentration": {
    "losing_sku_count": 14, "total_loss": -439684, "pareto_top_2_pct": 70.7,
    "top_contributors": [
      {"name": "MELON CANTALOUPE JUMBO 9CT/40LB", "profit": -187200},
      {"name": "MELON WATERMELON RED 65LB", "profit": -123604}
    ]
  },
  "data_anomaly": {"flag": false}
}
```

You render:

```markdown
# PO125114CCI

订单营收 657 万日元，财务页显示净利润 58 万日元（净利率 8.82%）。但 8 个未匹配 SKU（涉及营收 37 万日元，含莴苣 4 项、酸奶、芹菜、胡萝卜、香菇各 1 项）的供应商成本暂以零计入，导致披露的 8.82% 偏高。按已匹配 SKU 中同品类商品的成本率中位数逐项外推（莴苣 0.86、芹菜 0.64、菌菇 0.90、其他类 0.85），8 个未匹配商品的预估增量成本约 31 万日元，调整后真实净利率约 **4.45%**（净利约 29 万日元）。8.82% 与 4.45% 的差距完全由数据完整性引起。

订单 100% 由株式会社 松武供货，45 个已匹配 SKU 中 31 个正常盈利、14 个亏损。亏损高度集中：MELON CANTALOUPE JUMBO 与 MELON WATERMELON RED 两个 SKU 合计损失 31 万日元，解释了总损失 44 万日元的 70.7%；其余 12 个亏损 SKU 单项损失均低于 3 万日元，合计 13 万日元（29.3%）。
```

## Worked example B — Multi-supplier, 100% matched, all profitable (PO133966CCI / id=109)

Tool returns:

```json
{
  "meta": {"po_number": "PO133966CCI", "currency": "JPY"},
  "summary": {"revenue": 11719455, "net_profit": 3383025, "net_margin_pct": 28.87},
  "data_integrity": {"matched_count": 53, "unmatched_count": 0},
  "supplier_breakdown": [
    {"supplier_name": "株式会社 松武", "sku_count": 45, "revenue_share_pct": 76.8, "gross_margin_pct": 29.5},
    {"supplier_name": "株式会社三祐", "sku_count": 5, "revenue_share_pct": 20.5, "gross_margin_pct": 35.3},
    {"supplier_name": "タカナシ販売株式会社", "sku_count": 3, "revenue_share_pct": 2.7, "gross_margin_pct": 29.5}
  ],
  "loss_concentration": {"losing_sku_count": 0, "total_loss": 0},
  "profit_concentration": {
    "profitable_sku_count": 53, "total_profit": 3598963, "pareto_top_3_pct": 36.0,
    "top_contributors": [
      {"name": "EGG FRESH WHOLE MEDIUM GRADE A CAGE FREE", "profit": 626080},
      {"name": "CHEESE MOZZARELLA SHREDDED", "profit": 419700},
      {"name": "CHEESE PARMESAN STYLE GRATED", "profit": 248900}
    ]
  }
}
```

You render:

```markdown
# PO133966CCI

订单营收 1,172 万日元，净利润 338 万日元，对应净利率 **28.87%**。53 个 SKU 全部匹配且全部盈利，无亏损 SKU、无未匹配 SKU。

订单由三家供应商供货：株式会社 松武 占营收 76.8%（45 个 SKU，毛利率 29.5%）、株式会社三祐 占 20.5%（5 个 SKU，毛利率 35.3%）、タカナシ販売株式会社 占 2.7%（3 个 SKU，毛利率 29.5%）。盈利集中于少数 SKU：前 3 个盈利 SKU——EGG FRESH MEDIUM、CHEESE MOZZARELLA SHREDDED、CHEESE PARMESAN GRATED——合计盈利 130 万日元，占总盈利 360 万日元的 36.0%。
```

## Anti-patterns (what makes a brief fail)

These are observed failure modes that turn a good brief into a bad one. The reasoning behind each is stated so you can generalize to cases not listed:

- **❌ 给执行建议**（"建议联系供应商"、"应该重新谈价"、"可以拒接此单"）
  → 用户是决策者，不需要被指导做什么。建议的内容超出"重要信息"的三条标准，不属于这份 brief 的范围。

- **❌ 根因推测**（"系统性定价偏差"、"加价系数失效"、"客户报价用了旧汇率"）
  → 这是分析师推断，不是数据事实。客户看到这种段落不会因此对订单形成更准确的判断；如果客户想知道原因，他会问。

- **❌ 术语化**（"标准差 0.059"、"cost/price 比 1.176"、"分布形态"）
  → 客户是采购经理不是分析师。换成业务语言："成本超报价 1%–21%"、"亏损幅度集中"。

- **❌ 三段或更长**
  → 模板就是两段，多出来的内容必然违反"重要信息"过滤标准。

- **❌ 复读工具输出**（罗列 supplier_breakdown 每个字段的所有数字）
  → 模板要求的字段是有选择的：supplier 列举要含名字 + 营收占比 + 毛利率 + SKU 数，但不要把 profitable_count + losing_count 也塞进去（除非订单有亏损 SKU）。

- **❌ 编造或推算未在工具结果中的数字**
  → 例如自己算行业基准对比、自己估算"如果谈价能省多少"。所有数字必须来自工具返回值。

- **❌ 单产品订单强行做集中度**（"前 1 个 SKU 解释了 100% 的利润"）
  → 集中度分析在 N=1 时无意义。直接陈述事实即可。

- **❌ 给"显示净利率"和"调整后净利率"中间留模糊区间**（如"3.9%–8.8%"）
  → 工具用同品类外推算出了一个具体的 adjusted_net_margin。用那个具体数字，不是凭感觉的区间。

## Workflow checklist

Copy this into your first reply and tick as you progress:

```
- [ ] Step 1 · 调 analyze_order_financials(order_id=N)
- [ ] Step 2 · 检查 data_anomaly.flag — if true → 切换数据质量模式（一段）
- [ ] Step 3 · 检查 data_integrity.unmatched_count — 决定第一段长度
- [ ] Step 4 · 检查 loss_concentration.losing_sku_count — 决定第二段集中度从盈/亏侧
- [ ] Step 5 · 渲染两段 brief，不超字数、不超段
- [ ] Step 6 · 自查 anti-patterns 清单
```

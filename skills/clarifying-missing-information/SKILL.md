---
name: clarifying-missing-information
description: Generate ordered missing_questions for required quotation fields such as product_query, region and quantity by comparing collected requirements against the decision-tree order, while preserving previously answered values across turns and supporting explicit user corrections. Use when a multi-turn conversation (缺失信息追问/多轮澄清) still lacks required fields, or when the user corrects or updates an earlier answer.
---

# Clarifying Missing Information（缺失信息追问，多轮澄清）

对照必填字段清单与决策树顺序，找出仍缺失的信息并生成有序的追问问题；跨轮次保留已答内容，支持用户随时更正。

## Input

- 当前会话已收集（含已确认与待确认）的需求字段。
- 必填字段清单与决策树步骤顺序。
- 用户最新一轮回复（可能是补充答案，也可能是更正）。

## Required Fields Checklist（必填字段）

按决策树顺序检查（缺哪个先问哪个）：

1. `product_query` —— 想要什么产品/产品线？
2. `region` —— 使用/交付地区？（决定区域限制规则）
3. `quantity` —— 数量？
4. 决策树推进所需的步骤信息（探测器类型、滤线栅位置、发生器规格等）。
5. 商务信息（可选但建议收集）：`currency`、`incoterm`、`delivery_location`、`target_price`。

## Workflow

1. **盘点**：列出已答字段（含值与置信度）与缺失字段。
2. **排序**：缺失字段严格按决策树顺序排列，最多生成 20 条 `missing_questions`。
3. **提问**：每条问题只问一个字段，问题中给出可选值范围（枚举字段列出允许值）。
4. **保留**：新一轮回答只更新被回答/被更正的字段，其余已答内容原样保留，绝不清空重问。
5. **更正**：用户说"改成 X""不是 A 是 B"时，覆盖旧值、重打置信度，并复述确认："已将数量从 2 改为 3。"
6. **确认**：置信度 < 0.7 的已有候选值也要列入待确认问题（"您提到预算约 50 万，确认目标价为 500000 CNY 吗？"）。

## Output Format

```json
{
  "answered": [
    {"field_name": "product_query", "value": "FMT 数字 X 光机", "confidence": 0.95}
  ],
  "missing_questions": [
    "请问设备将在哪个国家/地区使用？（用于校验区域限制规则）",
    "需要采购的数量是多少台？（1–999 的整数）",
    "结算货币是哪一种？（USD / EUR / CNY / SGD / JPY / GBP / AUD / HKD）"
  ],
  "pending_confirmation": [
    {"field_name": "target_price", "value": "500000", "question": "确认目标价为 500000 吗？"}
  ]
}
```

## Guidelines

- 一次追问不超过 3–5 个问题，优先阻塞决策树推进的字段。
- 不重复询问已答且置信度 ≥ 0.7 的字段。
- 用户拒答可选字段时记录为"用户未提供"，不再重复追问。

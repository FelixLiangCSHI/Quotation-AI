---
name: extracting-requirement-fields
description: Extract candidate values for whitelisted quotation requirement fields from natural-language product requests and score each candidate with a confidence value, normalizing aliases such as RMB to CNY and lowercasing regions. Use when a user describes the product they want in free text (需求要素抽取) and the request must be converted into structured requirement fields such as product_query, region, quantity, currency or incoterm before configuration can start.
---

# Extracting Requirement Fields（需求要素抽取）

从用户的自然语言需求中抽取白名单字段的候选值，为每个候选值打置信度，并做别名归一化。抽取结果是"候选值"，不是最终值：低置信度候选必须由用户确认后才能写入报价。

## Input

用户的一段或多段自然语言产品需求，例如：

> 我们新加坡的客户想要一台 FMT 数字 X 光机，带无线探测器，数量 2 台，预算大约 50 万 RMB，FOB 交货。

## Field Whitelist（字段白名单）

`field_name` 只能取以下值，其余一律丢弃：

| field_name | 类型/约束 |
| --- | --- |
| `product_query` | 文本，≤200 字符 |
| `region` | 文本，归一化为小写 |
| `quantity` | 整数，1–999 |
| `intended_use` | 文本 |
| `delivery_location` | 文本 |
| `currency` | 枚举：USD, EUR, CNY, SGD, JPY, GBP, AUD, HKD |
| `incoterm` | 枚举：EXW, FCA, FOB, CIF, CIP, DAP, DDP |
| `target_price` | 正数 |
| `budget_notes` | 长文本，≤2000 字符 |
| `constraints` | 长文本 |
| `customer_name` | 文本 |
| `requested_accessories` | 文本列表 |
| `requested_services` | 文本列表 |
| `selected_product_ids` | 产品 ID 列表 |

## Normalization Rules（归一化规则）

1. 货币别名：`RMB` → `CNY`，`US$`/`USD$` → `USD`，`€` → `EUR`；统一大写。
2. 区域：去首尾空白后小写化（如 `Singapore` → `singapore`）。
3. 数量：转为整数；布尔值、非数字、≤0 或 >999 一律拒绝。
4. 金额：去千分位逗号后转数字，必须大于零。
5. 空白值、超长值直接拒绝，不产出候选。

## Confidence Scoring（置信度）

- 用户明确说出的值（"数量 2 台"）：0.9–1.0。
- 需要轻度推断的值（"新加坡的客户" → region=singapore）：0.7–0.9。
- 强推断/模糊值（"预算大约 50 万" → target_price=500000）：<0.7。
- **置信度 < 0.7 的候选必须标记为待确认，不得静默合并。**

## Output Format

```json
{
  "requirements": [
    {"field_name": "product_query", "value": "FMT 数字 X 光机 无线探测器", "confidence": 0.95},
    {"field_name": "region", "value": "singapore", "confidence": 0.85},
    {"field_name": "quantity", "value": "2", "confidence": 0.95},
    {"field_name": "currency", "value": "CNY", "confidence": 0.9},
    {"field_name": "incoterm", "value": "FOB", "confidence": 0.95},
    {"field_name": "target_price", "value": "500000", "confidence": 0.6}
  ],
  "rejected": [
    {"field_name": "quantity", "raw_value": "很多", "reason": "quantity must be a whole number"}
  ]
}
```

## Guidelines

- 只抽取用户实际提供的信息，绝不编造字段值。
- 一个字段出现多个矛盾值时，保留最新表述并降低置信度。
- 抽取后应交由字段校验工具（如 `validate_requirement_fields`）做类型/枚举校验，以校验结果为准。

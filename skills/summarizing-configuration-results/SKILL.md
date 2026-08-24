---
name: summarizing-configuration-results
description: Produce the final configuration deliverable as strict structured JSON plus a human-readable configuration list, labeling every line with a recommendation level of required, recommended, optional or incompatible. Use at the end of an automated parameter-configuration session (配置结果汇总输出) when all decision-tree steps are resolved or explicitly marked pending and the user needs the consolidated result.
---

# Summarizing Configuration Results（配置结果汇总输出）

在配置流程收尾时，把全部已确认字段与决策树选型汇总为：(1) 严格结构化的 JSON，(2) 人类可读的配置清单。每一行配置必须标注推荐等级。

## Input

- 已确认的需求字段（含置信度）。
- 决策树各步骤的选型结果与规则依据。
- 规则校验工具的最终校验结果。
- 仍待确认或缺失的事项。

## Recommendation Levels（推荐等级）

| 等级 | 含义 |
| --- | --- |
| `required` | 决策树/规则要求必配的项 |
| `recommended` | 规则允许且知识库推荐搭配的项 |
| `optional` | 规则允许、由用户自选的项 |
| `incompatible` | 校验判定不兼容，仅作提示，禁止计入配置 |
| `not evaluated` | 因信息缺失未能校验的项 |

## Output Format

### 1. 结构化 JSON

```json
{
  "requirements": [
    {"field_name": "product_query", "value": "FMT 数字 X 光机", "confidence": 0.95},
    {"field_name": "region", "value": "singapore", "confidence": 0.85},
    {"field_name": "quantity", "value": "2", "confidence": 0.95}
  ],
  "configuration": [
    {"step": "fmt_step_1a", "option_group": "System", "selected": "DEMO-FMT-100", "level": "required", "rule_basis": "..."},
    {"step": "fmt_step_2b", "option_group": "Detector", "selected": "DEMO-DET-W35", "level": "required", "rule_basis": "..."},
    {"step": "fmt_step_5a", "option_group": "Accessories", "selected": "DEMO-ACC-01", "level": "recommended", "rule_basis": "..."}
  ],
  "product_interpretation": "对用户需求的产品化解读",
  "missing_questions": [],
  "recommendation_rationale": "逐项说明选择依据的决策树步骤与规则文本"
}
```

### 2. 人类可读清单

```text
配置清单（DEMO-FMT-100，新加坡，2 台）
────────────────────────────────
[必配 required]      主机     DEMO-FMT-100   依据: fmt_step_1a / System
[必配 required]      探测器   DEMO-DET-W35   依据: fmt_step_2b / Detector
[推荐 recommended]   配件     DEMO-ACC-01    依据: fmt_step_5a / Accessories
[不兼容 incompatible] 滤线栅  DEMO-GRD-T1    原因: 与无线探测器不兼容（仅提示，未计入）
待确认: 目标价 500000 CNY（置信度 0.6，请确认）
```

## Guidelines

- JSON 中 `field_name` 只允许白名单字段；`incompatible` 项只能出现在提示区，不得计入有效配置。
- 每项配置必须带 `rule_basis`；无依据的项标注"需人工确认"。
- 汇总中不得包含价格结论、折扣、成本、审批或交期承诺；说明这些由后续定价与审批环节处理。
- 仍有待确认/缺失项时，在结尾清晰列出，提示用户配置尚未最终定稿。

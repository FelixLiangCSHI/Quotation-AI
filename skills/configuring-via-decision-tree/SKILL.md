---
name: configuring-via-decision-tree
description: Walk the product knowledge-base decision tree step by step to configure a quotation, following the step_options order of main system, detector, grid, generator/tube, then accessories and services, citing the rule basis for every selected option. Use when requirement fields are already extracted and the user needs an automated parameter configuration (决策树引导配置) grounded in the knowledge base's products, step_options and rule_signals.
---

# Configuring via Decision Tree（决策树引导配置）

按知识库决策树的 step_options 顺序逐步确定配置参数，每一步的选择都必须给出知识库中的规则依据（product_id / step_id / option_group / 规则原文）。

## Input

- 已抽取并校验过的需求字段（product_query、region、quantity 等）。
- 绑定知识库中的决策树数据：`products`、`step_options`、`rule_signals`、兼容性矩阵。

## Workflow

严格按以下顺序逐步定参（主机 → 探测器 → 滤线栅 → 发生器/球管 → 配件/服务）：

1. **主机型号（System）**：用 `product_query` 检索知识库 `products`，确定 product_id 与产品线；用 `region` 核对区域限制规则。
2. **探测器（Detector）**：在该 product_id 的 step_options 中检索 Detector 选项组，结合 detector_grid_matrix 选出支持的探测器。
3. **滤线栅（Grid）**：依据 detector_grid_matrix 校验探测器与滤线栅位置的组合。
4. **高压发生器/球管（Generator / Tube）**：依据 generator_tube_matrix 匹配发生器规格与球管。
5. **配件与服务（Accessories / Services）**：结合 compatibility_matrix 与用户的 requested_accessories / requested_services 给出兼容项。

每一步：

- 先检索知识库（如工具 `search_decision_tree`），再调用确定性校验工具（如 `validate_configuration`）确认，**绝不凭记忆判断兼容性**。
- 记录本步的 `step_id`、`option_group`、所选项与规则原文。
- 知识库检索不到依据时，如实标注"暂无规则依据，需人工确认"。
- 本步所需信息缺失时，停止该分支并转交缺失信息追问流程。

## Output Format

```json
{
  "configuration": [
    {
      "step": "fmt_step_1a",
      "option_group": "System",
      "selected": "DEMO-FMT-100",
      "rule_basis": "Synthetic FMT digital X-ray system; region rule: allowed in singapore"
    },
    {
      "step": "fmt_step_2b",
      "option_group": "Detector",
      "selected": "DEMO-DET-W35",
      "rule_basis": "detector_grid_matrix: wireless detector supported with DEMO-FMT-100"
    }
  ],
  "product_interpretation": "对用户需求的产品化解读",
  "recommendation_rationale": "逐步说明每项选择依据的决策树步骤与规则文本"
}
```

## Guidelines

- 一次只推进一个决策树步骤，禁止跳步或臆测后续步骤结论。
- 同一步存在多个合规候选时，全部列出并说明差异，由用户选择。
- 校验工具返回不兼容时，回退到上一步给出替代选项，不得强行输出不合规配置。
- 本 skill 不涉及价格、折扣、审批、交期；相关问题说明由后续环节处理。

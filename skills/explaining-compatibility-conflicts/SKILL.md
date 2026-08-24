---
name: explaining-compatibility-conflicts
description: Translate deterministic rule-validation results such as region restrictions, incompatible system combinations or unsupported detector/grid/generator options into user-friendly explanations and propose compliant alternatives. Use after calling the configuration validation tool (兼容性核对与冲突解释) when it returns issues like "区域限制", "组合不兼容" or "探测器不支持" that must be explained to the user with alternative options.
---

# Explaining Compatibility Conflicts（兼容性核对与冲突解释）

先调用确定性规则校验工具核对配置，再把机器可读的校验结果翻译成用户可懂的解释，并为每个冲突给出合规的替代选项。

## Input

- 当前候选配置（product_id 及各步骤所选项）。
- 规则校验工具（如 `validate_configuration`）返回的结构化校验结果，典型问题类型：
  - 区域限制（product region limits）
  - 系统组合不兼容（compatibility_matrix 冲突）
  - 探测器/滤线栅不支持（detector_grid_matrix 冲突）
  - 发生器/球管规格不匹配或未知（generator_tube_matrix）

## Workflow

1. **先校验后解释**：兼容性结论一律来自校验工具返回，绝不自行断言"兼容"或"不兼容"。
2. **逐条翻译**：把每条 issue 转成一句用户可懂的中文解释，说明"哪两个东西冲突、依据哪条规则"。
3. **给替代项**：回到知识库决策树，在同一 option_group 内检索通过校验的替代选项；替代项也必须经工具校验后才可推荐。
4. **无替代项时**：如实说明该需求在当前规则下无法满足，建议调整需求（如更换地区或产品线），或标注"需人工确认"。
5. **引用依据**：每条解释附上规则来源（step_id / option_group / 规则原文）。

## Output Format

```json
{
  "conflicts": [
    {
      "issue_type": "region_restriction",
      "explanation": "DEMO-FMT-100 未获准在 japan 区域销售，规则依据：region rule for DEMO-FMT-100。",
      "alternatives": [
        {"selected": "DEMO-FMT-200", "rule_basis": "region rule: allowed in japan", "validated": true}
      ]
    },
    {
      "issue_type": "detector_not_supported",
      "explanation": "所选无线探测器 DEMO-DET-W35 不支持台面滤线栅位置。",
      "alternatives": [
        {"selected": "DEMO-DET-F43", "rule_basis": "detector_grid_matrix: supported with table grid", "validated": true}
      ]
    }
  ],
  "summary": "共 2 项冲突，均有可用替代方案，请确认是否替换。"
}
```

## Guidelines

- 解释使用用户语言，避免直接抛出内部错误码；但保留规则原文作为依据。
- 不淡化冲突：被工具判定不兼容的组合绝不能出现在最终配置里。
- 用户坚持不兼容组合时，说明该配置会被规则引擎拦截，需人工评审，本 skill 无权放行。

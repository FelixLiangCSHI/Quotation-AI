# 低代码平台工具 JSON 定义

本目录包含 `tools.md` 中 5 个 Agent 工具的 JSON 版本，可直接导入企业低代码平台。

每个文件对应一个工具，结构统一：

| 字段 | 说明 |
| --- | --- |
| `name` | 工具名称 |
| `type` / `method` | 工具类型（HTTP）与请求方法 |
| `description` | 工具描述（Agent 何时调用） |
| `api` | 接口名称、路径、方法与接口说明 |
| `request` | 请求参数（位置、类型、必填、示例） |
| `response` | 返回参数结构 |
| `agent_parameter_guidance` | Agent 填写参数的指引 |

## 工具列表

| 文件 | 工具 | 接口 |
| --- | --- | --- |
| `search_decision_tree.json` | 决策树节点检索 | `GET /api/v1/decision-tree/search` |
| `recommend_configuration.json` | 产品配置推荐（绑定已有 `app/api.py` 端点） | `POST /recommend` |
| `validate_configuration.json` | 配置规则校验 | `POST /api/v1/configuration/validate` |
| `validate_requirement_fields.json` | 需求字段校验 | `POST /api/v1/requirements/validate` |
| `merge_requirements.json` | 需求候选合并（可选） | `POST /api/v1/requirements/merge` |

> 注意：目前仅 `POST /recommend` 已在 `app/api.py` 中实现，其余端点为规划中的接口，导入后需待后端实现后方可调用。

# 低代码平台工具 JSON 定义

本目录包含 `tools.md` 中 5 个 Agent 工具的 JSON 版本，采用标准 **OpenAPI 3.0.1** 格式，可直接导入企业低代码平台（支持 OpenAPI/Swagger 导入的平台均可识别）。

每个文件都是一份独立、合法的 OpenAPI 3.0 文档，结构统一：

| 字段 | 说明 |
| --- | --- |
| `info.title` | 工具名称 |
| `info.description` | 工具描述（Agent 何时调用） |
| `servers` | 服务地址（默认 `http://localhost:8000`，导入后请替换为实际部署地址） |
| `paths.<path>.<method>` | 接口路径与请求方法 |
| `operationId` | 工具唯一标识（与工具名一致） |
| `parameters` / `requestBody` | 请求参数（位置、类型、必填、示例，JSON Schema 描述） |
| `responses` | 返回参数结构（JSON Schema 描述） |

Agent 填写参数的指引已合并到各接口的 `description` 字段中。

## 工具列表

| 文件 | 工具 | 接口 |
| --- | --- | --- |
| `search_decision_tree.json` | 决策树节点检索 | `GET /api/v1/decision-tree/search` |
| `recommend_configuration.json` | 产品配置推荐（绑定已有 `app/api.py` 端点） | `POST /recommend` |
| `validate_configuration.json` | 配置规则校验 | `POST /api/v1/configuration/validate` |
| `validate_requirement_fields.json` | 需求字段校验 | `POST /api/v1/requirements/validate` |
| `merge_requirements.json` | 需求候选合并（可选） | `POST /api/v1/requirements/merge` |

> 注意：上述 5 个端点均已在 `app/api.py` 中实现。导入后请将各 JSON 中 `servers[0].url` 替换为后端实际部署地址。

Tool 1：search_decision_tree
第一步：创建接口
接口名称：决策树节点检索
接口路径：GET /api/v1/decision-tree/search
接口说明：按产品线或关键词检索知识库决策树节点（products / step_options / rule_signals），返回步骤 ID、选项组与约束原文。
请求参数设置（Query）

JSON
{
  "query": {
    "type": "string",
    "required": true,
    "description": "检索关键词，如产品线、型号或选项名，例：FMT",
    "example": "FMT"
  },
  "product_id": {
    "type": "string",
    "required": false,
    "description": "限定某一产品 ID 内检索，例：DEMO-FMT-100"
  },
  "option_group": {
    "type": "string",
    "required": false,
    "description": "限定选项组：System / Detector / Grid / Generator / Tube / Accessories"
  },
  "limit": {
    "type": "integer",
    "required": false,
    "default": 10,
    "description": "最多返回节点数（1-50）"
  }
}
返回参数设置

JSON
{
  "total": {"type": "integer", "description": "命中节点总数"},
  "nodes": {
    "type": "array",
    "description": "命中的决策树节点列表",
    "items": {
      "step_id": {"type": "string", "description": "决策树步骤 ID，例：fmt_step_1a"},
      "product_id": {"type": "string", "description": "所属产品 ID"},
      "option_group": {"type": "string", "description": "选项组，例：System / Detector"},
      "short_description": {"type": "string", "description": "节点简述"},
      "raw_constraint_text": {"type": "string", "description": "约束规则原文，可为 null"}
    }
  },
  "rule_signals": {
    "type": "array",
    "description": "关联的规则信号",
    "items": {
      "rule_id": {"type": "string", "description": "规则 ID"},
      "message": {"type": "string", "description": "规则文本"},
      "strength": {"type": "string", "description": "hard_block 或 advisory"}
    }
  }
}
第二步：工具基本信息与参数选择
工具名称：search_decision_tree
工具类型：HTTP（GET）
工具描述：在配置任何参数前检索决策树知识库，获取该步骤的候选选项与规则依据。当需要确定主机、探测器、滤线栅、发生器/球管等某一步的可选项时调用。
选择接口：决策树节点检索（GET /api/v1/decision-tree/search）
参数选择（由 Agent 填写）：query（必填，取自用户需求关键词）、product_id、option_group、limit（选填）
Tool 2：recommend_configuration ✅（绑定已有端点，无 404 风险）
第一步：创建接口
接口名称：产品配置推荐
接口路径：POST /recommend
接口说明：输入自然语言产品需求，返回主机型号与兼容配件候选及推荐等级（已存在于 app/api.py）。
请求参数设置（Body，application/json）

JSON
{
  "message": {
    "type": "string",
    "required": true,
    "minLength": 1,
    "description": "自然语言产品需求",
    "example": "需要一台 FMT 数字 X 光机，带无线探测器"
  },
  "region": {
    "type": "string",
    "required": false,
    "maxLength": 30,
    "description": "地区，仅支持：canada/china/eu/italy/us/other（及别名 usa、europe 等），非法值返回 422",
    "example": "china"
  },
  "max_accessories": {
    "type": "integer",
    "required": false,
    "minimum": 1,
    "maximum": 200,
    "description": "最多返回的配件数"
  }
}
返回参数设置

JSON
{
  "answer": {"type": "string", "description": "人类可读的推荐文本"},
  "recommendation": {
    "type": "object",
    "description": "结构化推荐结果（客户安全视图，不含内部成本）",
    "properties": {
      "main_model": {"type": "object", "description": "推荐主机型号及决策树依据"},
      "accessories": {
        "type": "array",
        "items": {
          "product_id": {"type": "string"},
          "level": {"type": "string", "description": "required / recommended / optional / incompatible / not evaluated"}
        }
      }
    }
  }
}
第二步：工具基本信息与参数选择
工具名称：recommend_configuration
工具类型：HTTP（POST）
工具描述：把用户的产品需求关键词发给推荐引擎，获得主机型号 + 兼容选项候选及推荐等级。在需求要素齐备后、逐步配置前调用一次以获取基准推荐。
选择接口：产品配置推荐（POST /recommend）
参数选择：message（必填，拼接用户需求原文与已抽取关键词）、region（选填，注意仅传上表允许的枚举值，否则 422）、max_accessories（选填）
Tool 3：validate_configuration
第一步：创建接口
接口名称：配置规则校验
接口路径：POST /api/v1/configuration/validate
接口说明：对候选配置做确定性规则校验：区域限制、系统组合兼容、探测器/滤线栅支持、发生器/球管规格（封装 rule_engine.check_configuration）。
请求参数设置（Body）（字段与 check_configuration 形参一一对应）

JSON
{
  "product_ids": {
    "type": "array",
    "items": {"type": "string"},
    "required": true,
    "description": "待校验的产品 ID 列表",
    "example": ["DEMO-FMT-100"]
  },
  "region": {"type": "string", "required": false, "description": "地区；传了 product_ids 未传 region 会记入 missing_fields"},
  "system_family": {"type": "string", "required": false, "description": "系统家族，例：FMT"},
  "acquisition_type": {"type": "string", "required": false, "description": "采集类型"},
  "tube_stand_id": {"type": "string", "required": false, "description": "球管支架 ID"},
  "wallstand_id": {"type": "string", "required": false, "description": "胸片架 ID"},
  "table_id": {"type": "string", "required": false, "description": "摄影床 ID"},
  "grid_id": {"type": "string", "required": false, "description": "滤线栅 ID"},
  "grid_position": {"type": "string", "required": false, "description": "滤线栅位置"},
  "detector_type": {"type": "string", "required": false, "description": "探测器类型"},
  "generator": {"type": "string", "required": false, "description": "高压发生器"},
  "tube_spec": {"type": "string", "required": false, "description": "球管规格"},
  "spec_category": {"type": "string", "required": false, "description": "规格类别"}
}
返回参数设置

JSON
{
  "passed": {"type": "boolean", "description": "是否无阻断性问题（无 severity=error）"},
  "issues": {
    "type": "array",
    "items": {
      "severity": {"type": "string", "description": "error（阻断）或 warning（提示）"},
      "code": {"type": "string", "description": "问题码，例：unknown_product / region_restriction"},
      "message": {"type": "string", "description": "规则原文/说明"},
      "product_id": {"type": "string", "description": "涉及的产品 ID，可为 null"},
      "rule_id": {"type": "string", "description": "触发的规则 ID，可为 null"}
    }
  },
  "missing_fields": {
    "type": "array",
    "items": {"type": "string"},
    "description": "校验所缺的必要字段，例：[\"region\"]"
  }
}
第二步：工具基本信息与参数选择
工具名称：validate_configuration
工具类型：HTTP（POST）
工具描述：每选定一步参数后必须调用本工具做确定性兼容校验；兼容/不兼容结论一律以本工具返回为准，Agent 不得自行判断。
选择接口：配置规则校验（POST /api/v1/configuration/validate）
参数选择：product_ids + region（核心必传）；其余按当前决策树步骤按需填入（探测器步骤传 detector_type/grid_id/grid_position，发生器步骤传 generator/tube_spec/spec_category 等）
Tool 4：validate_requirement_fields
第一步：创建接口
接口名称：需求字段校验
接口路径：POST /api/v1/requirements/validate
接口说明：对抽取的候选字段值做类型与枚举校验（货币、Incoterm、数量范围等），拒绝非法值并返回原因（封装 app/requirement_fields.py）。
请求参数设置（Body）

JSON
{
  "candidates": {
    "type": "array",
    "required": true,
    "description": "待校验的候选字段列表",
    "items": {
      "field_name": {
        "type": "string",
        "required": true,
        "description": "白名单字段名：product_query/region/quantity/intended_use/delivery_location/currency/incoterm/target_price/budget_notes/constraints/customer_name/requested_accessories/requested_services/selected_product_ids"
      },
      "value": {"type": "string", "required": true, "description": "候选值原文，例：RMB、2、Singapore"},
      "confidence": {"type": "number", "required": false, "minimum": 0, "maximum": 1, "default": 0.5}
    }
  }
}
返回参数设置

JSON
{
  "accepted": {
    "type": "array",
    "description": "通过校验并已归一化的字段",
    "items": {
      "field_name": {"type": "string"},
      "value": {"type": "string", "description": "归一化后的值，例：RMB→CNY、Singapore→singapore"},
      "confidence": {"type": "number"}
    }
  },
  "rejected": {
    "type": "array",
    "description": "被拒绝的字段及原因",
    "items": {
      "field_name": {"type": "string"},
      "raw_value": {"type": "string"},
      "reason": {"type": "string", "description": "拒绝原因，例：quantity must be a whole number / currency must be one of: USD, EUR, CNY, SGD, JPY, GBP, AUD, HKD"}
    }
  }
}
第二步：工具基本信息与参数选择
工具名称：validate_requirement_fields
工具类型：HTTP（POST）
工具描述：需求要素抽取完成后立即调用，对每个候选值做类型/枚举校验与归一化；只有 accepted 的值才能进入后续合并与配置流程。
选择接口：需求字段校验（POST /api/v1/requirements/validate）
参数选择：candidates（必填，直接传入抽取 skill 产出的 requirements 数组）
Tool 5：merge_requirements（可选）
第一步：创建接口
接口名称：需求候选合并
接口路径：POST /api/v1/requirements/merge
接口说明：把置信度 ≥0.7 的已校验候选静默合并进会话报价草稿，低置信度候选挂起为待确认项（封装 requirement_intake.merge_candidates）。
请求参数设置（Body）

JSON
{
  "session_id": {"type": "string", "required": true, "description": "会话/草稿 ID，保证多轮对话合并到同一草稿"},
  "candidates": {
    "type": "array",
    "required": true,
    "description": "已通过字段校验的候选值",
    "items": {
      "field_name": {"type": "string", "required": true},
      "value": {"type": "string", "required": true},
      "confidence": {"type": "number", "required": true, "minimum": 0, "maximum": 1}
    }
  },
  "confirmations": {
    "type": "array",
    "required": false,
    "description": "用户对挂起项的确认/否决",
    "items": {
      "field_name": {"type": "string"},
      "confirmed": {"type": "boolean"}
    }
  }
}
返回参数设置

JSON
{
  "merged": {
    "type": "array",
    "description": "本次成功写入草稿的字段（confidence ≥ 0.7 或已确认）",
    "items": {
      "field_name": {"type": "string"},
      "value": {"type": "string"}
    }
  },
  "pending_confirmations": {
    "type": "array",
    "description": "挂起待用户确认的低置信度候选",
    "items": {
      "field_name": {"type": "string"},
      "value": {"type": "string"},
      "confidence": {"type": "number"},
      "question": {"type": "string", "description": "向用户复述确认的问题文本"}
    }
  },
  "rejected": {
    "type": "array",
    "items": {
      "field_name": {"type": "string"},
      "raw_value": {"type": "string"},
      "reason": {"type": "string"}
    }
  },
  "draft_snapshot": {"type": "object", "description": "合并后的草稿字段全量视图"}
}
第二步：工具基本信息与参数选择
工具名称：merge_requirements
工具类型：HTTP（POST）
工具描述：把已校验候选合并进会话草稿：高置信度（≥0.7）静默合并，低置信度挂起并返回确认问题；用户确认后再次调用带 confirmations 落库。多轮澄清场景使用。
选择接口：需求候选合并（POST /api/v1/requirements/merge）
参数选择：session_id（必填，取会话变量）、candidates（必填，取 Tool 4 的 accepted 输出）、confirmations（选填，用户确认后回传）

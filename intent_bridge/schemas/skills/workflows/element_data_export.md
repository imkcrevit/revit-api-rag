# 元素数据导出 — Element Data Export & Reporting

适用于：批量提取元素属性、生成报表、导出数据等需要遍历+格式化的操作。

## 触发关键词
中文：导出、报表、统计、汇总、清单、明细、表格、数据、属性值、批量提取
English：export, report, summary, schedule, list, table, data, extract, batch

## 参考蓝图

```
阶段 1: 确定数据源
├── 确认目标元素类别（墙/柱/梁/房间/...）
├── 确认过滤范围（全模型 / 当前视图 / 用户选择）
└── 确认是否包含链接模型

阶段 2: 收集元素
├── FilteredElementCollector + 类别/类型过滤
├── 可选：参数值过滤（如"高度大于3m的墙"）
└── 可选：视图过滤（OwnedByView）

阶段 3: 提取属性
├── 确认要提取哪些参数（用户指定 or 常用默认集）
├── get_Parameter(BuiltInParameter.xxx) 或按名称查找
├── 处理参数类型（string/double/int/ElementId → 解析为可读值）
└── 单位转换

阶段 4: 格式化输出
├── 询问输出格式：CSV / 写入共享参数 / TaskDialog / JSON
├── 排序规则（按标高/按类型/按区域）
└── 汇总行（总数、面积合计等）
```

## 关键 API 速查

| 用途 | API |
|------|-----|
| 按名称查参数 | `element.LookupParameter("参数名")` |
| 按内置参数查 | `element.get_Parameter(BuiltInParameter.WALL_USER_HEIGHT_PARAM)` |
| 参数值读取 | `.AsString()` / `.AsDouble()` / `.AsInteger()` / `.AsValueString()` |
| 类型名称 | `element.Name` 或通过 `GetTypeId()` 获取类型元素 |
| 房间属性 | `Room.Area` / `Room.Volume` / `Room.Number` |

## 这不是固定流程
- 简单统计（"有多少面墙"）→ 直接 Collector.Count，不需要导出
- 只要一个属性（"墙的高度"）→ 不需要完整导出流程
- 用户已经说了具体参数 → 跳过"确认哪些参数"的步骤

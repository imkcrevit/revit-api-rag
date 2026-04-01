# 净高/间距计算 — Clearance & Distance Calculation

适用于：计算构件之间的净高、间距、碰撞检测等需要几何分析的多步操作。

## 触发关键词
中文：净高、净空、间距、距离、碰撞、干涉、检测、计算高度、梁底、板底、顶面、底面、标高差
English：clearance, headroom, distance, clash, interference, gap, offset, beam bottom, slab top

## 工作流本质

这类操作**不是单一 API 调用**，而是多步组合。下面是参考蓝图——根据用户实际需求**灵活裁剪和调整**，不要机械照搬。

## 参考蓝图：梁底与楼板净高

```
阶段 1: 收集源元素
├── 获取当前视图的目标构件（FilteredElementCollector + OfCategory + OwnedByView）
├── 或让用户点选具体构件（interactive:pick_object / pick_objects）
└── 确认构件类型过滤条件（BuiltInCategory.OST_StructuralFraming 等）

阶段 2: 提取几何数据
├── 获取构件的 BoundingBox 或 Geometry（get_BoundingBox / get_Geometry）
├── 找到关键标高点（梁底 = BoundingBox.Min.Z，板顶 = BoundingBox.Max.Z）
└── 注意单位转换（内部单位 feet → 用户单位 mm）

阶段 3: 处理链接模型（如涉及）
├── 获取 RevitLinkInstance（FilteredElementCollector.OfClass(typeof(RevitLinkInstance))）
├── 用户选择目标链接模型（如有多个链接）
├── 获取链接文档（linkInstance.GetLinkDocument()）
├── 获取链接模型的 Transform（linkInstance.GetTotalTransform()）
└── 在链接文档中查询目标元素，坐标需通过 Transform 转换

阶段 4: 计算与比较
├── 计算差值（净高 = 板顶Z - 梁底Z，或反过来取决于空间关系）
├── 常识校验：梁底应高于楼板顶（否则已经碰撞）
├── 考虑是否需要 ReferenceIntersector 做精确射线检测
└── 处理多对多关系（每根梁对应最近的楼板）

阶段 5: 输出结果
├── 询问用户输出格式（写入参数/CSV导出/TaskDialog显示）
├── 格式化结果（构件ID、名称、净高值、是否合规）
└── 如有不合规项，高亮标注
```

## 关键 API 速查

| 用途 | API | 备注 |
|------|-----|------|
| 收集元素 | `FilteredElementCollector` | 必须配合 OfClass/OfCategory |
| 当前视图过滤 | `.OwnedByView(activeViewId)` | 只看当前视图元素 |
| 结构梁 | `BuiltInCategory.OST_StructuralFraming` | 包括梁和桁架 |
| 楼板 | `BuiltInCategory.OST_Floors` | — |
| 包围盒 | `element.get_BoundingBox(view)` | view 可为 null 取全局 |
| 链接实例 | `RevitLinkInstance` | OfClass(typeof(RevitLinkInstance)) |
| 链接文档 | `linkInstance.GetLinkDocument()` | 可能为 null（未加载） |
| 链接变换 | `linkInstance.GetTotalTransform()` | 坐标转换必需 |
| 射线检测 | `ReferenceIntersector` | 精确碰撞，需 3D 视图 |
| 单位转换 | `UnitUtils.ConvertFromInternalUnits()` | feet → mm/m |

## 用户交互决策点

以下是需要向用户确认的关键节点（用 question 表示）：

| 决策点 | enrich 类型 | 什么时候需要 |
|--------|------------|-------------|
| 选择具体梁 vs 全部梁 | `host_pick` 或 `none` | 用户说"选择"/"指定"时 |
| 选择哪个链接模型 | `host_pick` | 项目有多个链接时 |
| 选择楼板范围 | `none` | 全部 vs 指定楼层的楼板 |
| 输出格式 | `none` | 参数备注/CSV/对话框 |
| 合规阈值 | `none` | 如果用户要做合规检查 |

## 这不是固定流程

以上蓝图是参考。根据用户实际需求灵活调整：

- 用户只说"计算净高" → 不涉及链接模型，跳过阶段 3
- 用户说"当前视图所有梁" → 不需要 pick_object，直接 Collector
- 用户已经指定了楼板 → 跳过楼板选择步骤
- 用户要的是碰撞检测不是净高 → 用 ReferenceIntersector 替代 BoundingBox 差值
- 用户要的是梁与天花板的关系 → 改 BuiltInCategory

**你的任务是理解用户意图，从蓝图中选择适用的步骤，组合成 action_plan 或 custom 代码生成请求。**

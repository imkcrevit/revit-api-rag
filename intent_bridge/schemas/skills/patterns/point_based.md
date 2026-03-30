# 点式构件创建 — Point-Based Elements

适用于：柱 Column、家具 Furniture、设备 Equipment、灯具 Lighting、卫浴 Plumbing Fixture、植物 Planting 等放置在单个点的构件。

## 触发关键词
中文：柱、柱子、结构柱、家具、沙发、桌子、椅子、设备、灯、灯具、洁具、马桶
English：column, structural column, furniture, equipment, lighting, fixture

## 必须询问的参数

1. **族类型** — enrich: `family_type:<对应类别>`
   - 柱 → `family_type:column`，家具 → `family_type:furniture`
   - 绝不允许选默认类型

2. **标高** — enrich: `level`

3. **放置点坐标** — enrich: `none`
   - 单个 XYZ 点
   - 数量 > 1 时，必须询问每个构件的独立坐标
   - 格式：`(x, y, z)`，单位 mm

4. **其他必要参数**（按具体构件）：
   - 结构柱：StructuralType（`Autodesk.Revit.DB.Structure.StructuralType.Column`）
   - 结构柱：顶部标高（如果用户提及）
   - 族实例通用：Activate() 由代码生成器处理，不需要用户关心

## 常见错误
- FamilySymbol 必须先 Activate() 再放置（代码生成器已处理）
- 结构柱必须指定 StructuralType，不能省略
- StructuralType 必须用完全限定名：`Autodesk.Revit.DB.Structure.StructuralType.Column`
- 放置点是模型空间坐标，不是屏幕坐标

## 数量模板
`构件 {i}: 位置(x,y,z) / Element {i}: position(x,y,z)`

# 线性构件创建 — Line-Based Elements

适用于：墙 Wall、梁 Beam、管道 Pipe、风管 Duct、电缆桥架 CableTray、线管 Conduit 等沿曲线创建的构件。

## 触发关键词
中文：墙、墙体、隔墙、承重墙、梁、横梁、主梁、次梁、管道、管线、风管、桥架、线管
English：wall, beam, girder, pipe, duct, cable tray, conduit

## 必须询问的参数

1. **族类型** — enrich: `family_type:<对应类别>`
   - 墙 → `family_type:wall`，梁 → `family_type:beam`，管道 → `family_type:pipe`
   - 绝不允许选默认类型

2. **标高** — enrich: `level`

3. **起点和终点坐标** — enrich: `none`
   - 每个构件需要 start(x,y,z) 和 end(x,y,z)
   - 数量 > 1 时，每个构件单独询问起终点
   - 格式：`起点(x,y,z) 终点(x,y,z)`，单位 mm

4. **其他必要参数**（按具体构件）：
   - 墙：高度、是否结构
   - 梁：StructuralType（必须用 `Autodesk.Revit.DB.Structure.StructuralType.Beam`）
   - 管道/风管：直径或截面尺寸、系统类型

## 常见错误
- API 需要 Line/Curve 对象，但用户只需提供起终点坐标，代码生成器负责转换
- 不要混淆构件高度和标高
- 数量 > 1 时绝不复用坐标
- 梁的 StructuralType 必须用完全限定名

## 数量模板
`构件 {i}: 起点(x,y,z) 终点(x,y,z) / Element {i}: start(x,y,z) end(x,y,z)`

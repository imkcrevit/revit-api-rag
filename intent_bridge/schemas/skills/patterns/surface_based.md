# 面状构件创建 — Surface-Based Elements

适用于：楼板 Floor、屋顶 Roof、天花板 Ceiling、场地 Topography 等基于闭合轮廓创建的构件。

## 触发关键词
中文：楼板、地板、板、底板、屋顶、天花、天花板、吊顶、场地、地形
English：floor, slab, roof, ceiling, topography, site

## 必须询问的参数

1. **族类型** — enrich: `family_type:<对应类别>`
   - 楼板 → `family_type:floor`，屋顶 → `family_type:roof`，天花板 → `family_type:ceiling`

2. **标高** — enrich: `level`

3. **边界轮廓点** — enrich: `none`
   - 至少 3 个点形成闭合多边形
   - 最后一个点自动连接第一个点
   - 格式：`(x1,y1), (x2,y2), (x3,y3), ...`，单位 mm
   - 如果用户说「5m x 3m」，仍需询问原点位置来计算 4 个顶点

4. **是否结构** — 楼板和屋顶需要

## 常见错误
- Floor.Create 需要 CurveLoop（闭合轮廓），不是面积尺寸
- 用户说「5m x 3m 楼板」时，必须追问原点位置
- 边界必须闭合 — 首尾自动相连
- 不要混淆板厚和族类型 — 厚度是 FloorType 定义的一部分

## 数量模板
`构件 {i}: 边界点列表 / Element {i}: boundary points`

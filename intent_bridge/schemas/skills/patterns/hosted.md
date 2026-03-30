# 宿主构件放置 — Hosted Elements

适用于：门 Door、窗 Window 等必须放置在宿主元素（通常是墙）上的构件。

## 触发关键词
中文：门、窗、窗户、单开门、双开门、推拉门、飘窗、落地窗、平开窗
English：door, window, casement, sliding door, sliding window

## 必须询问的参数

1. **宿主元素** — enrich: `host_pick`
   - 门窗必须放置在墙上，始终先要求选择宿主
   - 提示用户：「请在 Revit 中选择宿主墙体 / Select the host wall in Revit」
   - 数量 > 1 时，每个构件的宿主可能不同，逐一询问

2. **族类型** — enrich: `family_type:<对应类别>`
   - 门 → `family_type:door`，窗 → `family_type:window`

3. **标高** — enrich: `level`

4. **在宿主上的放置位置** — enrich: `none`
   - XYZ 点在墙面上的中心位置

5. **窗台高度**（仅窗户）— enrich: `none`
   - 从标高起算，不是从地面
   - 建议选项：900mm, 1000mm, 1100mm

## 常见错误
- NewFamilyInstance 对宿主构件需要 host Element 引用，不是仅坐标
- FamilySymbol 必须 Activate()（代码生成器已处理）
- 放置点在墙中心线上，不是墙面
- 窗台高度从标高算，不是从楼板面算

## 数量模板
`构件 {i}: 宿主墙ID + 位置 / Element {i}: host wall ID + location`

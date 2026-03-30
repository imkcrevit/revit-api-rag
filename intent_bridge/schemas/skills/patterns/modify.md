# 修改操作 — Modify Elements

适用于：移动、旋转、修改参数、更换类型等对已有元素的修改操作。

## 触发关键词
中文：修改、移动、旋转、改变、调整、设置、更改、偏移
English：modify, move, rotate, change, adjust, set, update, offset

## 必须询问的参数

1. **目标元素** — enrich: `host_pick`
   - 始终要求选择或指定 ElementId
   - 绝不假设「最后创建的元素」

2. **修改类型**（如果用户没有明确说明）：
   - 移动 / Move
   - 旋转 / Rotate
   - 修改参数 / Change parameter
   - 更换族类型 / Change type

3. **修改具体值**（按类型不同）：
   - 移动：位移向量 (dx, dy, dz)，单位 mm — 是相对位移，不是绝对位置
   - 旋转：旋转角度 + 旋转轴 — 两者缺一不可
   - 修改参数：参数名 + 新值
   - 更换类型：新的族类型 — enrich: `family_type:<类别>`

## 常见错误
- 移动向量是相对位移，不是目标位置
- 旋转必须同时提供轴点和角度
- 更换类型用 ChangeTypeId()，不能直接赋值
- 修改参数前需确认参数存在且可写

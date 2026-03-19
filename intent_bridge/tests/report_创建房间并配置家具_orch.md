# Model Comparison Report -- "创建房间并配置家具" (orch)

**Test input**: `创建房间并配置家具`
**Expected quantity**: 1
**Test level**: orch

## Score Summary (0-5)

| Model | Score | Composite | Steps (min) | Ask Type | Ask Level | Ask Position | No Defaults | Duration |
|-------|-------|-----------|----------|----------|-----------|-------------|-------------|----------|
| gemini | **4/5** | — | [FAIL] | [PASS] | [PASS] | [PASS] | [PASS] | 7078ms |
| codex | **4/5** | — | [FAIL] | [PASS] | [PASS] | [PASS] | [PASS] | 54225ms |
| claude | **4/5** | — | [FAIL] | [PASS] | [PASS] | [PASS] | [PASS] | 14252ms |

---
## gemini
**Score**: 4/5 | **Duration**: 7078ms

### Slots extracted (merged)
```json
{}
```
### Questions (4 total, flattened from all steps)
**Q1** [wall_type]: 请选择墙体类型：
  Options: ['常规 - 200mm', '常规 - 300mm', '砖墙 - 240mm', '其他 (自定义)']
**Q2** [wall_type]: 请选择墙体类型：
  Options: ['常规 - 200mm', '常规 - 300mm', '砖墙 - 240mm', '其他 (自定义)']
**Q3** [level]: 墙体放置在哪个标高？
  Options: ['标高 1 (0mm)', '标高 2 (3000mm)', '其他 (自定义)']
**Q4** [wall_geometry]: 请输入房间的矩形尺寸（长和宽，单位：mm）：
例如：4000, 3000
  Options: ['4000x3000', '5000x4000', '6000x5000', '其他 (自定义)']

**Positions asked for**: 0 (expected: 1)

---
## codex
**Score**: 4/5 | **Duration**: 54225ms

### Slots extracted (merged)
```json
{
  "quantity": 1
}
```
### Questions (4 total, flattened from all steps)
**Q1** [enclosure_status]: 请先确认房间边界条件：
  Options: ['已有封闭边界（已由墙体或房间分隔线围合）', '需要先创建房间分隔线再创建房间', '需要先创建墙体形成闭合区域再创建房间', '其他 (自定义)']
**Q2** [enclosure_status]: 请先确认房间边界条件：
  Options: ['已有封闭边界（已由墙体或房间分隔线围合）', '需要先创建房间分隔线再创建房间', '需要先创建墙体形成闭合区域再创建房间', '其他 (自定义)']
**Q3** [level]: 请选择房间所在标高：
  Options: ['标高 1（0mm）', '标高 2（3000mm）', '标高 3（6000mm）', '其他 (自定义)']
**Q4** [room_point_uv]: 请输入房间放置点 UV 坐标（二维）：格式示例：12000,8000
  Options: ['其他 (自定义)']

**Positions asked for**: 0 (expected: 1)

---
## claude
**Score**: 4/5 | **Duration**: 14252ms

### Slots extracted (merged)
```json
{}
```
### Questions (5 total, flattened from all steps)
**Q1** [wall_type]: 请选择墙体类型：
  Options: ['内墙 200mm', '内墙 150mm', '内墙 100mm', '外墙 300mm', '其他 (自定义)']
**Q2** [wall_type]: 请选择墙体类型：
  Options: ['内墙 200mm', '内墙 150mm', '内墙 100mm', '外墙 300mm', '其他 (自定义)']
**Q3** [level]: 放置在哪个标高？
  Options: ['标高 1 (0mm)', '标高 2 (3000mm)', '标高 3 (6000mm)', '其他 (自定义)']
**Q4** [room_boundary]: 请输入房间边界的四个角点坐标（矩形房间）：
角点 1: (x, y)
角点 2: (x, y)
角点 3: (x, y)
角点 4: (x, y)
格式示例: 0,0; 4000,0; 4000,3000; 0,3000
  Options: ['其他 (自定义)']
**Q5** [wall_height]: 墙体高度：
  Options: ['2700mm', '3000mm', '3300mm', '其他 (自定义)']

**Positions asked for**: 0 (expected: 1)

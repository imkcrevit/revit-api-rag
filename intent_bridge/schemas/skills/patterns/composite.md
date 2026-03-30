# 复合操作拆分 — Composite Action Decomposition

当用户的一句话包含多个步骤时，必须拆分为 `action_plan`。

## 触发关键词
中文：并、然后、同时、之后、接着、再、以及、顺便、配置、布置、装修
English：and then, also, after that, with, including, furnish, layout

## 什么时候必须拆分

用户的请求需要 **多次不同的 API 调用** 时，必须用 `action_plan` 格式：

| 用户说 | 拆分步骤 |
|--------|---------|
| "创建房间并配置家具" | 1.创建墙体 → 2.创建房间 → 3.放置家具 |
| "创建一面墙然后开个门" | 1.创建墙 → 2.在墙上创建门 |
| "建一个带窗户的办公室" | 1.创建墙体 → 2.创建窗户 → 3.创建房间 |
| "创建楼板并放置柱子" | 1.创建楼板 → 2.放置柱子 |

## 什么时候不需要拆分

同一个 API 调用可以完成的，不要拆：
- "创建三面墙" → 单个操作，quantity=3
- "创建一个结构柱" → 单个操作
- "删除所有墙" → 单个操作

## 拆分规则

1. **每个 step 都是独立的 intent**，有自己的 questions 和 slots
2. **step 之间有依赖关系时说明白**：
   - "在步骤1创建的墙上开门" → step 2 的 host_wall 依赖 step 1 的结果
3. **每个 step 都必须遵守对应操作模式的参数规则**（不能因为是复合操作就跳过参数）
4. **共享参数只问一次**：如果所有 step 在同一标高，level 只在第一个 step 问

## 输出格式

```json
{
  "intent": "composite",
  "confidence": 0.9,
  "action_plan": [
    {
      "step": 1,
      "intent": "create_wall",
      "display_name": "创建围合墙体 / Create enclosing walls",
      "api_method": "Wall.Create",
      "description": "Create walls forming the room boundary",
      "slots": {},
      "questions": [
        { "slot": "wall_type", "text": "...", "enrich": "family_type:wall" }
      ]
    },
    {
      "step": 2,
      "intent": "create_room",
      "display_name": "创建房间 / Create room",
      "api_method": "NewRoom",
      "description": "Place room inside enclosed walls from step 1",
      "slots": {},
      "questions": [
        { "slot": "room_point", "text": "...", "enrich": "none" }
      ]
    }
  ],
  "summary": ""
}
```

## 常见错误
- 不要把 quantity > 1 的同类操作拆成多个 step（那是数量，不是复合）
- 每个 step 的 questions 不能为空（除非 slots 已经从用户输入中提取了所有参数）
- 不要省略后续 step 的参数 — "反正前面问过了" 是错的，每个 step 独立

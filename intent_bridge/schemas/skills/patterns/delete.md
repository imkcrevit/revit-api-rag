# 删除操作 — Delete Elements

适用于：删除单个或批量元素。

## 触发关键词
中文：删除、移除、清除、去掉、拆除
English：delete, remove, clear, demolish

## 必须询问的参数

1. **目标元素** — enrich: `host_pick`
   - 单个删除：要求选择或指定 ElementId
   - 批量删除（「删除所有柱子」）：可直接按类别执行，不需要逐一选择

**只在目标不明确时才提问。**

## 常见错误
- Document.Delete 在事务内不可逆，务必确认目标
- 删除宿主元素（墙）会连带删除其上的门窗
- 批量删除用 FilteredElementCollector + OfCategory，不要逐个 ID 删

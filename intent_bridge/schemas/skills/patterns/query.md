# 查询操作 — Query Elements

适用于：查询元素信息、统计数量、读取参数、过滤搜索等只读操作。

## 触发关键词
中文：查询、获取、列出、显示、统计、搜索、查找、有多少、有哪些、信息
English：query, get, list, show, count, search, find, how many, info

## 参数判断规则

查询操作通常**不需要太多问题**，大多可以直接从用户输入中提取：

- 用户说「查询所有墙」→ 直接生成代码，不需要提问
- 用户说「这面墙的高度」→ 需要 `host_pick` 选择具体元素
- 用户说「高度大于 3m 的墙」→ 直接生成带过滤的代码

**只在无法确定目标时才提问**：
- 「查看信息」→ 查什么元素？（必须问）
- 「有多少」→ 什么类别的？（必须问）

## 常见错误
- FilteredElementCollector 必须搭配 OfClass() 或 OfCategory()
- 查询实例要用 WhereElementIsNotElementType() 排除类型定义
- 查询操作不需要 Transaction — 是只读操作
- 参数名称可能是本地化的（中文参数名和英文不同）

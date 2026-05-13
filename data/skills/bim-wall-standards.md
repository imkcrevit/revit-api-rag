---
name: BIM 墙体命名与分类规范
description: Revit 墙体类型命名、结构功能、耐火等级与构造层规则
version: "1.0"
author: built-in
module: global
enabled: true
tags:
  - BIM
  - Wall
  - Architecture
  - Naming
---

# BIM 墙体命名与分类规范

> 适用范围：所有创建/修改/查询墙体（`Wall`、`WallType`）的操作。
> 用户输入包含「墙、墙体、隔墙、外墙、剪力墙、Wall」等关键词时本规范自动生效。

## 一、墙体类型命名格式

```
<位置>_<构造类型>_<厚度mm>_<耐火等级>
```

| 字段 | 取值 | 示例 |
|------|------|------|
| 位置 | `外墙` / `内墙` / `隔墙` / `分户墙` | 外墙 |
| 构造类型 | `砼` / `砌块` / `轻钢龙骨` / `玻璃幕墙` / `预制` | 砼 |
| 厚度 | 整数 mm | 200 |
| 耐火等级 | `F0.5` / `F1.0` / `F1.5` / `F2.0` / `F3.0` / `F4.0` | F2.0 |

**示例**：

- `外墙_砼_200_F2.0`
- `内墙_砌块_200_F1.5`
- `隔墙_轻钢龙骨_100_F1.0`
- `分户墙_砼_200_F1.5`
- `外墙_玻璃幕墙_50_F1.0`

## 二、结构功能（Function）取值规则

| 中文 | Revit Function 枚举 | 适用场景 |
|------|---------------------|----------|
| 外墙 | `Exterior` | 直接接触室外环境 |
| 内墙 | `Interior` | 分隔室内空间 |
| 基础挡墙 | `Foundation` | 标高 ±0.000 以下 |
| 挡土墙 | `Retaining` | 室外挡土 |
| 临时隔墙 | `Soffit` | 工序临时分隔 |
| 内核 | `CoreShaft` | 设备/楼梯井道 |

## 三、构造层（Compound Structure）建模规则

墙体必须包含明确的构造层定义，**禁止**使用 *Generic 200mm* 等通用类型。

| 层位置 | 功能 | 常用材质 |
|--------|------|----------|
| Finish 1 [4] | 室外/正面饰面 | 涂料、面砖、石材 |
| Substrate [2] | 找平层 | 水泥砂浆 |
| Thermal/Air [3] | 保温层 | XPS、EPS、岩棉 |
| Structure [1] | 结构核心 | 混凝土、砌块 |
| Membrane Layer | 防水/防潮 | SBS、PVC |
| Finish 2 [5] | 室内/背面饰面 | 涂料、瓷砖 |

> 注：结构核心层 `Structure [1]` 厚度必须等于「类型名厚度」字段（如 `_200_` → 200mm）。

## 四、必填参数

| 参数 | 类型 | 来源 |
|------|------|------|
| `Fire Rating` | string | 类型参数，与名称中耐火等级一致 |
| `Type Mark` | string | 类型编号，如 `QT-EW01` |
| `Function` | enum | 与名称中位置一致 |
| `Structural Usage` | enum | 非承重 `Non-bearing` / 承重 `Bearing` / 抗剪 `Shear` |
| `Assembly Code` | string | 来自项目分类编码（如 GB/T 51269） |

## 五、生成墙体的硬性约束（给 AI）

- 创建墙体使用 `Wall.Create(doc, curve, wallTypeId, levelId, height, offset, flip, structural)`，**禁止**使用过时重载 `Wall.Create(doc, curve, levelId, structural)`（默认类型不可控）。
- 创建前必须用 `FilteredElementCollector` + `WallType` 过滤获取**实际存在**的墙类型 ElementId，禁止假设 "默认墙" 存在。
- 设置高度优先使用 `WALL_USER_HEIGHT_PARAM`（Unconnected Height）或 `WALL_HEIGHT_TYPE`（约束顶部到某标高），**禁止**写死 `3000mm`。
- 改变墙的定位线需要修改 `WALL_KEY_REF_PARAM`（Location Line），不要直接平移 `LocationCurve`。
- 涉及外墙时优先开启 `WALL_ATTR_ROOM_BOUNDING = 1`，便于房间面积统计。

## 六、反模式

| 错误做法 | 为什么错 | 正确做法 |
|----------|----------|----------|
| 使用 `Generic - 200mm` | 无构造、无耐火、无饰面 | 创建/选择规范命名的类型 |
| 名称写英文 `Exterior Wall 200` | 与项目其他文档不一致 | 中文命名 |
| 一面墙跨多个防火分区 | 影响消防分析 | 在分区线处打断 |
| 改类型名称不同步改 `Type Mark` | 明细表错乱 | 同步更新两项 |
| 墙体高度从 -100 写到 +3000 | 越过结构标高，造成净空错误 | 用顶部约束到上层标高 |

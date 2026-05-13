---
name: BIM 族与类型命名规范
description: Revit 族（Family）、族类型（Family Type）、共享参数的命名与组织规则
version: "1.0"
author: built-in
module: global
enabled: true
tags:
  - BIM
  - Family
  - Naming
  - Shared Parameters
---

# BIM 族与类型命名规范

> 适用范围：所有族文件（.rfa）、项目内族实例及类型的创建、加载、重命名操作。
> 用户输入包含「族、Family、构件、FamilySymbol、类型」时本规范自动生效。

## 一、族文件命名（.rfa）

### 1.1 命名格式

```
<专业>_<类别>_<构件>_<规格或材质>
```

| 字段 | 取值示例 |
|------|----------|
| 专业 | `建`（建筑） / `结`（结构） / `给排`（给排水） / `暖通` / `电气` / `智能` |
| 类别 | 门、窗、家具、灯具、设备、阀门、配电柜… |
| 构件 | 双扇平开门、办公桌、风机盘管… |
| 规格或材质 | `1200x2100`、`50W`、`DN100`… |

**示例**：

- `建_门_双扇平开门_1500x2100.rfa`
- `结_梁_矩形混凝土梁.rfa`
- `暖通_设备_风机盘管_四管制.rfa`
- `电气_灯具_LED 筒灯_18W.rfa`
- `给排_阀门_闸阀_DN100.rfa`

### 1.2 文件位置

```
project_families/
├── 建筑/
├── 结构/
├── 机电/
│   ├── 给排水/
│   ├── 暖通/
│   └── 电气/
├── 室内/
└── 场地/
```

## 二、族类型（Type）命名

族文件可能包含多个类型，类型名称用于在 Revit 项目中选择不同规格。

### 2.1 命名格式

```
<规格1>x<规格2>_<可选关键属性>
```

**示例**：

| 族 | 类型示例 |
|-----|----------|
| 单扇门 | `900x2100_甲级防火`、`1000x2400` |
| 矩形梁 | `300x600`、`400x800_C30` |
| 风机盘管 | `FP-68_四管制_嵌入式`、`FP-102_两管制_明装` |
| 桥架 | `100x50_镀锌`、`200x100_不锈钢` |

### 2.2 类型参数命名（关键参数）

| 参数 | 类型 | 单位 |
|------|------|------|
| `Type Mark` | string | — |
| `Width` / `宽度` | length | mm |
| `Height` / `高度` | length | mm |
| `Depth` / `深度` | length | mm |
| `Material` / `材质` | material | — |
| `Manufacturer` / `生产厂家` | string | — |
| `Model` / `型号` | string | — |
| `Cost` / `成本` | currency | 元 |

## 三、共享参数（Shared Parameters）

### 3.1 组织结构

共享参数文件统一存放在项目根目录：

```
project_root/
└── shared_parameters.txt
```

分组按专业 + 用途：

| 分组 | 用途 |
|------|------|
| `BIM-通用` | Type Mark、Assembly Code 等 |
| `BIM-建筑` | 防火、门窗编号 |
| `BIM-结构` | 配筋率、构件编号 |
| `BIM-机电` | 系统编号、压力等级 |
| `BIM-项目` | 项目阶段、楼栋号 |

### 3.2 命名规范

- **必须中文** 或 **统一英文**，禁止中英混用
- GUID **永不修改**，否则视为新参数
- 参数名包含单位提示，如 `净高(mm)`、`流量(L/s)`

## 四、给 AI 的硬性约束

- 加载族使用 `doc.LoadFamily(filePath, out family)`，**必须**检查返回值；加载失败时不要静默忽略。
- 获取 `FamilySymbol`（即 Type）后**必须**先调用 `symbol.Activate()` 再放置实例，否则放置会失败。
- 创建实例使用 `doc.Create.NewFamilyInstance(...)` 时传入的 `FamilySymbol` 必须是 **激活状态**。
- 类型重命名使用 `FamilySymbol.Name = "新名"`，**不要**直接改 `ElementName` 参数。
- 共享参数绑定时使用 `BindingMap.Insert/ReInsert`，**必须**指定正确的 `BuiltInParameterGroup`。
- 涉及多语言版本时，**禁止**用 `Name` 字符串匹配 BuiltInCategory；使用 `BuiltInCategory` 枚举或 `Category.Id`。

## 五、反模式

| 错误做法 | 为什么错 | 正确做法 |
|----------|----------|----------|
| 族名带空格或特殊字符 `门 - 双扇.rfa` | 加载 API 易出错、版本管理混乱 | 用下划线连接 |
| 类型名带规格的同时再加 ID | 重复、易错 | 规格 + 关键属性即可 |
| 把项目参数当共享参数 | 跨项目无法迁移 | 一律共享参数 |
| 修改共享参数名忘记更新 GUID 引用 | 明细表/标签失效 | 名字可改，GUID 不动 |
| 直接 `new FamilyInstance` | API 不允许 | 使用 `doc.Create.NewFamilyInstance` |
| 未激活就放置 FamilySymbol | 抛 `InvalidOperationException` | 先 `Activate()` 再放置 |

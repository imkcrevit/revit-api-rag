---
name: BIM 机电管线着色规范
description: Revit 给排水、暖通、电气专业管线颜色、线宽与过滤器统一标准，对齐 GB/T 51269-2017 建筑信息模型分类和编码标准
version: "1.0"
author: built-in
module: global
enabled: true
tags:
  - BIM
  - MEP
  - Color
  - Filter
  - View Template
---

# BIM 机电管线着色规范

> 适用范围：所有涉及给排水、暖通、电气管线/管道/桥架的 *Override Graphics*、*Filter*、*View Template* 操作。
> 用户输入包含「管道 / 风管 / 桥架 / 线管 / 颜色 / 过滤器 / 视图样板」时本规范自动生效。

## 一、颜色编码总表（RGB）

### 给排水（Plumbing）

| 系统 | System Classification | RGB | 备注 |
|------|----------------------|-----|------|
| 生活给水（J） | Domestic Cold Water | `0, 153, 204` | 青蓝 |
| 生活热水（RJ） | Domestic Hot Water | `204, 0, 0` | 朱红 |
| 生活回水（RH） | Hot Water Return | `204, 102, 0` | 橙红 |
| 污水（W） | Sanitary | `102, 51, 0` | 深棕 |
| 废水（F） | Waste | `153, 102, 51` | 棕色 |
| 雨水（Y） | Storm | `0, 153, 76` | 翠绿 |
| 通气（T） | Vent | `153, 153, 153` | 浅灰 |
| 消火栓（XH） | Fire Hydrant | `255, 0, 0` | 纯红 |
| 自动喷淋（ZP） | Sprinkler | `255, 102, 102` | 浅红 |

### 暖通空调（HVAC）

| 系统 | RGB | 备注 |
|------|-----|------|
| 送风（SA） | `0, 102, 204` | 海蓝 |
| 回风（RA） | `0, 204, 102` | 翠绿 |
| 新风（OA） | `255, 204, 0` | 明黄 |
| 排风（EA） | `153, 51, 153` | 紫色 |
| 排烟（SE） | `102, 0, 102` | 深紫 |
| 冷冻供水（CHWS） | `0, 204, 204` | 青色 |
| 冷冻回水（CHWR） | `0, 153, 153` | 深青 |
| 冷却供水（CWS） | `102, 178, 255` | 浅蓝 |
| 冷却回水（CWR） | `51, 102, 153` | 深蓝 |
| 热水供水（HWS） | `255, 102, 0` | 橙色 |
| 热水回水（HWR） | `204, 51, 0` | 深橙 |

### 电气（Electrical）

| 系统 | 桥架/线管颜色 | RGB | 备注 |
|------|---------------|-----|------|
| 强电（动力） | 红色 | `255, 0, 0` | 主供电 |
| 强电（照明） | 黄色 | `255, 204, 0` | 照明回路 |
| 弱电（数据/语音） | 绿色 | `0, 153, 0` | 智能化 |
| 弱电（安防/监控） | 浅绿 | `102, 204, 0` | CCTV、门禁 |
| 消防报警 | 紫红 | `204, 0, 102` | FAS |
| 应急照明 | 橙色 | `255, 153, 51` | EPS |
| 接地 | 黄绿 | `153, 204, 0` | PE |

## 二、线宽与样式

| 类别 | Cut（剖切） | Projection（投影） | 样式 |
|------|-------------|-------------------|------|
| 主干管（DN ≥ 100） | 6 | 4 | Solid |
| 支管（DN < 100） | 4 | 2 | Solid |
| 隐蔽管线（吊顶/楼板内） | 2 | 1 | Dash |
| 拟拆除管线 | 4 | 2 | DashDot，半色调（Halftone） |

## 三、过滤器（Filter）配置规则

为每个系统建立一个 `Selection Filter` + `Rule-Based Filter`，命名格式：

```
MEP_<专业>_<系统缩写>
例如：MEP_HVAC_SA、MEP_Plumb_J、MEP_Elec_动力
```

**规则字段**：

- 管道/风管：`System Classification` = `<系统类型>` AND `System Name` 包含缩写
- 桥架/线管：`Type Name` 包含 `<专业缩写>_<系统>`

## 四、视图样板（View Template）建议

按专业建立三个基础样板：

1. `BIM-机电-给排水-平面` → 启用上述给排水过滤器
2. `BIM-机电-暖通-平面` → 启用 HVAC 过滤器
3. `BIM-机电-电气-平面` → 启用电气过滤器

每个样板必须显式设置：

- `V/G Overrides Filters` 中所有过滤器的 *Projection/Surface Lines* 颜色、线宽
- `Detail Level` = Medium 或 Fine
- `Discipline` 与对应专业一致

## 五、给 AI 的硬性约束

- 修改图形覆盖时使用 `View.SetFilterOverrides(filterId, OverrideGraphicSettings)`，**禁止**直接改 `Element.LineStyle`（会污染模型本身）。
- 创建颜色对象使用 `new Color(r, g, b)`（**0-255 整数**），不要用 0-1 浮点。
- 应用到视图样板时必须先确认 `ViewTemplate.IsTemplate == true`。
- 涉及风管/管道系统识别时，使用 `MEPSystem.SystemType` 或 `MechanicalSystemType` / `PipingSystemType`，**不要**用字符串匹配 `Name`（多语言会失败）。

## 六、反模式

| 错误做法 | 为什么错 | 正确做法 |
|----------|----------|----------|
| 给每根管道单独 override | 一旦视图样板刷新就丢失 | 用 Filter 批量覆盖 |
| 颜色用 RGB(255,255,255) | 白色在白底视图不可见 | 强制非白色 |
| 不区分供回水颜色 | 现场施工无法识别流向 | 供水/回水必须区分 |
| 修改系统族类型直接改颜色 | 影响所有项目 | 通过项目级过滤器实现 |

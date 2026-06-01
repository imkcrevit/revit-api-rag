# Frontend Style Guide

[English](./frontend-style-guide.en.md) | **中文**

本项目前端遵循 Graptolite BIM/AI 工具界面风格：暖白工作底、清晰分层线框、克制的 rust accent、少量深青对比色、Inter + IBM Plex Mono 字体，以及面向 Revit API / RAG 工作流的清晰信息密度。工具界面应保持主站的克制感，但不能因全浅色而失焦。

## Design Tokens

样式 token 位于 `frontend/src/index.css`：

```css
:root {
  --bg: #fbfaf6;
  --bg2: #f1eee7;
  --bg3: #e5ded4;
  --panel: #fffdf8;
  --panel-strong: #f8f3ec;
  --dark: #171713;
  --mid: #454941;
  --faint: #6d7168;
  --subtle: #d7d0c4;
  --accent: #b45535;
  --accent2: #873a24;
  --accent-contrast: #164241;
  --accent-contrast2: #0f302f;
  --success: #2f6f55;
  --danger: #b33b2e;
  --warning: #9a5a16;
  --line: rgba(29, 29, 27, 0.18);
  --radius-xs: 6px;
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --display: 'Inter', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
  --serif: 'Inter', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
  --mono: 'IBM Plex Mono', monospace;
}
```

## Usage Rules

- 页面背景使用 `--bg`，工具面板和卡片使用 `--panel`，辅助区域使用 `--bg2`、`--bg3` 或 `--panel-strong`。
- 主体文字使用 `--serif`，标题使用 `--display`，技术标签、状态、日志、按钮和元信息使用 `--mono`。
- 默认正文和状态信息避免使用过浅灰；`--faint` 只用于次要元信息，关键标签至少使用 `--mid`。
- 按钮、输入框、tab、提示条和工作面板使用 `--radius-sm` 到 `--radius-md`，保持柔和但不做大胶囊。
- 主按钮优先使用深色底，hover 时切换到 `--accent2`；需要额外对比时使用 `--accent-contrast`，不要引入大面积蓝紫渐变。
- 卡片只用于重复项、工具面板、弹窗和明确边界的工作区；避免卡片套卡片。
- hover 动效保持轻量：颜色变化、细线变化或 `translateY(-1px)`。
- 移动端优先保证 tab、按钮和 label 不溢出；不要用 viewport 宽度动态缩放字体。

## Frontend Entry Points

- 字体加载：`frontend/index.html`
- 全局 token 和基础控件：`frontend/src/index.css`
- 顶层 shell / header / tab bar：`frontend/src/App.tsx`

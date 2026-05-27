# Frontend Style Guide

**English** | [中文](./frontend-style-guide.md)

The frontend follows the Graptolite BIM/AI tool style: near-white surfaces, low-contrast rules, restrained rust accents, Inter + IBM Plex Mono typography, and a clear working density for Revit API / RAG workflows.

## Design Tokens

Tokens live in `frontend/src/index.css`:

```css
:root {
  --bg: #fdfdfb;
  --bg2: #f7f7f4;
  --bg3: #efeee9;
  --panel: #ffffff;
  --dark: #1d1d1b;
  --mid: #5f615c;
  --faint: #8b8d86;
  --subtle: #eeeeea;
  --accent: #c45f3c;
  --accent2: #a94b2f;
  --line: rgba(29, 29, 27, 0.1);
  --display: 'Inter', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
  --serif: 'Inter', 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif;
  --mono: 'IBM Plex Mono', monospace;
}
```

## Usage Rules

- Use `--bg` for page backgrounds, `--panel` for tool surfaces and cards, and `--bg2` or `--bg3` for secondary regions.
- Use `--serif` for body text, `--display` for headings, and `--mono` for technical labels, status text, logs, buttons, and metadata.
- Keep buttons rectangular with a 2px radius; primary buttons should use a dark base and move to `--accent2` on hover.
- Use cards only for repeated items, tool panels, modals, and clearly framed workspaces; avoid nesting cards.
- Keep motion restrained: color changes, thin-line changes, or `translateY(-1px)`.
- On mobile, prioritize wrapping and overflow safety for tabs, buttons, and labels; do not scale font sizes with viewport width.

## Frontend Entry Points

- Font loading: `frontend/index.html`
- Global tokens and base controls: `frontend/src/index.css`
- Top-level shell / header / tab bar: `frontend/src/App.tsx`

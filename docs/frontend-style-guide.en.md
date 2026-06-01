# Frontend Style Guide

**English** | [中文](./frontend-style-guide.md)

The frontend follows the Graptolite BIM/AI tool style: warm near-white work surfaces, clearer layered rules, restrained rust accents, a small deep-teal contrast color, Inter + IBM Plex Mono typography, and a clear working density for Revit API / RAG workflows. The UI should stay aligned with the main site, but it should not feel defocused from an all-light palette.

## Design Tokens

Tokens live in `frontend/src/index.css`:

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

- Use `--bg` for page backgrounds, `--panel` for tool surfaces and cards, and `--bg2`, `--bg3`, or `--panel-strong` for secondary regions.
- Use `--serif` for body text, `--display` for headings, and `--mono` for technical labels, status text, logs, buttons, and metadata.
- Avoid very pale gray for default body and status text; reserve `--faint` for secondary metadata, and use at least `--mid` for important labels.
- Use `--radius-sm` to `--radius-md` for buttons, inputs, tabs, banners, and work panels. Keep the geometry soft without turning controls into large pills.
- Primary buttons should use a dark base and move to `--accent2` on hover. Use `--accent-contrast` when extra contrast is needed, and avoid large blue/purple gradients.
- Use cards only for repeated items, tool panels, modals, and clearly framed workspaces; avoid nesting cards.
- Keep motion restrained: color changes, thin-line changes, or `translateY(-1px)`.
- On mobile, prioritize wrapping and overflow safety for tabs, buttons, and labels; do not scale font sizes with viewport width.

## Frontend Entry Points

- Font loading: `frontend/index.html`
- Global tokens and base controls: `frontend/src/index.css`
- Top-level shell / header / tab bar: `frontend/src/App.tsx`

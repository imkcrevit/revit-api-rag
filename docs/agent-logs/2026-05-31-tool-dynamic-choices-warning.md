# Agent Change Log: Tool Dynamic Choices Warning

Date: 2026-05-31

## Request

Check the remaining frontend change and push it if it is valid.

## Scope

- Updated `frontend/src/components/bridge/ToolLibrary.tsx`.
- Updated `frontend/src/index.css`.

## Behavior

When a saved tool has dynamic Revit choices and the live Revit query fails,
the Tool Library now keeps the tool loaded instead of failing the whole load.
The UI shows a warning that dynamic choices are unavailable and still lets the
user view code or fill parameters manually.

The tool load flow also clears stale run output and warning text when changing
tools or reloading a selected tool.

## Validation

Passed:

- `cd frontend && npm run build`
- `cd frontend && npm run lint`
- `git diff --check`

Build note:

- `npm run build` touched `frontend/dist/index.html` as a generated side effect.
  That generated file was restored because this change only targets source UI
  behavior and styles.

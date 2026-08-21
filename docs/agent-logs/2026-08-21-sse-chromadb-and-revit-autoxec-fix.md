# Agent Change Log: ChromaDB SSE Recovery and Revit Auto-Execution

Date: 2026-08-21

## Incidents

During the live Revit demo, the React pipeline showed `SSE 500: Internal
Server Error` after the saved-tool check. The request had actually advanced to
`POST /api/v1/bridge/generate-stream`; it was not a failure of the tool-list
route.

The same session also showed that interactive Revit picking and level queries
worked only after `allowRemoteCodeExecution` was enabled. Once enabled, every
dynamic-code execution still opened a Revit confirmation dialog containing C#
code, which was not useful to the intended non-programmer audience.

## Root Causes

The container starts as root to copy Docker secrets, then runs the application
as uid/gid 1000. The bind-mounted ChromaDB directories and SQLite files were
owned by root and were not writable by uid 1000. ChromaDB opens its SQLite
files in read/write mode even during retrieval. Startup therefore logged
`attempt to write a readonly database`, and the later retriever initialization
failed with a secondary `RustBindingsAPI` cleanup exception, producing the
HTTP 500.

The Revit code confirmation was implemented centrally in
`ExecuteCodeEventHandler.Execute`, so it appeared for both freshly generated
code and solidified-tool execution.

## Changes

- `scripts/docker-entrypoint.sh` now repairs ownership and owner write access
  for `/app/data/sqlite` and `/app/data/chromadb` before dropping privileges.
  A genuinely read-only mount now fails container startup instead of failing
  later as an opaque SSE 500.
- Removed the per-execution Revit `TaskDialog` from
  `ExecuteCodeEventHandler`. Dynamic code and solidified tools now execute
  directly once the installation-level remote-code switch is enabled.
- Retained the authenticated Slot token, `allowRemoteCodeExecution` opt-in,
  server-side sandbox review, symbol/assembly restrictions, and Revit
  transaction boundary.
- Retained the separate confirmation for registering a solidified tool because
  that action persists remotely supplied code to the local Revit plugin data.
- Updated the installer revision to `2026-08-21-autoxec1` and documented the
  execution behavior.
- Changed the server `.env` permission to mode `0600`; the file and its values
  remain untracked and are not included in this log.

## Runtime Validation

Passed:

- Docker image rebuilt and the service recreated successfully.
- Container is healthy, its Python process runs as uid/gid 1000, and both
  ChromaDB SQLite files are owned and writable by `appuser`.
- Revit automatically reconnected to authenticated WebSocket Slot 1 after the
  backend restart.
- Authenticated `POST /api/v1/bridge/api-search` returned HTTP 200 with three
  API results and three SDK results.
- Authenticated full `generate-stream` returned HTTP 200 with 17 progress
  events, one `done` event, and zero `error` events.
- `RevitMCPCommandSet` `Release R26` build completed with zero errors and the
  same three pre-existing warnings.
- The rebuilt command DLL contains no code-execution confirmation text and
  still contains the solidified-tool registration confirmation.
- Shell syntax, Bridge transport tests, and `git diff --check` passed.

The RAG startup consistency check still reports that the ChromaDB API metadata
contains 28,863 records while the SQLite API database contains 27,596 rows.
This did not block retrieval or generation, but the index should be rebuilt in
a separate data-maintenance task to restore exact parity.

## Replacement Demo Kit

- Revision: `2026-08-21-autoxec1`.
- URL:
  `https://graptolite.ai/downloads/revit-demo-kit-20260821-autoxec1-43d44784.7z`.
- Size: `8,341,153` bytes.
- SHA-256:
  `46525d5978d68a974ad58e4361faf68925960ddd77b367a50ceb3cf8ff5e011c`.
- Public download returned HTTP 200, matched the source checksum, and passed an
  AES-encrypted `7z t` archive test.
- The archive password and Slot 1 token are intentionally excluded from git.

The user must close Revit, install this revision with
`-EnableRemoteCodeExecution`, restart Revit, and click `Revit MCP Switch`.
Windows/Revit execution without the dialog remains pending that user-side
verification.

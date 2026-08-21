# Agent Change Log: Remote Revit Demo Bridge

Date: 2026-08-21

## Request

Confirm that the Revit plugin changes are complete, deploy the remote Bridge,
prepare everything required on the Revit 2026 workstation, and publish a
downloadable configuration kit for the user's live connection and demo
recording.

## Scope

- Completed the outbound Revit WebSocket route at
  `wss://graptolite.ai/api/v1/bridge/ws/{slot_id}` while keeping TCP port 18080
  loopback-only.
- Added slot-token loading from environment values or Docker secret files.
- Fixed shared FastAPI dependencies to accept `HTTPConnection`, avoiding a
  bogus required `request` query parameter on HTTP and WebSocket routes.
- Added a relay-only service health response that explicitly separates server
  readiness from the pending Windows/Revit connection.
- Required slot ID plus the matching slot token on functional public Bridge
  HTTP routes. Only relay health and slot availability remain unauthenticated.
- Added browser support for a masked, session-only slot token and included the
  token on API and SSE calls.
- Added a root-only container bootstrap step that copies Docker's root-owned
  secret mount to a mode-0400 appuser runtime file, then drops privileges before
  starting Python.
- Added the Nginx WebSocket/HTTP proxy snippet ahead of the existing generic
  `/api/` compatibility redirect.
- Built the Revit 2026 plugin and command set, added safe default WebSocket
  settings, and added a PowerShell installer with timestamped backups.
- Added the Chinese Demo Kit guide, including safe read-only startup, explicit
  code-execution opt-in, troubleshooting, and cleanup instructions.
- Corrected current plugin documentation from 23 to 24 registered commands.

## Deployment

- Container: `revit-rag`, bound to `127.0.0.1:7860`, reports healthy.
- Public UI: `https://graptolite.ai/revit/`.
- Public relay health:
  `https://graptolite.ai/api/v1/bridge/service-health`.
- Nginx configuration passed `nginx -t` and was reloaded. The previous active
  homepage configuration was preserved at
  `/etc/nginx/conf.d/graptolite-homepage.conf.pre-revit-20260821`.
- Full Revit 2026 and CommandSet binaries are in the encrypted Demo Kit; the
  archive contains 23 files and 24 registered commands.
- Download:
  `https://graptolite.ai/downloads/revit-demo-kit-20260821-7b28ab30.7z`.
- Archive size: 8,345,217 bytes.
- Archive SHA-256:
  `198c7cf8151345164abf5662638d76cb1e3974ae20fb3b507f0eaeb68885ee87`.
- The archive password and the slot token are intentionally not stored in git.

## Validation

Passed:

- `pytest` across all three test files: 9 passed.
- `npm run build` and `npm run lint`.
- Main Revit plugin `Release R26` build: 0 warnings, 0 errors.
- CommandSet `Release R26` build: 0 errors. Four existing non-blocking warnings
  remain for the explicit `System.Net.Http` reference, a Revit 2026 obsolete
  curve-intersection overload, and an unused exception variable.
- Docker build and container health check.
- Container secret runtime file is mode 0400 and owned by uid/gid 1000; the
  Python process runs as uid/gid 1000 after bootstrap.
- Public UI HTML and final JavaScript asset returned HTTP 200.
- Public service-health and slots returned HTTP 200.
- Functional Bridge request without slot credentials returned HTTP 403;
  correct slot credentials returned HTTP 200.
- A real TLS WebSocket client connected through the public Nginx route, sent
  the correct first-message token, and registered Slot 1 successfully. It was
  then closed and Slot 1 returned to `free`.
- The published archive was downloaded through the public URL, matched the
  source SHA-256, and passed `7z t` with AES header encryption enabled.
- RAG data validation reported 27,596 API entries and 1,232 SDK examples.
- `git diff --check` and shell syntax checks.

## Remaining External Validation

- No Windows/Revit process is available on this Linux host, so actual Revit
  ribbon loading and command execution remain pending the user's workstation
  test. The public WSS transport itself was validated with a protocol-compatible
  client.
- `OPENROUTER_API_KEY` is not configured on the server. Relay health, plugin
  connection, slot authentication, and direct Bridge checks work now, but
  intent analysis, embedding-backed retrieval, and generated-code demo stages
  require that runtime secret before recording the full workflow.

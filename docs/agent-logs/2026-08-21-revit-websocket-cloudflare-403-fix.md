# Agent Change Log: Revit WebSocket Cloudflare 403 Fix

Date: 2026-08-21

## Incident

After installing the first remote demo kit, Revit 2026 started the WebSocket
worker but never registered Slot 1. The browser correctly reported that the
remote relay was ready and still waiting for the local Revit plugin.

Two screenshots uploaded at 15:34 local time showed:

- Connection settings were already `WebSocket (Cloud)`, used
  `wss://graptolite.ai/api/v1/bridge/ws`, and selected Slot 1.
- The plugin retried every five seconds and reported that the server returned
  HTTP 403 when HTTP 101 was expected.
- The settings page incorrectly described the worker as connected even while
  every handshake was failing.

## Root Cause

The default .NET `ClientWebSocket` handshake did not send a `User-Agent`.
Cloudflare rejected that header-less handshake at the edge with HTTP 403, so
the request never reached the origin Nginx or FastAPI Bridge.

The behavior was reproduced outside Revit with a minimal .NET 8
`ClientWebSocket` client:

- Default client: HTTP 403, no origin Nginx request.
- Client with `User-Agent: RevitMCPPlugin/0.3`: state `Open`, origin Nginx
  recorded HTTP 101, and FastAPI accepted the WebSocket.

This also proves that the slot token was not the cause: the failure happened
before the WebSocket upgrade and before the first-message token handshake.

## Changes

- `WebSocketService` now sends `User-Agent: RevitMCPPlugin/0.3` before
  `ConnectAsync`.
- Replaced `.Wait()` with `GetAwaiter().GetResult()` and logs the base exception
  so HTTP handshake failures are no longer hidden by `AggregateException`.
- Added separate `IsConnected` and `LastConnectionError` state.
- The settings page refreshes once per second and distinguishes connecting,
  connected, reconnecting, and stopped states.
- The initial Ribbon dialog now says the connection was started rather than
  claiming it was already connected.
- Added package revision `2026-08-21-wsfix1` to the installer output and
  documented the Cloudflare fix in the plugin and Demo Kit guides.

## Validation

Passed:

- Minimal .NET 8 `ClientWebSocket` live probe with the new User-Agent: state
  `Open`; Nginx HTTP 101.
- The same .NET probe sent the real first-message token without printing it;
  the server reported Slot 1 `connected`, then returned it to `free` on close.
- RevitMCPPlugin `Release R26` cross-build: 0 warnings, 0 errors.
- RevitMCPCommandSet `Release R26` cross-build: 0 errors, 3 pre-existing
  non-blocking warnings.
- Built DLL string check confirms `RevitMCPPlugin/0.3` and the revised status
  messages are present in the packaged binary.
- All Python tests: 9 passed.
- `git diff --check` and JSON validation.
- Encrypted archive local `7z t` test.
- Real public download returned HTTP 200 and 8,346,049 bytes.
- Downloaded archive SHA-256 matched the source and passed `7z t` with AES
  header encryption.

## Replacement Demo Kit

- Revision: `2026-08-21-wsfix1`.
- URL:
  `https://graptolite.ai/downloads/revit-demo-kit-20260821-wsfix1-69de1944.7z`.
- SHA-256:
  `fa0465dd58276bd2f3ec4b2fefe840a6623556a82e2c624c62b15330c63da460`.
- The archive password and Slot 1 token are intentionally excluded from git.

The installer safely backs up the currently installed plugin. The user must
close Revit, run the WSFix1 installer, restart Revit, click the Ribbon switch
once, and then confirm that the settings page and public Bridge both show Slot
1 connected.

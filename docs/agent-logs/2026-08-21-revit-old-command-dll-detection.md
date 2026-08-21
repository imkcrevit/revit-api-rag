# Agent Change Log: Detect Old Revit Command DLL Deployments

Date: 2026-08-21

## Follow-up Incident

The user uploaded
`Snipaste_2026-08-21_16-44-35.png` after installing a replacement demo kit.
The screenshot still showed the bilingual `Confirm code execution` dialog and
displayed the dynamic `PickObject` code used by `Pick in Revit`.

The backend received the corresponding request at 16:44 local time and Revit
returned `OperationCanceledException: The user aborted the pick operation`.
This proves that the dialog was produced inside the loaded Revit command
assembly rather than by the browser or backend.

## Evidence

- The old WSFix1 `RevitMCPCommandSet.dll` is 280,064 bytes, has SHA-256
  `5f9d3ceb86479ce6c59f7f855a4b5791650a262527c02eea76afd23724887c20`,
  and contains the `Confirm code execution` string.
- The AutoExec command DLL is 279,552 bytes, has SHA-256
  `49ccdce6ba5ca3010a30a6714d6d18cc30d08dbaf638979d355977d17e958f27`,
  and does not contain that string.
- The previously published AutoExec1 archive was downloaded, extracted, and
  confirmed to contain the correct AutoExec DLL. The screenshot therefore
  means that the running Revit process loaded an older installed copy or a
  second active add-in manifest selected another plugin location.

## Installer Hardening

- Bumped the package revision to `2026-08-21-autoxec2`.
- The installer now calculates the SHA-256 of the installed
  `RevitMCPCommandSet.dll` and aborts unless it exactly matches the AutoExec
  build.
- Successful output includes the package revision, installed command DLL hash,
  and remote-code execution state.
- The installer scans both the per-user and machine-wide Revit 2026 Addins
  directories for active manifests that load `revit_mcp_plugin.Core.Application`.
  It warns and prints every path when more than one active manifest exists.
- The demo guide documents the required output and tells the user not to start
  Revit when duplicate manifests are reported.

## AutoExec2 Package

- URL:
  `https://graptolite.ai/downloads/revit-demo-kit-20260821-autoxec2-e0212210.7z`.
- Size: `8,341,921` bytes.
- SHA-256:
  `2aad6ec3d815a26ae8f13aeab2b32ee94d65e4e3886931f3af2b6625590b0958`.
- Public download returned HTTP 200, matched the source checksum, passed the
  AES-encrypted archive test, and extracted successfully.
- The extracted public command DLL matched the required AutoExec SHA-256 and
  contained no execution-confirmation string.
- The archive password and Slot 1 token are intentionally excluded from git.

Windows/Revit verification remains pending. The user must fully close Revit,
run the AutoExec2 installer with `-EnableRemoteCodeExecution`, confirm the three
required output lines, and report any duplicate-manifest warning before
starting Revit.

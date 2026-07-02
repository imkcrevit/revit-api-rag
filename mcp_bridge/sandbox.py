"""
Code Sandbox — static security review for generated C# code.

Blocks dangerous patterns (file I/O, network, process execution, reflection)
before sending code to Revit for execution.

IMPORTANT: This static blacklist is only ONE layer of defense-in-depth. It is
inherently easy to bypass (encoding tricks, reflection chains, aliasing, etc.)
and must NOT be relied upon as the security boundary. The real isolation MUST
live on the Revit plugin side (assembly whitelist + human confirmation +
symbol-level static checks before Roslyn compilation). Treat any match here as
a strong signal to reject, but never assume a "safe" result means the code is
actually safe.
"""
from __future__ import annotations

import re


# Patterns that should NOT appear in generated Revit code
_BLACKLIST: list[tuple[str, str]] = [
    (r"\bSystem\.IO\b", "System.IO (file operations)"),
    (r"\bFile\.(Read|Write|Delete|Copy|Move|Create|Open|Append)", "File I/O operations"),
    (r"\bDirectory\.(Create|Delete|Move|GetFiles)", "Directory operations"),
    (r"\bStreamReader\b|\bStreamWriter\b", "Stream file I/O"),
    (r"\bSystem\.Net\b", "System.Net (network operations)"),
    (r"\bHttpClient\b|\bWebClient\b|\bWebRequest\b", "HTTP/network client"),
    (r"\bSystem\.Diagnostics\.Process\b", "Process execution"),
    (r"\bProcess\.Start\b", "Process.Start"),
    (r"\bAssembly\.Load", "Dynamic assembly loading"),
    (r"\.Assembly\b", "Assembly reflection access"),
    (r"\bActivator\b", "Dynamic type instantiation via Activator"),
    (r"\bType\.GetType\b", "Reflection type lookup"),
    (r"\bSystem\.Reflection\b", "Reflection namespace"),
    (r"\bAppDomain\b", "AppDomain manipulation"),
    (r"\bMarshal\b", "System.Runtime.InteropServices.Marshal"),
    (r"\bGCHandle\b", "GCHandle (unmanaged memory pinning)"),
    (r"\bDllImport\b", "P/Invoke via DllImport"),
    (r"\bdynamic\b", "dynamic keyword (bypasses static analysis)"),
    (r"\bunsafe\b", "unsafe code block"),
    (r"\bSystem\.Threading\b", "System.Threading (thread manipulation)"),
    (r"\bEnvironment\.(Exit|GetEnvironmentVariable|SetEnvironmentVariable)",
     "Environment manipulation"),
    (r"\bRegistry\b", "Windows Registry access"),
]


def review(code: str) -> tuple[bool, list[str]]:
    """
    Review generated C# code for dangerous patterns.

    Returns:
        (safe, warnings) — safe=True if no dangerous patterns found.
    """
    warnings: list[str] = []
    for pattern, description in _BLACKLIST:
        if re.search(pattern, code):
            warnings.append(f"Blocked pattern: {description}")

    return (len(warnings) == 0, warnings)

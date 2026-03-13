"""
MCP Bridge — RAG-driven code generation + Revit execution + tool solidification.

Architecture:
    Cloud (RAG + LLM) → generate C# code
    Local (TCP socket) → send to Revit plugin for execution
    Solidify           → save successful code as reusable named tools
"""

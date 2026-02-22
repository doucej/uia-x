# Quicken Automation Skill

This directory contains reference material for automating **Quicken Classic
Premier** using UIA-X.  It was the original use case for V1
(`mcp_quicken`) and is preserved here as an example of building
application-specific skills on top of the generic substrate.

## Files

| File | Purpose |
|------|---------|
| `AGENT_SKILL_GUIDE.md` | Complete guide for an LLM agent to navigate Quicken's UI |
| `example_calls.json` | Representative tool call payloads & responses |
| `quicken_attach.py` | Helper script to attach to a running Quicken instance |

## How to use

1. Start Quicken Classic Premier.
2. Start the UIA-X server: `python -m server.server`
3. Use `select_window` to attach:
   ```json
   { "process_name": "qw.exe" }
   ```
   or
   ```json
   { "class_name": "QWinFrame" }
   ```
4. Use the UIA tools (`uia_inspect`, `uia_invoke`, etc.) as described in
   the skill guide.

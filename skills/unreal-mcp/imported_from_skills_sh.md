This local skill was adapted from external Unreal agent-skill patterns discovered through skills.sh-compatible sources.

Primary references used:
- `quodsoler/unreal-engine-skills`
  - emphasized audited Unreal API accuracy
  - emphasized skill-based specialization by domain
  - emphasized project-context-first behavior
- `dstn2000/claude-unreal-engine-skill`
  - emphasized zero-assumptions workflow
  - emphasized project structure discovery before code suggestions
  - emphasized project-specific, version-aware Unreal guidance

This local Hermes skill narrows those broad Unreal patterns into a PanicRoom + UnrealMCP specific control skill.

Local specialization applied here:
- target UE plugin path: `D:\PanicRoom\Plugins\UnrealMCP`
- target UnrealMCP repo path: `E:\UnrealMCP`
- transport expectation: Python MCP server over stdio, then TCP `127.0.0.1:13377` to the UE plugin
- expected operations: actor, blueprint, material, AI, editor, advanced UnrealMCP tool families

This file is provenance and guidance only. The executable local behavior lives in `skill.json` and `system_prompt.md`.

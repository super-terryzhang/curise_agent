"""Agent platform — Phase 6 of the v3 rebuild.

Subpackages:
- `engine/`: ReAct loop, tool registry, tool context, tracer (no `db.*`)
- `llm/`: provider abstraction + Gemini/Kimi/OpenAI/DeepSeek implementations
- `storage/`: persistence layer (whitelisted to use `db.*` per ADR-0006)
- `memory/`: cross-session agent memory (whitelisted)
- `tools/`: generic + business tools (call domain services, never `db.*`)
- `middlewares/`: 11 middlewares (memory / guardrail / loop_detection / ...)
- `prompts/`: prompt builder + layer composition
- `skills/`: markdown skill catalog (content-only)
"""

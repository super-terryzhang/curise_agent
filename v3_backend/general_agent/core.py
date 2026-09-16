"""Core Agent — single entry, while-loop, tool calling.

Design:
  - One entry point: `run(task) -> str`.
  - Tools come from `agent.toolkit` (imported for side-effects → all built-ins
    auto-register in the singleton `REGISTRY`).
  - Each Agent gets a `ToolView` scoped by its enabled toolsets, plus a
    `ToolContext` with workspace/memory/allow_bash.  Handlers that declared
    `ctx: ToolContext` receive it at dispatch time.
  - Loop safeguards: max_steps, duplicate (tool, args) detection,
    no-progress stop, explicit `finish` terminator tool, paired
    tool_call/tool_result integrity.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .approval import ApprovalState, Decision, Mode
from .budget import Budget, BudgetExceeded
from .context import Context
from .context_engine import CompressingEngine, ContextEngine, TrimEngine
from .errors import NonRetryableError
from .interrupt import CancelToken
from .llm import LLM, LLMConfig
from .memory import Memory
from .session_store import SessionStore
from .skills import SkillLoader
from .tools import REGISTRY, ToolContext, ToolView
from .workspace import Workspace

# Side-effect import: registers all built-in tools into REGISTRY.
from . import toolkit as _toolkit
from .toolkit import load_external_tools
from .toolkit.toolsets import resolve_many

_ = _toolkit  # keep import alive for side-effects


DEFAULT_SYSTEM = """You are a capable autonomous agent solving a user task end-to-end.

Operating protocol:
1. THINK briefly about what the task needs. If information is missing or time-sensitive, CALL `web_search` before guessing.
2. ACT using tools. Prefer many small tool calls over one giant one. Read results carefully.
3. VERIFY: before finishing, confirm your answer is actually grounded (facts from search/fetch/files), not hallucinated.
4. FINISH: when the task is fully answered, call the `finish` tool with the complete user-facing answer.

Rules:
- Do not repeat the exact same tool call twice. If a tool returns an error, change your approach.
- Do not end the task by replying in plain text — always use the `finish` tool for the final answer.
- Do not invent URLs; only fetch URLs returned by `web_search` or given by the user.
- Keep file writes inside the workspace. Use `write_file` to save long artifacts (reports, code).
- If the task is ambiguous, make a reasonable assumption, state it in the final answer, and proceed."""


@dataclass
class Step:
    kind: str               # "tool" | "final" | "error"
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    result: str = ""


@dataclass
class AgentConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    max_steps: int = 25
    workspace: str = "./workspace"
    # Skills — accepts a single path OR a list of paths. All roots merged.
    # Back-compat: singular `skills_root` still works.
    skills_root: str | list[str] | None = "./skills"
    system_prompt: str = DEFAULT_SYSTEM
    force_first_tool: bool = False
    allow_bash: bool = True
    no_progress_limit: int = 3
    verbose: bool = True
    # Tool management — either a list of toolset names (resolved via TOOLSETS)
    # or literal tool names. Default = everything registered.
    toolsets: list[str] = field(default_factory=lambda: ["all"])
    # Memory file path; defaults to <workspace>/memory.md.
    memory_path: str | None = None
    # External plugin directories. Each `.py` in these dirs is imported at
    # Agent-build time (NOT package load) so its @tool decorators fire.
    extra_tool_dirs: list[str] = field(default_factory=list)
    # Bash approval gate — "manual" prompts on dangerous patterns,
    # "off" = YOLO, "deny_all" = read-only agent.
    approval_mode: str = "manual"
    # Optional callback: (command, description, mode) -> "allow_once" | "allow_session" | "deny".
    # Default is a CLI blocking prompt with timeout.
    approval_callback: Any = None

    # Context-compaction strategy.  "trim" = drop middle turns (no LLM cost);
    # "compress" = LLM-summarize middle via structured template (hermes-style).
    context_engine: str = "trim"
    # Fires compaction when estimated tokens exceed this.
    context_threshold_tokens: int = 15_000
    # For "compress" only — the most recent ~N tokens that survive verbatim.
    context_tail_token_budget: int = 6_000

    # Optional SQLite session store.  If set, Agent persists every message
    # so the conversation can be resumed later by `session_id`.
    session_db: str | None = None
    session_id: str | None = None       # resume if given, else new
    session_title: str = ""

    # Alternative: inject a pre-built store instance (must duck-type SessionStore:
    # create / get / resolve_prefix / load / append / replace_all / end_session).
    # Takes precedence over `session_db`. Used by host applications that want
    # to persist into their own database (Postgres, etc.) instead of SQLite.
    session_store: Any | None = None

    # Optional Memory backend. If None, Agent constructs the default
    # markdown-file Memory under `<workspace>/memory.md`. Host applications
    # supply a DB-backed implementation that duck-types `agent.memory.Memory`
    # (load / append / search). Takes precedence over `memory_path`.
    memory: Any | None = None

    # Hard budget caps (0 / "" = no cap for that dimension).
    max_total_tokens: int = 0
    max_usd: float = 0.0

    # MCP (Model Context Protocol) server specs — each spawned as a stdio
    # subprocess at Agent init.  Format: {name: {command, args, env?}}
    mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Clarify callback: (question, choices_or_None) -> user_answer_str.
    # If None, the clarify tool prompts on the CLI.
    clarify_callback: Any = None

    # Streaming callbacks — when set, the LLM uses stream=True internally
    # and fires per-token hooks (text/reasoning/tool_call_started/
    # tool_args_delta/assistant_message_done). Returned message shape is
    # unchanged; only the *delivery* changes. See `agent.llm.StreamCallbacks`.
    #
    # Tool dispatch results (after a tool runs) flow through the existing
    # `on_step` callback with kind="tool", so v3 chat backends can pipe
    # both streaming generation and tool execution into a single SSE feed.
    stream_callbacks: Any = None


class _SyntheticToolCall:
    """Stand-in for an OpenAI ChatCompletionMessageToolCall when we have
    to split a malformed Gemini parallel call into pieces. Same attribute
    shape so the dispatch loop doesn't have to know it's synthetic.

    `extra_content` carries provider-specific replay tokens — for Gemini
    3 this is the `{"google": {"thought_signature": ...}}` that the API
    REQUIRES on every function call in the next-turn history. Empirically
    (prod 2026-05-19 streaming concat-bug repro) Gemini accepts the same
    signature duplicated across N splits but rejects splits without it
    ("Function call is missing a thought_signature in functionCall
    parts, position N"). Semantically correct: the model emitted those
    N calls as one reasoning step, so they SHARE one signature.
    """

    __slots__ = ("id", "type", "function", "extra_content")

    def __init__(
        self,
        call_id: str,
        name: str,
        arguments: str,
        extra_content: dict | None = None,
    ) -> None:
        self.id = call_id
        self.type = "function"

        class _Fn:
            pass

        fn = _Fn()
        fn.name = name
        fn.arguments = arguments
        self.function = fn
        if extra_content:
            self.extra_content = extra_content


def _tool_call_to_dict(tc: Any) -> dict[str, Any]:
    """Serialize a tool_call object back to its OpenAI-compat dict form.

    Preserves provider-specific opaque fields like Gemini 3's
    `extra_content.google.thought_signature` — the API REQUIRES that
    signature on every replay, and stripping it produces a hard 400:
    "Function call is missing a thought_signature in functionCall parts."
    """
    d: dict[str, Any] = {
        "id": tc.id,
        "type": getattr(tc, "type", "function") or "function",
        "function": {
            "name": tc.function.name,
            "arguments": tc.function.arguments,
        },
    }
    extras = getattr(tc, "extra_content", None)
    if extras:
        d["extra_content"] = extras
    return d


def _split_malformed_tool_calls(tool_calls: list[Any], view: Any) -> list[Any]:
    """Walk Gemini's tool_calls, expanding the OpenAI-compat parallel bug.

    Symptom: a single tool_call whose `name` is two registered tool names
    concatenated and whose `arguments` is two JSON objects concatenated:

        name="query_dbquery_db"
        arguments='{"sql":"..."}{"sql":"..."}'

    Detection requires the tool registry (`view`) to confirm the name is
    NOT a real tool but DOES split into a sequence of real tool names.
    """
    if not tool_calls:
        return tool_calls
    known = set(view.names()) if view is not None else set()
    out: list[Any] = []
    for tc in tool_calls:
        name = tc.function.name
        raw_args = tc.function.arguments or ""
        if name in known:
            out.append(tc)
            continue
        split_names = _split_concatenated_names(name, known)
        split_args = _split_concatenated_json(raw_args)
        if split_names and len(split_names) == len(split_args) and len(split_names) > 1:
            base_id = getattr(tc, "id", None) or "split"
            # Preserve Gemini 3's `thought_signature` (and any future
            # provider-specific replay token) by duplicating the
            # original tool_call's `extra_content` onto every synthetic
            # split. The original was ONE call carrying ONE signature;
            # the splits all share that single reasoning step, so
            # logically the signature applies to each. Empirically
            # Gemini accepts the duplication; splits-without-signature
            # are rejected with HTTP 400.
            extras = getattr(tc, "extra_content", None)
            for i, (n, a) in enumerate(zip(split_names, split_args)):
                out.append(_SyntheticToolCall(f"{base_id}-{i}", n, a, extras))
        else:
            # Not the known bug — pass through, the dispatch loop will
            # surface a proper "tool not enabled" error.
            out.append(tc)
    return out


def _split_concatenated_names(name: str, known: set[str]) -> list[str] | None:
    """Try to split `name` into a sequence of strings each in `known`."""
    if not name or not known:
        return None
    pieces: list[str] = []
    cursor = 0
    n = len(name)
    while cursor < n:
        # Greedy longest-match against `known`.
        best: str | None = None
        for k in known:
            end = cursor + len(k)
            if end <= n and name[cursor:end] == k and (best is None or len(k) > len(best)):
                best = k
        if best is None:
            return None
        pieces.append(best)
        cursor += len(best)
    return pieces if len(pieces) >= 2 else None


def _split_concatenated_json(raw: str) -> list[str]:
    """Split `{"a":1}{"b":2}` into [`{"a":1}`, `{"b":2}`].

    Walks the string respecting string literals + escape sequences so a
    closing brace inside a value doesn't trigger a false split.
    """
    if not raw or "}{" not in raw:
        return [raw]
    out: list[str] = []
    depth = 0
    in_str = False
    escape = False
    start = 0
    for i, ch in enumerate(raw):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append(raw[start : i + 1])
                start = i + 1
    if start < len(raw):
        out.append(raw[start:])
    return [s for s in out if s.strip()]


class Agent:
    """Single-entry agent.

    Customization:
        - `config.toolsets=["web","files","control"]` — scope available tools
        - `config.system_prompt="..."`                — override persona
        - `config.skills_root=None`                   — disable skill loading
        - `view=REGISTRY.view(names={...})`           — custom tool set entirely
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        view: ToolView | None = None,
        on_step: Callable[[Step], None] | None = None,
        workspace: Workspace | None = None,
    ) -> None:
        self.config = config or AgentConfig()

        # Budget — wired into LLM.on_usage so every response usage is accrued.
        self.budget = Budget(
            model=self.config.llm.model,
            max_total_tokens=self.config.max_total_tokens,
            max_usd=self.config.max_usd,
        )
        self.llm = LLM(self.config.llm, on_usage=self.budget.update_from_usage)

        # Cancel token — checked at each loop boundary + exposed to tools via ctx.
        self.cancel_token = CancelToken()

        # Load external tool plugins BEFORE building the view, so their
        # @tool decorators have registered by the time we resolve toolsets.
        if self.config.extra_tool_dirs:
            load_external_tools(
                self.config.extra_tool_dirs,
                quiet=not self.config.verbose,
            )

        # Connect MCP servers (also BEFORE view construction).
        if self.config.mcp_servers:
            try:
                from .mcp_client import register_mcp_servers
                register_mcp_servers(
                    self.config.mcp_servers,
                    quiet=not self.config.verbose,
                )
            except Exception as e:
                if self.config.verbose:
                    print(f"[mcp] setup failed: {e}")

        # Resolve toolsets → a set of tool names → ToolView over REGISTRY.
        if view is None:
            resolved = resolve_many(self.config.toolsets)
            self.view = REGISTRY.view(resolved)
        else:
            self.view = view

        # Build Workspace (owns the directory + subagent carving + manifest).
        # If a pre-built workspace is passed in (e.g. from delegate_task), use
        # it so the child inherits its parent's carved subdir.
        self.workspace: Workspace = workspace or Workspace(
            self.config.workspace,
            manifest_meta={
                "model": self.config.llm.model,
                "toolsets": list(self.config.toolsets),
            },
        )
        mem = (
            self.config.memory
            if self.config.memory is not None
            else Memory(self.config.memory_path or (self.workspace.root / "memory.md"))
        )
        self.approval = ApprovalState(
            mode=self.config.approval_mode,  # type: ignore[arg-type]
            callback=self.config.approval_callback,
        )
        self.ctx = ToolContext(
            workspace=self.workspace.root,
            memory=mem,
            allow_bash=self.config.allow_bash,
            agent=self,
            approval=self.approval,
            extras={
                "workspace_obj": self.workspace,
                "cancel_token": self.cancel_token,
                "clarify_callback": self.config.clarify_callback,
            },
        )

        # Delegation depth. Parent=0; children bump this when spawned.
        self.depth: int = 0

        # Optional persistence — SQLite-backed session store, OR an injected
        # store implementation (host app's Postgres backend, etc.).
        self.store: Any = None
        self.session_id: str | None = None
        if self.config.session_store is not None:
            # Injected backend — assumed to duck-type SessionStore's surface.
            self.store = self.config.session_store
        elif self.config.session_db:
            self.store = SessionStore(self.config.session_db)
        if self.store is not None:
            if self.config.session_id:
                resolved = (
                    self.store.get(self.config.session_id)
                    or (
                        self.store.get(self.store.resolve_prefix(self.config.session_id) or "")
                        if self.config.session_id
                        else None
                    )
                )
                if resolved:
                    self.session_id = resolved["id"]
                # else: session_id passed but not found → create fresh below.
            if self.session_id is None:
                self.session_id = self.store.create(
                    model=self.config.llm.model,
                    system_prompt=self.config.system_prompt,
                    title=self.config.session_title,
                )

        self.skills = (
            SkillLoader(self.config.skills_root) if self.config.skills_root else None
        )
        # SkillLoader itself accepts str | Path | list — passthrough works for all.
        self.on_step = on_step or self._default_step_hook
        self.trace: list[Step] = []

    # ---------- cancellation ----------

    def cancel(self, reason: str = "cancelled by caller") -> None:
        """Request a graceful stop. Agent.run() will return on next boundary."""
        self.cancel_token.set(reason)

    # ---------- public entry ----------

    def run(self, task: str) -> str:
        system_prompt = self._build_system_prompt(task)
        engine = self._build_context_engine()
        ctx = Context(system_prompt, engine=engine)

        # Resume persisted history if we're on an existing session.
        resumed_msg_count = 0
        if self.store and self.session_id:
            prior = self.store.load(self.session_id)
            if prior:
                ctx.messages.extend(prior)
                resumed_msg_count = len(prior)

        ctx.add_user(task)
        if self.store and self.session_id:
            self.store.append(self.session_id, {"role": "user", "content": task})

        self.trace = []
        self._ctx_for_persist = ctx  # used by _persist_turn

        seen_calls: set[tuple[str, str]] = set()
        no_progress = 0

        for step_idx in range(self.config.max_steps):
            # Boundary check #1 — cancel requested?
            if self.cancel_token.is_set():
                return self._on_stop("cancelled", self.cancel_token.reason, ctx)

            # Boundary check #2 — budget exhausted?
            ok, reason = self.budget.check()
            if not ok:
                return self._on_stop("budget", reason, ctx)

            tool_choice: Any = "auto"
            if self.config.force_first_tool and step_idx == 0:
                tool_choice = "required"

            try:
                msg = self.llm.complete(
                    messages=ctx.for_api(),
                    tools=self.view.definitions(),
                    tool_choice=tool_choice,
                    stream_callbacks=self.config.stream_callbacks,
                )
            except NonRetryableError as e:
                return self._on_stop("api_error", str(e), ctx)
            except Exception as e:
                # Retryable but we ran out of retries — surface cleanly.
                return self._on_stop("api_error", f"{type(e).__name__}: {e}", ctx)
            ctx.add_assistant(msg)
            self._persist_last(ctx)

            # If the engine just compacted this turn, rewrite persisted
            # history so resume sees the compacted shape, not the raw log.
            if engine.compression_count > getattr(self, "_last_compression_seen", 0):
                self._last_compression_seen = engine.compression_count
                if self.store and self.session_id:
                    self.store.replace_all(self.session_id, ctx.messages)

            original_tool_calls = getattr(msg, "tool_calls", None) or []
            # Defensive splitter for Gemini's OpenAI-compat parallel-tool
            # bug: occasionally Gemini emits a single tool_call whose
            # `name` is two tool names concatenated (`query_dbquery_db`)
            # and whose `arguments` is two JSON objects concatenated
            # (`{"sql":"..."}{"sql":"..."}`). Detect & split before dispatch.
            tool_calls = _split_malformed_tool_calls(original_tool_calls, self.view)
            # If we did split, the assistant message we just appended to
            # history references the original (buggy) single tool_call.
            # The follow-up `add_tool_result` calls below will use the
            # synthetic ids — Gemini chokes on assistant.tool_calls /
            # tool.tool_call_id mismatch and refuses to continue. Patch
            # the persisted assistant entry so the ids line up.
            if tool_calls is not original_tool_calls and ctx.messages:
                last = ctx.messages[-1]
                if last.get("role") == "assistant":
                    last["tool_calls"] = [_tool_call_to_dict(tc) for tc in tool_calls]
                    # Sync DB: `_persist_last` above wrote the pre-split
                    # concatenated tool_call (single id, weird joined
                    # name). Tool result rows below will use the synthetic
                    # split ids, so the persisted assistant→tool linkage
                    # breaks: frontend's `stepById.get(callId)` misses and
                    # the UI shows an orphan "running" card forever (prod
                    # 2026-05-19 repro). Rewrite the persisted row with
                    # the split shape so the call_ids line up on reload.
                    if self.store and self.session_id and hasattr(
                        self.store, "replace_last"
                    ):
                        try:
                            self.store.replace_last(self.session_id, last)
                        except Exception:
                            # Non-fatal: live SSE stream already reflects
                            # the right state via add_tool_result calls;
                            # only the persisted view is at risk.
                            pass
            if not tool_calls:
                content = (getattr(msg, "content", "") or "").strip()
                if content:
                    step = Step(kind="final", result=content)
                    self.trace.append(step)
                    self.on_step(step)
                    return content
                ctx.add_user(
                    "You returned an empty message with no tool calls. "
                    "Either call a tool or call `finish` with the final answer."
                )
                no_progress += 1
                if no_progress >= self.config.no_progress_limit:
                    return "[agent stopped: no progress]"
                continue

            finished_answer: str | None = None
            new_work = False
            for tc in tool_calls:
                name = tc.function.name
                raw_args = tc.function.arguments or "{}"
                try:
                    args = json.loads(raw_args)
                    if not isinstance(args, dict):
                        raise ValueError("tool arguments must be a JSON object")
                except Exception as e:
                    hint = ""
                    if "unterminated" in str(e).lower() or "unexpected end" in str(e).lower():
                        hint = (
                            " HINT: your tool-call arguments were truncated. "
                            "Split large content (e.g. write_file) into multiple smaller calls."
                        )
                    result = (
                        f"[tool-error] invalid JSON args: {e}.{hint} "
                        f"Raw head: {raw_args[:200]}"
                    )
                    ctx.add_tool_result(tc.id, name, result)
                    step = Step(kind="error", name=name, args={}, result=result)
                    self.trace.append(step)
                    self.on_step(step)
                    continue

                call_key = (name, json.dumps(args, sort_keys=True, default=str))
                dup = call_key in seen_calls
                seen_calls.add(call_key)

                tool = self.view.get(name)
                if tool is None:
                    result = f"[tool-error] tool not enabled in this agent: {name}"
                elif dup:
                    result = (
                        f"[tool-error] duplicate call to {name} with identical args. "
                        "Change your approach instead of retrying."
                    )
                else:
                    result = self.view.dispatch(name, args, ctx=self.ctx)
                    new_work = True

                ctx.add_tool_result(tc.id, name, result)
                self._persist_last(ctx)
                step = Step(kind="tool", name=name, args=args, result=result)
                self.trace.append(step)
                self.on_step(step)

                if tool is not None and tool.is_terminator and not dup:
                    finished_answer = result

            if finished_answer is not None:
                final = Step(kind="final", result=finished_answer)
                self.trace.append(final)
                self.on_step(final)
                return finished_answer

            if not new_work:
                no_progress += 1
                if no_progress >= self.config.no_progress_limit:
                    return "[agent stopped: repeated calls with no new work]"
            else:
                no_progress = 0

        return self._fallback_final(ctx)

    # ---------- helpers ----------

    def _on_stop(self, kind: str, reason: str, ctx: Context) -> str:
        """Called when we bail early (cancel / budget / fatal API error).

        Persists whatever state we have and returns a prefixed message so the
        caller can distinguish from a normal finish.
        """
        if self.store and self.session_id:
            try:
                self.store.end_session(self.session_id)
            except Exception:
                pass
        step = Step(kind="final", result=f"[{kind}] {reason}")
        self.trace.append(step)
        self.on_step(step)
        return f"[{kind}] {reason}"

    def _build_context_engine(self) -> ContextEngine:
        kind = (self.config.context_engine or "trim").lower()
        if kind == "trim":
            return TrimEngine(threshold_tokens=self.config.context_threshold_tokens)
        if kind in ("compress", "compressor", "compressing"):
            # Summarizer: call our LLM with a minimal single-user-message
            # prompt and max_tokens set per call.  Plain-text output.
            llm = self.llm
            def _summarize(prompt: str, max_tokens: int) -> str:
                resp = llm.complete(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                )
                return (getattr(resp, "content", None) or "").strip()

            return CompressingEngine(
                summarizer=_summarize,
                threshold_tokens=self.config.context_threshold_tokens,
                tail_token_budget=self.config.context_tail_token_budget,
            )
        raise ValueError(f"unknown context_engine: {self.config.context_engine!r}")

    def _persist_last(self, ctx: Context) -> None:
        """Persist the most recently added message (if session-backed)."""
        if self.store and self.session_id and ctx.messages:
            self.store.append(self.session_id, ctx.messages[-1])

    def _build_system_prompt(self, task: str) -> str:
        parts = [self.config.system_prompt]
        describe = self.view.describe()
        if describe:
            parts.append("\n# Available tools (grouped by toolset)")
            for ts_name in sorted(describe):
                tool_names = describe[ts_name]
                parts.append(f"  [{ts_name}] " + ", ".join(tool_names))
        parts.append(f"\nWorkspace directory: {self.ctx.workspace}")
        if self.skills:
            # Progressive disclosure (Anthropic Skills pattern):
            # inject the skill *catalog* (name + description) only. The
            # full SKILL.md body is loaded lazily via the `load_skill`
            # tool when the LLM decides a skill applies. This keeps token
            # cost flat regardless of how many skills are installed.
            catalog = self.skills.list_metadata()
            if catalog:
                parts.append("\n# Available skills (load body via `load_skill(name)`)")
                parts.append(
                    "Each entry is a procedural-knowledge bundle. Read the description, "
                    "decide if it fits the user's task, then call `load_skill(name)` to "
                    "pull its full instructions into context. Do not invent skill names — "
                    "use only the `name` listed below."
                )
                for s in catalog:
                    parts.append(f"- {s['name']}: {s['description']}")
        return "\n".join(parts)

    def _fallback_final(self, ctx: Context) -> str:
        ctx.add_user(
            "You have hit the step budget. Reply with the best possible final "
            "answer you can give NOW based on what you've already gathered. "
            "Call the `finish` tool with that answer."
        )
        msg = self.llm.complete(
            messages=ctx.for_api(),
            tools=self.view.definitions(),
            tool_choice={"type": "function", "function": {"name": "finish"}},
        )
        tcs = getattr(msg, "tool_calls", None) or []
        if tcs:
            try:
                args = json.loads(tcs[0].function.arguments or "{}")
                return args.get("answer", "") or "(no answer)"
            except Exception:
                pass
        return (getattr(msg, "content", "") or "[max_steps reached]").strip()

    def _default_step_hook(self, step: Step) -> None:
        if not self.config.verbose:
            return
        if step.kind == "tool":
            tool = self.view.get(step.name)
            emoji = tool.emoji if tool else "·"
            preview = step.result[:200].replace("\n", " ")
            args_preview = json.dumps(step.args, ensure_ascii=False)[:120]
            print(f"  {emoji} {step.name}({args_preview}) -> {preview}")
        elif step.kind == "final":
            print(f"  ✓ final ({len(step.result)} chars)")
        elif step.kind == "error":
            print(f"  ✗ {step.name} error: {step.result[:200]}")

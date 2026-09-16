"""Section 10 — Agent reliability fixtures + reporting.

测试目标：
    系统性度量 v3 chat agent 在复杂、多工具、长链路任务上的**可靠性**，
    而不是单次「能不能跑通」。借鉴 τ-bench 的 pass^k 度量（同一任务跑
    k 次，全部通过才算 pass）—— 单次成功不足以说明 agent 真的稳。

    每个 bucket 都按 (provider × trial_id) 双轴 parametrize，pytest
    把它们当成独立 test 跑，最后 pytest_terminal_summary hook 汇总成
    pass^k 报告。

为什么必须有：
    现有 LLM eval（section 4 / 6）只测单轮单工具的正确性。**长链路、
    跨轮上下文、HITL 链、错误恢复**这些"agent 整体能力"维度完全没覆
    盖。Prod 已经撞到过：tools 单测全绿、agent 真用起来还是会卡死
    (例：v3-chat-store 切到 Kimi 后整段 silence 没人发现是因为只跑了
    Gemini eval)。

如何运行：
    RUN_AGENT_RELIABILITY=1 PYTEST_RUN_SLOW=1 \\
        GOOGLE_API_KEY=... MOONSHOT_API_KEY=... \\
        pytest test_v2/section_10_agent_reliability/ -v -s

    报告会同时打印到 stdout 和写到 reliability_report.md。
"""

from __future__ import annotations

import os
import time
from collections.abc import Generator
from typing import Any

import pytest

# ─── Skip guard ───────────────────────────────────────────────


_ENABLE_RELIABILITY = (
    os.getenv("RUN_AGENT_RELIABILITY")
    and os.getenv("PYTEST_RUN_SLOW")
    and os.getenv("GOOGLE_API_KEY")
)


skip_reliability = pytest.mark.skipif(
    not _ENABLE_RELIABILITY,
    reason=(
        "agent reliability suite (real LLM, slow); "
        "requires RUN_AGENT_RELIABILITY=1 PYTEST_RUN_SLOW=1 GOOGLE_API_KEY=..."
    ),
)


# Provider catalogue we benchmark. Kimi requires MOONSHOT_API_KEY in env;
# if absent the Kimi parametrizations skip individually rather than fail
# the whole suite — so a developer with only a GOOGLE_API_KEY can still
# get the Gemini half of the report.
PROVIDERS: list[str] = ["gemini-2.5-flash", "kimi-k2.6"]


def _provider_available(model: str) -> bool:
    if model.startswith("gemini-"):
        return bool(os.getenv("GOOGLE_API_KEY"))
    if model.startswith(("kimi-", "moonshot-")):
        return bool(os.getenv("MOONSHOT_API_KEY"))
    return False


# Trials per (bucket, provider). 3 is the sweet spot — enough to surface
# flake (pass^3 < pass^1 is the canonical signal), small enough to stay
# under 10 min wall-clock for the full suite.
TRIALS: int = 3


# ─── Per-test infra: real LLM allowed ─────────────────────────


@pytest.fixture
def real_llm_enabled(monkeypatch):
    """Override the global `_disable_llm_extractor` so the chat agent can
    actually call Gemini / Moonshot. The autouse default in `test_v2/conftest.py`
    blanks `GOOGLE_API_KEY` — we put it back here.
    """
    from infrastructure.config import settings

    if not settings.GOOGLE_API_KEY:
        monkeypatch.setattr(
            "infrastructure.config.settings.GOOGLE_API_KEY",
            os.environ.get("GOOGLE_API_KEY", ""),
        )
    monkeypatch.setattr(
        "domains.orders._llm_extractor.settings.GOOGLE_API_KEY",
        os.environ.get("GOOGLE_API_KEY", ""),
    )
    yield


# ─── Trace capture ────────────────────────────────────────────


class AgentRun:
    """Captured result of one `agent.run()` call.

    Carries everything a reliability assertion needs:
    - `answer`: final string returned by the agent
    - `tool_names`: ordered list of tool names invoked
    - `tool_steps`: full Step dicts (name, args, result) for deep checks
    - `step_count`: total trace length (proxy for "how many turns")
    - `elapsed_seconds`: wall-clock duration
    """

    __slots__ = ("answer", "tool_names", "tool_steps", "step_count", "elapsed_seconds")

    def __init__(
        self,
        answer: str,
        tool_names: list[str],
        tool_steps: list[dict[str, Any]],
        step_count: int,
        elapsed_seconds: float,
    ) -> None:
        self.answer = answer
        self.tool_names = tool_names
        self.tool_steps = tool_steps
        self.step_count = step_count
        self.elapsed_seconds = elapsed_seconds

    def has_tool(self, name: str) -> bool:
        return name in self.tool_names

    def has_any_tool(self, names: list[str]) -> bool:
        return any(t in self.tool_names for t in names)


def run_real_agent(
    db: Any,
    user_id: int,
    text: str,
    *,
    model: str,
    session_id: str | None = None,
    user_role: str = "superadmin",
) -> tuple[AgentRun, Any]:
    """Run a real chat agent turn and capture trace + answer.

    Returns the AgentRun + the Agent instance itself (so callers that
    need the session_id for a follow-up turn can grab `agent.session_id`).
    """
    from agent.runtime import create_v3_chat_agent

    started = time.perf_counter()
    agent = create_v3_chat_agent(
        db=db,
        user_id=user_id,
        user_role=user_role,
        session_id=session_id,
        model=model,
    )
    answer = agent.run(text)
    elapsed = time.perf_counter() - started

    tool_names: list[str] = []
    tool_steps: list[dict[str, Any]] = []
    for s in agent.trace:
        if getattr(s, "kind", None) == "tool":
            tool_names.append(getattr(s, "name", ""))
            tool_steps.append(
                {
                    "name": getattr(s, "name", ""),
                    "args": getattr(s, "args", None) or {},
                    "result": getattr(s, "result", "") or "",
                }
            )

    return (
        AgentRun(
            answer=answer or "",
            tool_names=tool_names,
            tool_steps=tool_steps,
            step_count=len(agent.trace),
            elapsed_seconds=elapsed,
        ),
        agent,
    )


# ─── Reliability report aggregation ───────────────────────────

# Module-level dict updated by every test that records a trial. Keyed
# by (bucket_name, provider) → list of {trial, passed, steps, seconds,
# tool_names, error?}. Read by the terminal-summary hook below.

_REPORT: dict[tuple[str, str], list[dict[str, Any]]] = {}


def record_trial(
    bucket: str,
    provider: str,
    trial: int,
    *,
    passed: bool,
    run: AgentRun | None = None,
    error: str | None = None,
) -> None:
    """Append one trial outcome to the in-process report. Called by each
    bucket's test body so the final hook can compute pass^k."""
    entry: dict[str, Any] = {"trial": trial, "passed": passed}
    if run is not None:
        entry["steps"] = run.step_count
        entry["seconds"] = round(run.elapsed_seconds, 2)
        entry["tools"] = run.tool_names
        entry["answer_preview"] = (run.answer or "")[:120]
    if error is not None:
        entry["error"] = error
    _REPORT.setdefault((bucket, provider), []).append(entry)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    """Render the pass^k table to stdout + reliability_report.md.

    We deliberately compute pass^1 (single best trial) AND pass^k (all
    trials passed) — pass^k is the τ-bench-style reliability metric, but
    pass^1 helps distinguish "capability ceiling" (model can never do it)
    from "consistency floor" (model can do it but flaps)."""
    if not _REPORT:
        return

    lines: list[str] = []
    lines.append("# Agent Reliability Report")
    lines.append("")
    lines.append(f"Trials per (bucket, provider): **{TRIALS}**")
    lines.append("")
    lines.append("| Bucket | Provider | pass^1 | pass^k | avg_steps | avg_sec |")
    lines.append("|---|---|---|---|---|---|")

    for (bucket, provider), trials in sorted(_REPORT.items()):
        passes = sum(1 for t in trials if t["passed"])
        n = len(trials)
        pass_1 = f"{passes}/{n}"
        # pass^k: 1 if all passed, 0 otherwise
        pass_k = "✓" if passes == n else "✗"
        if any("steps" in t for t in trials):
            avg_steps = round(
                sum(t.get("steps", 0) for t in trials) / max(n, 1), 1
            )
            avg_sec = round(
                sum(t.get("seconds", 0) for t in trials) / max(n, 1), 1
            )
        else:
            avg_steps = "-"
            avg_sec = "-"
        lines.append(
            f"| {bucket} | {provider} | {pass_1} | {pass_k} | {avg_steps} | {avg_sec} |"
        )

    # Per-trial detail
    lines.append("")
    lines.append("## Per-trial detail")
    lines.append("")
    for (bucket, provider), trials in sorted(_REPORT.items()):
        lines.append(f"### {bucket} × {provider}")
        for t in trials:
            status = "✓" if t["passed"] else "✗"
            tools = ",".join(t.get("tools", []))[:80]
            err = t.get("error", "")
            ans = t.get("answer_preview", "")
            lines.append(
                f"- trial {t['trial']}: {status} | steps={t.get('steps','-')} | "
                f"sec={t.get('seconds','-')} | tools=[{tools}] "
                f"| ans={ans!r}"
                + (f" | err={err}" if err else "")
            )
        lines.append("")

    report = "\n".join(lines)
    terminalreporter.write_sep("=", "AGENT RELIABILITY REPORT")
    terminalreporter.write_line(report)

    # Persist to file for later analysis
    report_path = "reliability_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    terminalreporter.write_line(f"\nReport saved to: {report_path}")


# ─── Provider gating helper for parametrize ───────────────────


def model_param(model: str) -> Any:
    """Return a `pytest.param` that auto-skips when the provider's API
    key is missing. Lets a developer with only GOOGLE_API_KEY run the
    Gemini half without the Kimi tests failing."""
    if _provider_available(model):
        return pytest.param(model, id=model)
    return pytest.param(
        model,
        id=model,
        marks=pytest.mark.skip(reason=f"no API key for {model}"),
    )


# Parametrize value tuples — import these in each bucket file.
PROVIDER_PARAMS = [model_param(m) for m in PROVIDERS]
TRIAL_PARAMS = list(range(TRIALS))

"""general-agent — autonomous agent runtime.

Public API. Importing from `agent` (or the installed package name
`general_agent`) is the supported entry point. Submodule imports work but
are not promised to be stable across releases.

Reference: see `docs/GUIDE.md` for the architecture and embedding patterns.
"""

from .approval import ApprovalState
from .budget import Budget, BudgetExceeded
from .context_engine import CompressingEngine, ContextEngine, TrimEngine
from .core import Agent, AgentConfig, Step
from .errors import (
    ClassifiedError,
    FailoverReason,
    NonRetryableError,
    classify_api_error,
)
from .interrupt import CancelToken, CancelledError, install_sigint_handler
from .llm import LLM, LLMConfig, StreamCallbacks
from .memory import Memory
from .session_store import SessionStore
from .skills import SkillLoader
from .tools import REGISTRY, ToolContext, ToolRegistry, ToolView, tool
from .toolkit import TOOLSETS, list_toolsets, resolve_toolset
from .workspace import Workspace, temp_workspace

__all__ = [
    # Core
    "Agent",
    "AgentConfig",
    "Step",
    # Tools
    "REGISTRY",
    "ToolContext",
    "ToolView",
    "ToolRegistry",
    "tool",
    "TOOLSETS",
    "resolve_toolset",
    "list_toolsets",
    # LLM
    "LLM",
    "LLMConfig",
    "StreamCallbacks",
    # Context management
    "ContextEngine",
    "TrimEngine",
    "CompressingEngine",
    # Persistence + state
    "SessionStore",
    "Memory",
    "Workspace",
    "temp_workspace",
    "ApprovalState",
    # Skills
    "SkillLoader",
    # Budget
    "Budget",
    "BudgetExceeded",
    # Errors
    "ClassifiedError",
    "FailoverReason",
    "NonRetryableError",
    "classify_api_error",
    # Interrupt
    "CancelToken",
    "CancelledError",
    "install_sigint_handler",
]

__version__ = "0.1.0"

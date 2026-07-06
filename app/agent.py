# ruff: noqa
import os
import re
import logging
import json
from typing import Any, AsyncGenerator
from pydantic import BaseModel, Field

from google.adk.agents.context import Context
from google.adk.agents import Agent
from google.adk import Workflow
from google.adk.workflow import Edge, START, node
from google.adk.models import Gemini
from google.adk.tools import AgentTool, ToolContext
from google.adk.events import RequestInput
from google.adk.events.event import Event
from google.adk.apps import App
from google.genai import types

from app.config import config

# Setup Logger
logger = logging.getLogger("attention_span_agent")

# 1. State Schema
class AttentionSpanState(BaseModel):
    focus_duration: int = 0
    interrupted: bool = False
    coins: int = 0
    xp: int = 0
    level: int = 1
    streak: int = 0
    deep_work_duration: int = 0
    current_app_usage: dict[str, int] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)
    audit_log: list[str] = Field(default_factory=list)
    pending_redemption: str | None = None  # To track HITL coin redemption requests

# 2. Setup model
model = Gemini(
    model=config.model,
    retry_options=types.HttpRetryOptions(attempts=3),
)

# 3. Python State Manipulation Tools for Sub-agents
def get_focus_state(ctx: ToolContext) -> str:
    """Gets the current user focus and attention span metrics from session state.

    Returns:
        A string summarizing the current focus metrics, streaks, coins, and levels.
    """
    state = ctx.state
    return (
        f"Current focus duration: {state.get('focus_duration', 0)} minutes.\n"
        f"Deep work duration: {state.get('deep_work_duration', 0)} minutes.\n"
        f"Level: {state.get('level', 1)} (XP: {state.get('xp', 0)}).\n"
        f"Coins balance: {state.get('coins', 0)} coins.\n"
        f"Current focus streak: {state.get('streak', 0)} sessions.\n"
        f"App usage statistics: {state.get('current_app_usage', {})}.\n"
        f"Recommendations: {state.get('recommendations', [])}."
    )

def record_focus_session(duration_minutes: int, interrupted: bool, ctx: ToolContext) -> str:
    """Records a completed or interrupted focus session and awards rewards.

    Args:
        duration_minutes: The duration of the focus session in minutes.
        interrupted: True if the session was interrupted by opening distracting apps, False otherwise.

    Returns:
        A string summarizing the session outcome and XP/coins rewarded.
    """
    state = ctx.state
    state['focus_duration'] = state.get('focus_duration', 0) + duration_minutes
    state['interrupted'] = interrupted

    if not interrupted:
        state['deep_work_duration'] = state.get('deep_work_duration', 0) + duration_minutes
        coins_earned = duration_minutes * 2
        xp_earned = duration_minutes * 5
        state['coins'] = state.get('coins', 0) + coins_earned
        state['xp'] = state.get('xp', 0) + xp_earned
        state['streak'] = state.get('streak', 0) + 1

        old_level = state.get('level', 1)
        new_level = 1 + (state['xp'] // 100)
        state['level'] = new_level
        level_up = f" LEVEL UP to Level {new_level}!" if new_level > old_level else ""

        return f"Successfully logged focus session of {duration_minutes} mins. Earned {coins_earned} coins and {xp_earned} XP.{level_up} Streak: {state['streak']}."
    else:
        state['streak'] = 0
        return f"Focus session of {duration_minutes} mins was interrupted. Streak reset to 0."

def report_app_usage(app_name: str, duration_minutes: int, ctx: ToolContext) -> str:
    """Logs app screen time and detects if it is a distracting application.

    Args:
        app_name: The name of the application.
        duration_minutes: The usage duration in minutes.

    Returns:
        A notification message indicating if the app is a distraction.
    """
    state = ctx.state
    usage = state.setdefault('current_app_usage', {})
    usage[app_name] = usage.get(app_name, 0) + duration_minutes
    state['current_app_usage'] = usage

    distracting_apps = ["instagram", "youtube", "facebook", "snapchat", "tiktok", "games"]
    if app_name.lower() in distracting_apps:
        return f"Warning: Spent {duration_minutes} minutes on distracting app '{app_name}'."
    return f"Logged {duration_minutes} minutes of usage on '{app_name}'."

# 4. Define specialized agents and MCP Toolset
import sys
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioConnectionParams, StdioServerParameters

mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"]
        )
    )
)

distraction_agent = Agent(
    name="distraction_agent",
    model=model,
    instruction=(
        "You are the Distraction and Focus Analyst agent. You analyze application usage patterns, "
        "detect distracting apps (like Instagram, YouTube, Facebook, games), and log sessions. "
        "Coordinate with the user's productivity goal. Use tools to report app usage and record focus sessions. "
        "You also have access to the local MCP distraction warning tools."
    ),
    tools=[record_focus_session, report_app_usage, mcp_toolset],
)

coach_agent = Agent(
    name="coach_agent",
    model=model,
    instruction=(
        "You are the Gamification and Recommendation Coach. You manage user rewards (XP, coins, level, streak) "
        "and suggest personalized productivity tips based on their patterns (e.g. 'You focus best at 8 PM'). "
        "Explain badges, help redeem rewards, and use get_focus_state to understand user status. "
        "You also have access to local MCP productivity tips and study notes tools."
    ),
    tools=[get_focus_state, mcp_toolset],
)


# 5. Define main orchestrator agent
orchestrator = Agent(
    name="orchestrator",
    model=model,
    instruction=(
        "You are the main Orchestrator for the Attention Span Recorder system. "
        "You assist users in tracking attention span, logging focus sessions, checking analytics, "
        "and managing gamification rewards. "
        "Delegate tasks to your sub-agents: "
        "- For focus tracking, session logging, and app usage monitoring, call distraction_agent. "
        "- For levels, streaks, badges, coins, or recommendations, call coach_agent. "
        "If the user explicitly requests to redeem coins for a reward, first confirm their balance, "
        "and if valid, let them know we are initiating the request (the system will handle confirmation)."
    ),
    tools=[
        AgentTool(agent=distraction_agent),
        AgentTool(agent=coach_agent),
    ],
)

# 6. Workflow function nodes
@node
async def security_checkpoint_node(ctx: Context, node_input: Any):
    """Filters query inputs for security risks and PII."""
    query = ""
    if isinstance(node_input, str):
        query = node_input
    elif isinstance(node_input, dict) and "text" in node_input:
        query = node_input["text"]
    elif isinstance(node_input, types.Content):
        query = "".join(p.text for p in node_input.parts if p.text)

    # 1. PII Scrubbing
    pii_regex = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,7}\b|\b\d{3}-\d{2}-\d{4}\b"
    scrubbed_query = re.sub(pii_regex, "[REDACTED]", query)

    # 2. Prompt Injection Detection
    injection_keywords = ["system prompt", "ignore previous instructions", "override", "you are now in developer mode"]
    has_injection = any(kw in query.lower() for kw in injection_keywords)

    # 3. Domain-specific rule: Consent Check / App usage logging consent
    has_consent = "log" in query.lower() or "track" in query.lower() or "focus" in query.lower() or "streak" in query.lower() or "badge" in query.lower() or "redeem" in query.lower() or "recommend" in query.lower() or "hello" in query.lower() or "hi" in query.lower() or "why" in query.lower()

    # Structured Audit Log
    severity = "INFO"
    decision = "pass"
    reason = "All checks passed"

    if has_injection:
        severity = "CRITICAL"
        decision = "reject"
        reason = "Prompt injection keywords detected"
    elif not has_consent and len(query) > 50:
        severity = "WARNING"
        decision = "reject"
        reason = "Non-productive query context / missing consent"

    audit_entry = {
        "severity": severity,
        "decision": decision,
        "reason": reason,
        "input_len": len(query)
    }
    
    # Store in state
    state = ctx.state
    logs = state.get("audit_log", [])
    logs.append(json.dumps(audit_entry))
    state["audit_log"] = logs

    if decision == "reject":
        ctx.route = "SECURITY_EVENT"
        return f"Security alert: request rejected. Reason: {reason}."

    # Proceed to orchestrator with scrubbed text
    ctx.route = "passed"
    return scrubbed_query

@node
async def security_failed_node(ctx: Context, node_input: Any):
    """Handles prompt rejection or policy violations."""
    return str(node_input)

@node(rerun_on_resume=True)
async def orchestrator_node(ctx: Context, node_input: Any):
    """Executes the main orchestrator agent."""
    query = str(node_input)
    
    # Check if request involves coin redemption
    if "redeem" in query.lower():
        # Match reward type
        reward = "Theme"
        if "badge" in query.lower():
            reward = "Bronze Focus Badge"
        elif "wallpaper" in query.lower():
            reward = "Motivational Wallpaper"
            
        ctx.state["pending_redemption"] = reward
        ctx.route = "needs_approval"
        return f"Initiating coin redemption for: {reward}."

    # Normal execution: run the orchestrator agent
    res = await ctx.run_node(orchestrator, query)
    
    # Extract output from Event or run response
    output_text = ""
    if isinstance(res, Event):
        if res.output:
            output_text = str(res.output)
        elif res.content and res.content.parts:
            output_text = "".join(p.text for p in res.content.parts if p.text)
    else:
        output_text = str(res)

    ctx.route = "done"
    return output_text

@node(rerun_on_resume=True)
async def hitl_approval_node(ctx: Context, node_input: Any):
    """Handles Human-In-The-Loop approval for coin redemptions."""
    reward = ctx.state.get("pending_redemption", "Reward")
    interrupt_id = f"redeem_approval_{reward}"
    
    # Check if confirmation exists in resume_inputs
    approval = ctx.resume_inputs.get(interrupt_id)
    if approval is None:
        # Pause and ask for human input
        return RequestInput(
            interrupt_id=interrupt_id,
            message=f"✋ Do you approve redeeming 50 coins to get the '{reward}'? (Type 'yes' or 'no')"
        )
        
    # Process response
    response_text = str(approval).strip().lower()
    ctx.state["pending_redemption"] = None  # clear pending
    
    if response_text in ["yes", "y", "approve"]:
        coins = ctx.state.get("coins", 0)
        if coins >= 50:
            ctx.state["coins"] = coins - 50
            return f"✅ Redemption Approved! Deducted 50 coins. You successfully claimed your '{reward}'. Remaining balance: {ctx.state['coins']} coins."
            
        return f"❌ Redemption Denied: Insufficient coins. You have {coins} coins but need 50."
    else:
        return f"❌ Redemption Cancelled: You chose not to redeem coins."

@node
async def final_output_node(ctx: Context, node_input: Any):
    """Formats and emits the final session result."""
    output_text = str(node_input)
    # Append state values for dashboard simulation
    state = ctx.state
    dashboard = (
        f"\n\n--- 📊 Productivity Dashboard ---\n"
        f"⏱ Total Focus: {state.get('focus_duration', 0)}m | 🏆 Level {state.get('level', 1)} ({state.get('xp', 0)} XP)\n"
        f"🔥 Streak: {state.get('streak', 0)} | 🪙 Coins: {state.get('coins', 0)}"
    )
    return output_text + dashboard

# 7. Workflow setup
attention_workflow = Workflow(
    name="attention_workflow",
    edges=[
        Edge(from_node=START, to_node=security_checkpoint_node),
        Edge(from_node=security_checkpoint_node, to_node=orchestrator_node, route="passed"),
        Edge(from_node=security_checkpoint_node, to_node=security_failed_node, route="SECURITY_EVENT"),
        Edge(from_node=orchestrator_node, to_node=hitl_approval_node, route="needs_approval"),
        Edge(from_node=orchestrator_node, to_node=final_output_node, route="done"),
        Edge(from_node=hitl_approval_node, to_node=final_output_node),
        Edge(from_node=security_failed_node, to_node=final_output_node),
    ],
    state_schema=AttentionSpanState
)

# Export required components for the FastAPI app Lifespan
root_agent = attention_workflow
app = App(
    root_agent=attention_workflow,
    name="app",
)

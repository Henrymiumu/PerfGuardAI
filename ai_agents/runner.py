from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import TextMentionTermination
from autogen_agentchat.teams import SelectorGroupChat
from autogen_core.models import ModelInfo
from autogen_ext.models.openai import OpenAIChatCompletionClient

from ai_agents.config import (
    get_debate_max_rounds,
    get_ollama_base_url,
    get_ollama_model,
    get_ollama_timeout,
)
from ai_agents.debate_summary_agent import DebateSummaryAgent
from ai_agents.deterministic_tool_agent import DeterministicToolAgent


def default_time_range_last_30m() -> tuple[int, int, str]:
    
    now_utc = datetime.now(timezone.utc)
    start_utc = now_utc - timedelta(minutes=30)
    from_ts = int(start_utc.timestamp())
    to_ts = int(now_utc.timestamp())
    label = f"Last 30 minutes (UTC {start_utc.strftime('%Y-%m-%d %H:%M')}~{now_utc.strftime('%H:%M')})"
    return from_ts, to_ts, label


def _select_ollama_model(base_url: str, requested_model: str) -> str:
    
    selected = requested_model
    try:
        resp = requests.get(f"{base_url}/models", timeout=5)
        if resp.status_code != 200:
            return selected
        payload = resp.json()
        ids: list[str] = []
        for item in payload.get("data", []) or []:
            mid = item.get("id")
            if mid:
                ids.append(mid)
        if ids and requested_model not in ids:
            selected = ids[0]
    except Exception:
        pass
    return selected


async def run_task(task: str) -> list[dict[str, Any]]:
    
    base_url = get_ollama_base_url()
    requested_model = get_ollama_model()
    ollama_timeout = get_ollama_timeout()
    max_rounds = get_debate_max_rounds()
    selected_model = _select_ollama_model(base_url, requested_model)

    base_model_info = ModelInfo(
        vision=False,
        function_calling=True,
        json_output=False,
        structured_output=False,
        family="ollama",
    )

    
    model_client = OpenAIChatCompletionClient(
        model=selected_model,
        api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
        base_url=base_url,
        model_info=base_model_info,
        temperature=0.2,
        max_retries=2,
        timeout=ollama_timeout,
    )

    
    summary_a_client = OpenAIChatCompletionClient(
        model=selected_model,
        api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
        base_url=base_url,
        model_info=base_model_info,
        temperature=0.15,
        seed=11,
        max_retries=2,
        timeout=ollama_timeout,
    )
    summary_b_client = OpenAIChatCompletionClient(
        model=selected_model,
        api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
        base_url=base_url,
        model_info=base_model_info,
        temperature=0.35,
        seed=22,
        max_retries=2,
        timeout=ollama_timeout,
    )

    from_ts, to_ts, window_label = default_time_range_last_30m()

    pm = AssistantAgent(
        name="PM",
        model_client=model_client,
        system_message=(
            "You are the PM agent. Your job is to translate the user's question into a single JSON task for the Tool agent.\n"
            "Do NOT ask follow-up questions. Infer which metrics to query based on the user's question.\n"
            "\n"
            "IMPORTANT ROUTING RULE:\n"
            "- If the user's question does NOT require calling the Datadog API (e.g., 'What can you do?', 'How does this app work?', 'Explain the agents'),\n"
            "  then respond directly in English describing PerfGuard AI's capabilities in 2–5 sentences and end with TERMINATE.\n"
            "  PerfGuard AI is a multi-LLM chatbot that fetches real-time system metrics from Datadog and uses a structured debate between two AI agents (Narrator and Skeptical Reviewer) to produce accurate, hallucination-reduced performance analysis and recommendations.\n"
            "  In this case, do NOT output JSON.\n"
            "\n"
            "Metric selection rules:\n"
            "- CPU => include cpu_usage\n"
            "- Memory/RAM => include memory_used_pct (or memory_pct_usable)\n"
            "- Disk => include disk_usage\n"
            "- Network => include network_total\n"
            "\n"
            "If (and only if) the question requires Datadog data, you MUST output ONLY ONE JSON object (no extra text), with this schema:\n"
            "{\n"
            '  \"metrics\": [\"cpu_usage\"],\n'
            "  \"host\": null,\n"
            "  \"from_ts\": null,\n"
            "  \"to_ts\": null,\n"
            '  \"question\": \"...\"\n'
            "}\n"
            "\n"
            "Notes:\n"
            "- Allowed metrics: cpu_usage, cpu_num_cores, memory_used_pct, memory_pct_usable, disk_usage, network_total\n"
            f"- If the user does NOT specify a time range, leave from_ts/to_ts as null. The system default is: {window_label}.\n"
            "- If the user specifies a time range, set from_ts/to_ts (epoch seconds, UTC).\n"
            "- IMPORTANT YEAR RULE: if the user provides a date without a year (e.g., 'Jan 4th'), assume the year is 2026.\n"
            "- Time zone conversion: Hong Kong Time (HKT) is UTC+8. If the user gives times in HKT, convert to UTC by subtracting 8 hours.\n"
            "- Keep 'question' as a short English summary."
        ),
    )

    tool_agent = DeterministicToolAgent(name="Tool", default_from_ts=from_ts, default_to_ts=to_ts)

    summary_agent = DebateSummaryAgent(
        name="Summary",
        model_client=summary_a_client,
        role_label="SummaryA",
        opponent_name="Summary2",
        stance="A",
        max_rounds=max_rounds,
        hide_opponent_in_round1=True,
    )
    summary_agent2 = DebateSummaryAgent(
        name="Summary2",
        model_client=summary_b_client,
        role_label="SummaryB (skeptical reviewer)",
        opponent_name="Summary",
        stance="B",
        max_rounds=max_rounds,
        hide_opponent_in_round1=True,
    )

    advice_agent = AssistantAgent(
        name="Advice",
        model_client=model_client,
        system_message=(
            "You are the Advice agent.\n"
            "Your job: consolidate SummaryA/SummaryB into a single final answer, grounded strictly in Tool JSON facts, then give actionable suggestions.\n"
            "You will see multiple Summary/Summary2 messages across rounds (with CONSENSUS markers). Focus only on the LATEST Summary + Summary2.\n"
            "\n"
            "Output format (must follow):\n"
            "1) SummaryA (1–2 sentences)\n"
            "2) SummaryB (1–2 sentences)\n"
            "3) Consensus facts (2–5 bullets; each bullet MUST cite Tool JSON stats/points, e.g., avg/min/max/count or key timestamps)\n"
            "4) Key disagreements (0–3 bullets; clearly label 'speculation' or 'insufficient data')\n"
            "5) Recommendations (2–5 bullets; each must map back to a consensus fact; keep actionable and concise)\n"
            "\n"
            "Hard rules:\n"
            "- Never invent numbers or conclusions. If Summary conflicts with Tool JSON, Tool JSON wins.\n"
            "- Do NOT paste long quotes from Summary/Summary2. Paraphrase only.\n"
            "- Do NOT output any CONSENSUS lines or standalone 'STATUS=' lines.\n"
            "- You MUST include content for sections (1)~(5). Do NOT reply with only 'TERMINATE'.\n"
            "- If Tool JSON has errors / missing data, explain what is needed (e.g., set DD_API_KEY/DD_APP_KEY or query missing metrics) then TERMINATE.\n"
            "End your message with 'TERMINATE'. Output in English."
        ),
    )

    def _extract_status(messages, source: str) -> str | None:
        import re

        for msg in reversed(messages):
            if getattr(msg, "source", None) != source:
                continue
            content = getattr(msg, "content", "")
            if not isinstance(content, str):
                continue
            m = re.search(r"CONSENSUS\\s+ROUND\\s*=\\s*(\\d+)\\s+STATUS\\s*=\\s*(AGREE|DISAGREE)", content, re.I)
            if m:
                return m.group(2).upper()
            return None
        return None

    def _count_summary(messages) -> int:
        return sum(1 for m in messages if getattr(m, "source", None) in ("Summary", "Summary2"))

    async def selector_func(messages) -> str | None:
        if not messages:
            return "PM"
        last = messages[-1]
        last_source = getattr(last, "source", None)

        if last_source == "user":
            return "PM"
        if last_source == "PM":
            return "Tool"
        if last_source == "Tool":
            return "Summary"

        summary_count = _count_summary(messages)
        max_debate_msgs = 8  
        if summary_count >= max_debate_msgs:
            return "Advice"

        a = _extract_status(messages, "Summary")
        b = _extract_status(messages, "Summary2")
        if summary_count >= 4 and a == "AGREE" and b == "AGREE":
            return "Advice"

        return "Summary" if (summary_count % 2 == 0) else "Summary2"

    team = SelectorGroupChat(
        participants=[pm, tool_agent, summary_agent, summary_agent2, advice_agent],
        model_client=model_client,
        selector_func=selector_func,
        termination_condition=TextMentionTermination("TERMINATE"),
        max_turns=20,
    )

    result = await team.run(task=task)
    out: list[dict[str, Any]] = []
    for m in result.messages:
        content = getattr(m, "content", None)
        if not isinstance(content, str) and content is not None:
            content = str(content)
        if not isinstance(content, str):
            continue
        out.append({"source": getattr(m, "source", "unknown"), "content": content})
    return out


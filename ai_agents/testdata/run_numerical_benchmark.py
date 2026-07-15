from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_core import CancellationToken
from autogen_core.models import ModelInfo, SystemMessage, UserMessage
from autogen_ext.models.openai import OpenAIChatCompletionClient

from ai_agents.config import get_ollama_base_url, get_ollama_model, get_ollama_timeout
from ai_agents.debate_summary_agent import DebateSummaryAgent





_NARRATOR_SYSTEM = (
    "You are a computer technician (narrator). "
    "Summarize telemetry data into a readable timeline and highlight trends. "
    "Clearly label speculation as speculation. "
    "Use only the provided Tool JSON data; do not invent values. "
    "Prefer time-series patterns, spikes, drops, and before/after comparisons."
)

_ANALYSIS_TASK = (
    "Analyze the system telemetry below and explain potential performance problems.\n\n"
    "Output sections:\n"
    "1) Observed facts — REQUIRED sub-items:\n"
    "   a) Peak value: the GLOBAL maximum across the ENTIRE series.\n"
    "      IMPORTANT: scan every data point to the LAST entry — do NOT stop at the first\n"
    "      prominent jump. If the trend is continuously rising, the peak is at the END.\n"
    "      State the exact maximum value and its timestamp (epoch ms) for EACH series.\n"
    "   b) Floor value: the GLOBAL minimum across the ENTIRE series.\n"
    "      IMPORTANT: scan every data point to the LAST entry.\n"
    "      State the exact minimum value and its timestamp (epoch ms) for EACH series.\n"
    "   c) Turning points: list any obvious spikes, drops, or burst events with their\n"
    "      exact value and timestamp (epoch ms). If none, write 'No significant turning points.'\n"
    "   d) Overall trend description (rising / falling / stable / burst-then-recover)\n"
    "2) Potential risks\n"
    "3) Uncertainty / speculation (label clearly)\n"
    "4) Recommendations\n\n"
    "Keep metadata notes brief unless they change operational interpretation.\n\n"
)






def _format_tool_json_for_llm(tool_json: dict) -> str:
    
    series_list = tool_json.get("series", [])
    n = len(series_list)

    header = [
        f"status: {tool_json.get('status', 'ok')}",
        f"query:  {tool_json.get('query', '')}",
        f"range:  {tool_json.get('from_date', '')} → {tool_json.get('to_date', '')}",
        f"total_series: {n}",
        "",
    ]

    blocks = []
    for i, s in enumerate(series_list, 1):
        scope       = s.get("scope", "")
        metric      = s.get("metric", "")
        pt_count    = s.get("point_count", len(s.get("pointlist", [])))
        null_count  = s.get("null_count", 0)
        pointlist   = s.get("pointlist", [])

        sep = "=" * 55
        block = [
            sep,
            f"SERIES {i} of {n}",
            f"  metric : {metric}",
            f"  scope  : {scope}",
            f"  points : {pt_count}" + (f"   nulls: {null_count}" if null_count else ""),
            f"  data   : [timestamp_ms, value]",
        ]
        for pt in pointlist:
            ts, val = pt[0], pt[1]
            block.append(f"    [{int(ts)}, {val if val is not None else 'null'}]")
        block.append(sep)
        blocks.append("\n".join(block))

    return "\n".join(header) + "\n".join(blocks)






def _ms_to_readable(ms: float) -> str:
    
    import datetime
    try:
        dt = datetime.datetime.utcfromtimestamp(ms / 1000)
        return dt.strftime("%H:%M:%S UTC")
    except Exception:
        return str(int(ms))


def detect_turning_points(
    pointlist: list[list],
    *,
    min_change_pct: float = 25.0,
    step_ratio_threshold: float = 5.0,
) -> list[dict[str, Any]]:
    
    valid = [(ts, v) for ts, v in pointlist if v is not None]
    if len(valid) < 3:
        return []

    timestamps = [ts for ts, _ in valid]
    values = [v for _, v in valid]
    v_range = max(values) - min(values)
    if v_range == 0:
        return []

    
    
    threshold = max(min_change_pct / 100.0 * v_range, 0.10 * max(values))
    turning_points: list[dict[str, Any]] = []
    seen_indices: set[int] = set()

    def _add(tp: dict[str, Any]) -> None:
        idx = tp["index"]
        if idx not in seen_indices:
            seen_indices.add(idx)
            turning_points.append(tp)

    
    for i in range(1, len(values) - 1):
        rise = values[i] - values[i - 1]
        fall = values[i + 1] - values[i]
        if rise * fall >= 0:
            continue  
        magnitude = max(abs(rise), abs(fall))
        if magnitude < threshold:
            continue
        tp_type = "peak" if rise > 0 else "valley"
        _add({
            "type": tp_type,
            "value": values[i],
            "timestamp": timestamps[i],
            "readable_time": _ms_to_readable(timestamps[i]),
            "index": i,
        })

    
    for i in range(1, len(values)):
        prev, curr = values[i - 1], values[i]
        if prev <= 0:
            continue
        ratio = curr / prev
        if ratio >= step_ratio_threshold:
            
            
            _add({
                "type": "burst_start",
                "value": curr,
                "timestamp": timestamps[i],
                "readable_time": _ms_to_readable(timestamps[i]),
                "index": i,
                "note": f"step up x{round(ratio,1)} from {prev}",
            })
        elif ratio <= 1.0 / step_ratio_threshold:
            _add({
                "type": "burst_end",
                "value": curr,
                "timestamp": timestamps[i],
                "readable_time": _ms_to_readable(timestamps[i]),
                "index": i,
                "note": f"step down to 1/{round(1/ratio,1)} of {prev}",
            })

    
    if len(values) >= 3:
        look = min(4, len(values) - 1)
        avg_next = sum(values[1: 1 + look]) / look
        if values[0] - avg_next >= threshold:
            _add({
                "type": "peak",
                "value": values[0],
                "timestamp": timestamps[0],
                "readable_time": _ms_to_readable(timestamps[0]),
                "index": 0,
                "note": "boundary peak (start)",
            })

    turning_points.sort(key=lambda x: x["index"])
    return turning_points


def compute_numerical_facts(tool_json: dict[str, Any]) -> list[dict[str, Any]]:
    
    facts: list[dict[str, Any]] = []
    for series in tool_json.get("series", []):
        metric = series.get("metric", "?")
        scope = series.get("scope", "?")
        pointlist = series.get("pointlist", [])
        valid = [(ts, v) for ts, v in pointlist if v is not None]
        if not valid:
            continue

        values = [v for _, v in valid]
        timestamps = [ts for ts, _ in valid]

        max_val = max(values)
        max_ts = timestamps[values.index(max_val)]
        min_val = min(values)
        min_ts = timestamps[values.index(min_val)]

        facts.append(
            {
                "fact_id": f"{scope}|max",
                "fact_type": "max_value",
                "metric": metric,
                "scope": scope,
                "value": max_val,
                "timestamp": max_ts,
                "readable_time": _ms_to_readable(max_ts),
                "description": (
                    f"Maximum {metric} value is {max_val} at {_ms_to_readable(max_ts)} "
                    f"(epoch {int(max_ts)})"
                ),
            }
        )
        facts.append(
            {
                "fact_id": f"{scope}|min",
                "fact_type": "min_value",
                "metric": metric,
                "scope": scope,
                "value": min_val,
                "timestamp": min_ts,
                "readable_time": _ms_to_readable(min_ts),
                "description": (
                    f"Minimum {metric} value is {min_val} at {_ms_to_readable(min_ts)} "
                    f"(epoch {int(min_ts)})"
                ),
            }
        )

        tps = detect_turning_points(pointlist)
        for tp in tps:
            facts.append(
                {
                    "fact_id": f"{scope}|tp_{tp['index']}",
                    "fact_type": "turning_point",
                    "metric": metric,
                    "scope": scope,
                    "tp_type": tp["type"],
                    "value": tp["value"],
                    "timestamp": tp["timestamp"],
                    "readable_time": tp["readable_time"],
                    "note": tp.get("note", ""),
                    "description": (
                        f"Turning point ({tp['type']}) at {tp['readable_time']} "
                        f"with value {tp['value']} (scope: {scope})"
                    ),
                }
            )

    return facts






def extract_numbers_from_text(text: str) -> list[float]:
    
    
    
    raw = re.findall(r"\b(\d[\d,]*(?:\.\d+)?)\s*%?", text)
    result = []
    for r in raw:
        try:
            
            result.append(float(r.replace(",", "")))
        except ValueError:
            pass
    return result


def extract_timestamps_from_text(text: str) -> list[float]:
    
    raw = re.findall(r"\b(17\d{11})\b", text)
    return [float(r) for r in raw]


def value_mentioned(
    output_text: str,
    target_value: float,
    *,
    tolerance_pct: float = 5.0,
) -> tuple[bool, float | None]:
    
    nums = extract_numbers_from_text(output_text)
    if not nums:
        return False, None
    tolerance = tolerance_pct / 100.0 * abs(target_value) if target_value != 0 else tolerance_pct
    best: float | None = None
    best_dist = float("inf")
    for n in nums:
        dist = abs(n - target_value)
        if dist < best_dist:
            best_dist = dist
            best = n
    found = best_dist <= tolerance
    return found, best


def timestamp_mentioned(
    output_text: str,
    target_ts: float,
    *,
    tolerance_ms: float = 600_000,  
) -> tuple[bool, float | None]:
    
    tss = extract_timestamps_from_text(output_text)
    if not tss:
        return False, None
    best: float | None = None
    best_dist = float("inf")
    for ts in tss:
        dist = abs(ts - target_ts)
        if dist < best_dist:
            best_dist = dist
            best = ts
    found = best_dist <= tolerance_ms
    return found, best






def score_fact(fact: dict[str, Any], output_text: str) -> dict[str, Any]:
    
    target_val = fact["value"]
    target_ts = fact["timestamp"]

    val_found, val_closest = value_mentioned(output_text, target_val)
    ts_found, ts_closest = timestamp_mentioned(output_text, target_ts)

    if val_found and ts_found:
        result = "FULL"
        label = "value + timestamp both mentioned"
    elif val_found:
        result = "PARTIAL"
        label = f"value {target_val} mentioned (closest: {val_closest}); timestamp not found"
    elif ts_found:
        result = "PARTIAL"
        label = f"timestamp mentioned; value not found (target: {target_val}, closest: {val_closest})"
    else:
        result = "MISS"
        label = f"neither value ({target_val}) nor timestamp found in output"

    return {
        "fact_id": fact["fact_id"],
        "fact_type": fact["fact_type"],
        "description": fact["description"],
        "target_value": target_val,
        "target_timestamp": target_ts,
        "value_found": val_found,
        "value_closest": val_closest,
        "timestamp_found": ts_found,
        "timestamp_closest": ts_closest,
        "result": result,
        "label": label,
    }


def accuracy_from_scores(scored: list[dict[str, Any]]) -> dict[str, float]:
    
    if not scored:
        return {"full_pct": 0.0, "partial_pct": 0.0, "weighted_pct": 0.0}
    full = sum(1 for s in scored if s["result"] == "FULL")
    partial = sum(1 for s in scored if s["result"] == "PARTIAL")
    total = len(scored)
    weighted = (full * 1.0 + partial * 0.5) / total * 100
    return {
        "full_pct": round(full / total * 100, 1),
        "partial_pct": round((full + partial) / total * 100, 1),
        "weighted_pct": round(weighted, 1),
        "full": full,
        "partial": partial,
        "miss": total - full - partial,
        "total": total,
    }






def _load_cases() -> list[dict[str, Any]]:
    p = Path(__file__).with_name("synthetic_datadog_cases.json")
    payload = json.loads(p.read_text(encoding="utf-8"))
    return payload.get("cases", []) or []


def _load_rules_text() -> str:
    p = Path(__file__).with_name("synthetic_data_rules.md")
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _select_ollama_model(base_url: str, requested_model: str) -> str:
    try:
        resp = requests.get(f"{base_url}/models", timeout=5)
        if resp.status_code == 200:
            ids = [x.get("id") for x in (resp.json().get("data") or []) if x.get("id")]
            if ids and requested_model not in ids:
                return ids[0]
    except Exception:
        pass
    return requested_model


def _build_client(*, temperature: float, seed: int, timeout: float) -> OpenAIChatCompletionClient:
    base_url = get_ollama_base_url()
    model = _select_ollama_model(base_url, get_ollama_model())
    return OpenAIChatCompletionClient(
        model=model,
        api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
        base_url=base_url,
        model_info=ModelInfo(
            vision=False, function_calling=True,
            json_output=False, structured_output=False, family="ollama",
        ),
        temperature=temperature,
        seed=seed,
        max_retries=2,
        timeout=timeout,
    )


def _filter_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    case_id = os.getenv("SYNTH_CASE_ID", "").strip()
    return [c for c in cases if str(c.get("case_id", "")).strip() == case_id] if case_id else cases


def _extract_debate_status(text: str) -> str | None:
    m = re.search(
        r"CONSENSUS\s+ROUND\s*=\s*\d+\s+STATUS\s*=\s*(AGREE|DISAGREE)",
        text or "", re.IGNORECASE,
    )
    return m.group(1).upper() if m else None


async def _run_single(
    client: OpenAIChatCompletionClient, *, rules_text: str, tool_json_text: str
) -> str:
    user = _ANALYSIS_TASK + f"RULES:\n{rules_text}\n\nTool JSON:\n{tool_json_text}\n"
    res = await client.create(
        messages=[SystemMessage(content=_NARRATOR_SYSTEM), UserMessage(content=user, source="single")],
        cancellation_token=CancellationToken(),
    )
    return (res.content if isinstance(res.content, str) else str(res.content)).strip()


async def _run_multi(
    narrator_client: OpenAIChatCompletionClient,
    reviewer_client: OpenAIChatCompletionClient,
    consolidator_client: OpenAIChatCompletionClient,
    *,
    rules_text: str,
    tool_json_text: str,
    max_rounds: int,
) -> tuple[str, list[dict]]:
    summary_a = DebateSummaryAgent(
        name="Summary", model_client=narrator_client, role_label="SummaryA",
        opponent_name="Summary2", stance="A", max_rounds=max_rounds,
        hide_opponent_in_round1=True, compact_prompt=False,
    )
    summary_b = DebateSummaryAgent(
        name="Summary2", model_client=reviewer_client, role_label="SummaryB",
        opponent_name="Summary", stance="B", max_rounds=max_rounds,
        hide_opponent_in_round1=True, compact_prompt=False,
    )

    tool_input = _ANALYSIS_TASK + f"RULES:\n{rules_text}\n\nTool JSON:\n{tool_json_text}\n"
    messages: list[BaseChatMessage] = [TextMessage(source="Tool", content=tool_input)]

    for idx in range(max_rounds * 2):
        agent = summary_a if idx % 2 == 0 else summary_b
        resp = await agent.on_messages(messages, CancellationToken())
        msg = resp.chat_message
        if isinstance(msg, TextMessage):
            messages.append(msg)
            if os.environ.get("DEBATE_VERBOSE") == "1":
                rnd = (idx // 2) + 1
                print(f"\n{'='*60}")
                print(f"Round {rnd}  |  Agent: {msg.source}")
                print('='*60)
                print(msg.content)
        a_msgs = [m for m in messages if getattr(m, "source", "") == "Summary"]
        b_msgs = [m for m in messages if getattr(m, "source", "") == "Summary2"]
        if a_msgs and b_msgs:
            if (_extract_debate_status(a_msgs[-1].content) == "AGREE" and
                    _extract_debate_status(b_msgs[-1].content) == "AGREE"):
                break

    latest_a = next((m.content for m in reversed(messages) if getattr(m, "source", "") == "Summary"), "")
    latest_b = next((m.content for m in reversed(messages) if getattr(m, "source", "") == "Summary2"), "")

    advice_res = await consolidator_client.create(
        messages=[
            SystemMessage(content=(
                "You are the Advice agent. Your job is to consolidate the debate between SummaryA (Narrator) "
                "and SummaryB (Reviewer) into a final answer.\n\n"
                "Step 1 — Extract Verifiable Consensus:\n"
                "Look at the LATEST round output from EACH agent (SummaryA and SummaryB).\n"
                "Find the 【Verifiable Consensus (Tool JSON only)】 section in each agent's output.\n"
                "Copy ALL numerical facts listed in those sections — every peak, floor, spike, valley, and\n"
                "turning point that appears in EITHER agent's Verifiable Consensus.\n"
                "These are the agreed facts from the debate. Do NOT omit any of them.\n\n"
                "Step 2 — Resolve conflicts:\n"
                "If SummaryA and SummaryB list different values for the same fact, use Tool JSON to pick the correct one.\n"
                "If a value is in one agent's Verifiable Consensus but absent from the other's, still include it —\n"
                "absence does not mean rejection.\n\n"
                "Step 3 — Role of Tool JSON:\n"
                "Use Tool JSON ONLY to verify or correct values found in Step 1.\n"
                "Do NOT add new facts from Tool JSON that neither agent mentioned.\n\n"
                "Output sections:\n"
                "1) Debate outcome summary (1-2 sentences)\n"
                "2) Numerical facts — ALL facts from both Verifiable Consensus sections (Step 1 + Step 2):\n"
                "   - Per series: Peak value(s): list EACH separately with exact value and epoch ms timestamp\n"
                "   - Per series: Floor value: <number> at <epoch ms>\n"
                "   - Turning points: list EACH one with value and epoch ms timestamp\n"
                "   Add [source: SummaryA / SummaryB / both] after each fact.\n"
                "3) Consensus interpretation (overall trend / risk level)\n"
                "4) Remaining disagreements (if any)\n"
                "5) Recommendations\n\n"
                "Hard rules:\n"
                "- Section 2 is mandatory. List every fact from both Verifiable Consensus sections.\n"
                "- A fact present in EITHER agent's Verifiable Consensus must appear in your output.\n"
                "- Do NOT summarise or merge turning points into one — list each one individually.\n"
                "- Do NOT output CONSENSUS lines.\n"
                "End with TERMINATE."
            )),
            UserMessage(
                content=(
                    f"Tool JSON:\n{tool_json_text}\n\n"
                    f"Latest SummaryA:\n{latest_a}\n\n"
                    f"Latest SummaryB:\n{latest_b}"
                ),
                source="Advice",
            ),
        ],
        cancellation_token=CancellationToken(),
    )
    final_text = (advice_res.content if isinstance(advice_res.content, str) else str(advice_res.content)).strip()
    if final_text.endswith("TERMINATE"):
        final_text = final_text[: -len("TERMINATE")].rstrip()

    debate_log = [
        {"source": getattr(m, "source", ""), "content": m.content}
        for m in messages if getattr(m, "source", "") in ("Summary", "Summary2")
    ]
    return final_text, debate_log






async def main() -> None:
    cases = _filter_cases(_load_cases())
    if not cases:
        raise RuntimeError("No matching case found.")
    rules_text = _load_rules_text()
    if rules_text:
        print("[INFO] Loaded synthetic_data_rules.md", flush=True)

    timeout = get_ollama_timeout()
    max_rounds = int(os.getenv("DEBATE_MAX_ROUNDS", "2"))

    single_client = _build_client(temperature=0.15, seed=11, timeout=timeout)
    narrator_client = _build_client(temperature=0.15, seed=11, timeout=timeout)
    reviewer_client = _build_client(temperature=0.35, seed=22, timeout=timeout)
    consolidator_client = _build_client(temperature=0.2, seed=303, timeout=timeout)

    started = time.time()
    all_results: list[dict[str, Any]] = []

    for c in cases:
        case_id = c.get("case_id", "unknown")
        title = c.get("title", "")
        tool_json = c.get("tool_json", {})
        
        tool_json_text = _format_tool_json_for_llm(tool_json)

        
        facts = compute_numerical_facts(tool_json)

        print(f"\n{'='*60}", flush=True)
        print(f"[CASE] {case_id}", flush=True)
        print(f"  Numerical facts extracted: {len(facts)}", flush=True)
        for f in facts:
            print(f"    [{f['fact_type']}] {f['description']}", flush=True)

        
        print(f"\n  [RUN] single LLM...", flush=True)
        t0 = time.time()
        single_text = await _run_single(single_client, rules_text=rules_text, tool_json_text=tool_json_text)
        t1 = time.time()
        print(f"\n  [OUTPUT][single]\n{single_text}\n", flush=True)

        
        print(f"  [RUN] multi-LLM ({max_rounds} rounds)...", flush=True)
        multi_text, debate_log = await _run_multi(
            narrator_client, reviewer_client, consolidator_client,
            rules_text=rules_text, tool_json_text=tool_json_text,
            max_rounds=max_rounds,
        )
        t2 = time.time()

        if os.getenv("PRINT_DEBATE", "0").strip().lower() in ("1", "true", "yes"):
            for i, dm in enumerate(debate_log):
                role = "SummaryA" if dm["source"] == "Summary" else "SummaryB"
                print(f"\n  [Debate Round {i//2+1} - {role}]\n{dm['content']}\n", flush=True)

        print(f"\n  [OUTPUT][multi-final]\n{multi_text}\n", flush=True)

        
        single_scored = [score_fact(f, single_text) for f in facts]
        multi_scored = [score_fact(f, multi_text) for f in facts]

        single_acc = accuracy_from_scores(single_scored)
        multi_acc = accuracy_from_scores(multi_scored)

        print(f"\n  --- NUMERICAL FACT SCORING ---", flush=True)
        print(f"  {'Fact':<45} {'Single':>10} {'Multi':>10}", flush=True)
        print(f"  {'-'*65}", flush=True)
        for s, m in zip(single_scored, multi_scored):
            desc = s["description"][:43]
            print(f"  {desc:<45} {s['result']:>10} {m['result']:>10}", flush=True)

        print(f"\n  Single LLM  -> weighted: {single_acc['weighted_pct']}%  "
              f"full: {single_acc['full']}/{single_acc['total']}  "
              f"partial: {single_acc['partial']}/{single_acc['total']}", flush=True)
        print(f"  Multi-LLM   -> weighted: {multi_acc['weighted_pct']}%  "
              f"full: {multi_acc['full']}/{multi_acc['total']}  "
              f"partial: {multi_acc['partial']}/{multi_acc['total']}", flush=True)

        winner = (
            "multi" if multi_acc["weighted_pct"] > single_acc["weighted_pct"]
            else ("single" if single_acc["weighted_pct"] > multi_acc["weighted_pct"] else "tie")
        )
        print(f"  Winner: {winner.upper()}", flush=True)
        print(f"\n  [DONE] single={round(t1-t0,1)}s  multi={round(t2-t1,1)}s  total={round(t2-t0,1)}s", flush=True)

        all_results.append({
            "case_id": case_id,
            "title": title,
            "numerical_facts": facts,
            "single": {
                "elapsed_sec": round(t1 - t0, 2),
                "accuracy": single_acc,
                "scored_facts": single_scored,
                "analysis_text": single_text,
            },
            "multi": {
                "elapsed_sec": round(t2 - t1, 2),
                "accuracy": multi_acc,
                "scored_facts": multi_scored,
                "analysis_text": multi_text,
                "debate_log": debate_log,
            },
            "winner": winner,
        })

    
    total_facts = sum(len(r["numerical_facts"]) for r in all_results)
    single_weighted = sum(r["single"]["accuracy"]["weighted_pct"] for r in all_results) / len(all_results)
    multi_weighted = sum(r["multi"]["accuracy"]["weighted_pct"] for r in all_results) / len(all_results)

    print(f"\n{'='*60}", flush=True)
    print(f"[OVERALL] cases={len(all_results)}  total_numerical_facts={total_facts}", flush=True)
    print(f"  Single LLM  avg weighted accuracy: {round(single_weighted,1)}%", flush=True)
    print(f"  Multi-LLM   avg weighted accuracy: {round(multi_weighted,1)}%", flush=True)

    
    artifact_dir = Path(__file__).parent
    json_path = artifact_dir / "numerical_benchmark_results.json"
    md_path = artifact_dir / "numerical_benchmark_report.md"

    payload = {
        "meta": {
            "generated_at_epoch": int(time.time()),
            "total_elapsed_sec": round(time.time() - started, 2),
            "case_count": len(all_results),
            "total_numerical_facts": total_facts,
            "debate_max_rounds": max_rounds,
            "ollama_model": get_ollama_model(),
            "scoring": "FULL=1.0, PARTIAL=0.5(value found, timestamp missing or vice versa), MISS=0",
            "overall_single_weighted_pct": round(single_weighted, 1),
            "overall_multi_weighted_pct": round(multi_weighted, 1),
        },
        "results": all_results,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    
    lines: list[str] = ["# Numerical Fact Extraction Benchmark", ""]
    lines += [
        "## Scoring Method",
        "",
        "Objective: check if LLM output mentions specific numerical facts from Tool JSON.",
        "",
        "| Result | Criteria | Score weight |",
        "|--|--|--|",
        "| FULL | Correct value AND timestamp both mentioned (within tolerance) | 1.0 |",
        "| PARTIAL | Value OR timestamp mentioned, but not both | 0.5 |",
        "| MISS | Neither value nor timestamp found in output | 0.0 |",
        "",
        f"Value tolerance: ±5% | Timestamp tolerance: ±600,000 ms (1 data point interval)",
        "",
        "## Overall Results",
        "",
        "| System | Avg Weighted Accuracy |",
        "|--|--|",
        f"| Single LLM | {round(single_weighted,1)}% |",
        f"| Multi-LLM  | {round(multi_weighted,1)}% |",
        "",
    ]

    for r in all_results:
        lines += [f"## Case: {r['case_id']}", f"*{r['title']}*", ""]
        lines += [
            "| System | Weighted | FULL | PARTIAL | MISS | Time |",
            "|--|--|--|--|--|--|",
            f"| Single | {r['single']['accuracy']['weighted_pct']}% | "
            f"{r['single']['accuracy']['full']} | {r['single']['accuracy']['partial']} | "
            f"{r['single']['accuracy']['miss']} | {r['single']['elapsed_sec']}s |",
            f"| Multi  | {r['multi']['accuracy']['weighted_pct']}% | "
            f"{r['multi']['accuracy']['full']} | {r['multi']['accuracy']['partial']} | "
            f"{r['multi']['accuracy']['miss']} | {r['multi']['elapsed_sec']}s |",
            f"| **Winner** | | | | | **{r['winner'].upper()}** |",
            "",
            "### Fact Breakdown",
            "",
            "| Fact | Type | Single | Multi |",
            "|--|--|--|--|",
        ]
        for s, m in zip(r["single"]["scored_facts"], r["multi"]["scored_facts"]):
            desc = s["description"][:60]
            lines.append(f"| {desc} | {s['fact_type']} | {s['result']} | {m['result']} |")
        lines.append("")

    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    print(f"\n[OK] JSON -> {json_path}")
    print(f"[OK] MD   -> {md_path}")

    await single_client.close()
    await narrator_client.close()
    await reviewer_client.close()
    await consolidator_client.close()


if __name__ == "__main__":
    asyncio.run(main())

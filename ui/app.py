from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import streamlit as st




_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import html as html_mod

import ai_agents.debate_summary_agent as debate_summary_agent
import ai_agents.runner as runner
from ai_agents.testdata.run_numerical_benchmark import (
    _ANALYSIS_TASK,
    _NARRATOR_SYSTEM,
    _build_client,
    _filter_cases,
    _format_tool_json_for_llm,
    _load_cases,
    _load_rules_text,
    _run_multi,
    _run_single,
    accuracy_from_scores,
    compute_numerical_facts,
    extract_numbers_from_text,
    score_fact,
)




st.set_page_config(
    page_title="PerfGuard AI",
    page_icon="🛡️",
    layout="wide",
)

_CONS_RE = re.compile(
    r"CONSENSUS\s+ROUND\s*=\s*(\d+)\s+STATUS\s*=\s*(AGREE|DISAGREE)", re.I
)






def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    new_loop = asyncio.new_event_loop()
    try:
        return new_loop.run_until_complete(coro)
    finally:
        new_loop.close()


def _strip_terminate(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    lines = text.splitlines()
    while lines and lines[-1].strip() == "":
        lines.pop()
    if lines and lines[-1].strip() == "TERMINATE":
        lines.pop()
    return "\n".join(lines).strip()


def _extract_consensus(text: str) -> tuple[int | None, str | None]:
    m = _CONS_RE.search(text or "")
    if not m:
        return None, None
    return int(m.group(1)), m.group(2).upper()


def _extract_objections_block(text: str) -> str:
    
    pat = re.compile(
        r"(?:【My Objections[^】]*】|\*\*My Objections[^*]*\*\*)([\s\S]*?)"
        r"(?=【|\*\*[A-Z]|\Z)",
        re.IGNORECASE,
    )
    m = pat.search(text or "")
    return m.group(1).strip() if m else ""


def _score_badge(result: str) -> str:
    colors = {"FULL": ("#16a34a", "#dcfce7"), "PARTIAL": ("#b45309", "#fef9c3"), "MISS": ("#dc2626", "#fee2e2")}
    fg, bg = colors.get(result, ("#374151", "#f3f4f6"))
    return (
        f'<span style="background:{bg};color:{fg};font-weight:700;'
        f'padding:2px 8px;border-radius:6px;font-size:0.82em;">{result}</span>'
    )


def _consensus_badge(status: str | None) -> str:
    if status == "AGREE":
        return '<span style="background:#dcfce7;color:#16a34a;font-weight:800;padding:3px 10px;border-radius:8px;">✓ AGREE</span>'
    if status == "DISAGREE":
        return '<span style="background:#fee2e2;color:#dc2626;font-weight:800;padding:3px 10px;border-radius:8px;">✗ DISAGREE</span>'
    return ""






def _render_single_llm(text: str) -> None:
    st.markdown(
        f"""
        <div style="border:2px solid #7c3aed;border-radius:12px;padding:16px;background:#faf5ff;">
          <div style="font-weight:800;color:#7c3aed;margin-bottom:8px;">🤖 Single LLM Analysis</div>
          <div style="white-space:pre-wrap;font-family:ui-sans-serif,system-ui;
               line-height:1.55;font-size:0.9em;">{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _build_annotated_html(text: str, scores: list[dict], facts: list[dict]) -> str:
    
    fact_map = {f["fact_id"]: f for f in facts}

    
    val_entries: list[tuple[float, float, str]] = []
    for s in scores:
        fact = fact_map.get(s["fact_id"], {})
        val = fact.get("value")
        if val is not None:
            tol = 0.05 * abs(float(val)) if float(val) != 0 else 0.5
            val_entries.append((float(val), tol, s["result"]))

    _rank = {"FULL": 3, "PARTIAL": 2, "MISS": 1}
    _icon = {
        "FULL":    '<span style="color:#16a34a;font-weight:700;margin-right:4px;">✅</span>',
        "PARTIAL": '<span style="color:#b45309;font-weight:700;margin-right:4px;">⚠️</span>',
        "MISS":    '<span style="color:#dc2626;font-weight:700;margin-right:4px;">❌</span>',
    }

    html_lines: list[str] = []
    for line in text.split("\n"):
        nums = extract_numbers_from_text(line)
        best: str | None = None
        if nums:
            for val, tol, result in val_entries:
                for n in nums:
                    if abs(n - val) <= tol:
                        if best is None or _rank[result] > _rank.get(best, 0):
                            best = result
                        break

        escaped = html_mod.escape(line)
        if best:
            html_lines.append(f"{_icon[best]}{escaped}")
        else:
            html_lines.append(escaped)

    return "\n".join(html_lines)


def _render_score_card(scores: list[dict], facts: list[dict], *, title: str, accent: str) -> None:
    
    fact_map = {f["fact_id"]: f for f in facts}
    icons = {"FULL": "✅", "PARTIAL": "⚠️", "MISS": "❌"}
    rows_html = ""
    for s in scores:
        res = s.get("result", "N/A")
        icon = icons.get(res, "•")
        fid  = s.get("fact_id", "")
        desc = fact_map.get(fid, {}).get("description", fid)
        short = desc[:55] + ("…" if len(desc) > 55 else "")
        rows_html += (
            f'<div style="display:flex;align-items:flex-start;gap:6px;'
            f'padding:5px 0;border-bottom:1px solid #f0f0f0;">'
            f'<span style="font-size:1em;flex-shrink:0;">{icon}</span>'
            f'<span style="font-size:0.8em;line-height:1.35;color:#374151;">{short}</span>'
            f'</div>'
        )
    st.markdown(
        f'<div style="border:1.5px solid {accent};border-radius:10px;'
        f'padding:12px 14px;background:#fff;">'
        f'<div style="font-weight:800;color:{accent};margin-bottom:8px;">{title}</div>'
        f'{rows_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_analysis_with_scores(
    text: str,
    scores: list[dict],
    facts: list[dict],
    *,
    label: str,
    text_border: str,
    text_bg: str,
    card_accent: str,
    card_title: str,
) -> None:
    
    annotated_html = _build_annotated_html(text, scores, facts)

    col_text, col_scores = st.columns([13, 7])
    with col_text:
        st.markdown(
            f"""
            <div style="border:2px solid {text_border};border-radius:12px;
                 padding:16px;background:{text_bg};">
              <div style="font-weight:800;color:{text_border};margin-bottom:8px;">{label}</div>
              <div style="white-space:pre-wrap;font-family:ui-sans-serif,system-ui;
                   line-height:1.6;font-size:0.88em;">{annotated_html}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_scores:
        _render_score_card(scores, facts, title=card_title, accent=card_accent)


def _render_debate_round(round_no: int, a_text: str, b_text: str, *, last: bool = False) -> None:
    _, a_status = _extract_consensus(a_text)
    _, b_status = _extract_consensus(b_text)

    
    a_display = _CONS_RE.sub("", a_text).strip()
    b_display = _CONS_RE.sub("", b_text).strip()

    obj_a = _extract_objections_block(a_display)
    obj_b = _extract_objections_block(b_display)

    agreed = a_status == "AGREE" and b_status == "AGREE"
    hdr_color = "#16a34a" if agreed else "#f97316"
    hdr_text = f"Round {round_no}" + (" — ✓ AGREED" if agreed else "")

    st.markdown(
        f"""<div style="background:{hdr_color};color:#fff;font-weight:800;
             text-align:center;padding:8px 0;border-radius:10px;margin:14px 0 8px 0;">
          {hdr_text}
        </div>""",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)

    for col, label, display, obj, status in [
        (col1, "Narrator (A)", a_display, obj_a, a_status),
        (col2, "Reviewer (B)", b_display, obj_b, b_status),
    ]:
        bg = "#f1f5f9" if label.startswith("N") else "#f0fdf4"
        border = "#94a3b8" if label.startswith("N") else "#86efac"
        with col:
            badge = _consensus_badge(status)
            
            if obj:
                highlighted = display.replace(
                    obj,
                    f'<span style="background:#fef9c3;border-left:3px solid #f59e0b;'
                    f'display:block;padding:4px 8px;">{obj}</span>',
                )
            else:
                highlighted = display

            st.markdown(
                f"""
                <div style="border:1.5px solid {border};border-radius:12px;
                     padding:14px;background:{bg};min-height:120px;">
                  <div style="display:flex;justify-content:space-between;
                       align-items:center;margin-bottom:8px;">
                    <span style="font-weight:800;">{label}</span>
                    {badge}
                  </div>
                  <div style="white-space:pre-wrap;font-family:ui-sans-serif,system-ui;
                       line-height:1.45;font-size:0.88em;">{highlighted}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_advice(text: str, *, is_benchmark: bool = False) -> None:
    title = "📋 Final Advice (Benchmark)" if is_benchmark else "📋 Advice"
    border = "#0891b2" if is_benchmark else "#22c55e"
    bg = "#ecfeff" if is_benchmark else "#f0fdf4"
    st.markdown(
        f"""
        <div style="border:2px solid {border};border-radius:12px;
             padding:16px;background:{bg};margin-top:8px;">
          <div style="font-weight:800;color:{border};margin-bottom:8px;">{title}</div>
          <div style="white-space:pre-wrap;font-family:ui-sans-serif,system-ui;
               line-height:1.55;font-size:0.9em;">{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_scoring_table(
    facts: list[dict],
    single_scores: list[dict],
    multi_scores: list[dict],
    single_acc: dict,
    multi_acc: dict,
) -> None:
    
    s_by_id = {s["fact_id"]: s for s in single_scores}
    m_by_id = {m["fact_id"]: m for m in multi_scores}

    
    c1, c2 = st.columns(2)
    with c1:
        sw = single_acc.get("weighted_pct", 0)
        st.metric("Single LLM", f"{sw:.1f}%",
                  delta=None, delta_color="off")
        st.progress(sw / 100)
    with c2:
        mw = multi_acc.get("weighted_pct", 0)
        delta = round(mw - sw, 1)
        st.metric("Multi-LLM", f"{mw:.1f}%",
                  delta=f"{delta:+.1f}% vs Single")
        st.progress(mw / 100)

    st.markdown("---")

    
    header = (
        '<div style="display:grid;grid-template-columns:2fr 1fr 1fr;'
        'gap:8px;font-weight:700;padding:6px 4px;border-bottom:2px solid #e5e7eb;">'
        "<span>Fact</span><span style='text-align:center'>Single</span>"
        "<span style='text-align:center'>Multi</span></div>"
    )
    rows = ""
    for f in facts:
        fid = f["fact_id"]
        desc = f["description"][:70] + ("…" if len(f["description"]) > 70 else "")
        sr = s_by_id.get(fid, {}).get("result", "N/A")
        mr = m_by_id.get(fid, {}).get("result", "N/A")
        rows += (
            f'<div style="display:grid;grid-template-columns:2fr 1fr 1fr;'
            f'gap:8px;padding:5px 4px;border-bottom:1px solid #f3f4f6;">'
            f"<span style='font-size:0.85em;'>{desc}</span>"
            f"<span style='text-align:center'>{_score_badge(sr)}</span>"
            f"<span style='text-align:center'>{_score_badge(mr)}</span>"
            f"</div>"
        )

    st.markdown(
        f'<div style="border:1px solid #e5e7eb;border-radius:10px;'
        f'padding:12px;background:#fafafa;">{header}{rows}</div>',
        unsafe_allow_html=True,
    )





with st.sidebar:
    st.image("https://img.shields.io/badge/PerfGuard-AI-orange?style=for-the-badge", width=200)
    st.markdown("## ⚙️ Settings")

    st.markdown("**Ollama**")
    st.caption("Ensure `ollama serve` is running")

    st.markdown("**Datadog** (Live mode)")
    st.caption("Set `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE` in `.env`")

    st.divider()
    st.markdown("**Display options**")
    show_single = st.checkbox("Show Single LLM output", value=True,
                              help="Compare single-LLM analysis against the multi-agent debate")
    show_tool_json = st.checkbox("Show Tool / Raw JSON", value=False)
    show_pm = st.checkbox("Show PM task JSON (live mode)", value=False)

    st.divider()
    debate_rounds = st.selectbox("Debate rounds", [2, 4], index=1)
    st.caption("More rounds = deeper debate, slower response")





st.markdown(
    """
    <div style="background:linear-gradient(135deg,#1e3a5f,#0891b2);
         border-radius:16px;padding:24px 28px;margin-bottom:20px;color:#fff;">
      <h1 style="margin:0;font-size:1.8em;">🛡️ PerfGuard AI</h1>
      <p style="margin:6px 0 0 0;opacity:0.85;font-size:0.95em;">
        Multi-LLM Collaborative Inference for System Performance Analysis
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)




tab_live, tab_bench = st.tabs(["💬 Live Query (Datadog)", "🧪 Benchmark Test Cases"])





with tab_live:
    st.markdown("Ask about real-time metrics from Datadog. The PM agent will route your query.")

    question = st.text_area(
        "Your question",
        placeholder="e.g. 'Analyze CPU usage in the last 30 minutes' or 'Was RAM spiking recently?'",
        height=90,
        key="live_question",
    )
    run_live = st.button("🚀 Run Live Query", type="primary", use_container_width=True, key="run_live")

    if run_live:
        if not question.strip():
            st.warning("Please enter a question.")
        else:
            with st.spinner("Running agents…"):
                importlib.reload(debate_summary_agent)
                importlib.reload(runner)
                msgs = _run_async(runner.run_task(question.strip()))
            st.session_state["live_msgs"] = msgs

    msgs = st.session_state.get("live_msgs")
    if msgs:
        pm_msg    = next((m["content"] for m in msgs if m["source"] == "PM"),     None)
        tool_msg  = next((m["content"] for m in msgs if m["source"] == "Tool"),   None)
        advice_msgs = [m["content"] for m in msgs if m["source"] == "Advice"]
        summary_msgs = [(m["source"], m["content"])
                        for m in msgs if m["source"] in ("Summary", "Summary2")]

        
        pm_is_direct = False
        if pm_msg:
            try:
                json.loads(pm_msg)
            except Exception:
                pm_is_direct = True

        if pm_is_direct and pm_msg:
            direct_text = _strip_terminate(pm_msg)
            st.markdown(
                f"""
                <div style="border:2px solid #60a5fa;border-radius:12px;
                     padding:16px;background:#eff6ff;margin-bottom:12px;">
                  <div style="font-weight:800;color:#1d4ed8;margin-bottom:8px;">
                    🤖 Assistant Reply
                  </div>
                  <div style="white-space:pre-wrap;font-family:ui-sans-serif,system-ui;
                       line-height:1.55;">{direct_text}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        
        if (show_pm or show_tool_json) and not pm_is_direct:
            cols = st.columns(2)
            if show_pm and pm_msg:
                with cols[0]:
                    st.markdown("**PM task JSON**")
                    try:
                        st.json(json.loads(pm_msg), expanded=False)
                    except Exception:
                        st.code(pm_msg)
            if show_tool_json and tool_msg:
                with cols[1]:
                    st.markdown("**Tool raw JSON**")
                    try:
                        st.json(json.loads(tool_msg), expanded=False)
                    except Exception:
                        st.code(tool_msg)

        
        if advice_msgs:
            _render_advice(_strip_terminate(advice_msgs[-1]))

        
        if not pm_is_direct:
            st.markdown("### 🗣️ Debate Rounds")
        if not summary_msgs and not pm_is_direct:
            st.info("No debate messages (PM answered directly).")
        else:
            pairs: list[tuple[str, str]] = []
            buf_a: str | None = None
            for src, content in summary_msgs:
                if src == "Summary":
                    buf_a = content
                elif src == "Summary2":
                    pairs.append((buf_a or "(Missing A)", content))
                    buf_a = None
            if buf_a:
                pairs.append((buf_a, "(Missing B)"))

            for idx, (a, b) in enumerate(pairs, 1):
                _render_debate_round(idx, a, b, last=(idx == len(pairs)))





with tab_bench:
    st.markdown(
        "Select a synthetic test case to run both **Single LLM** and **Multi-LLM debate**, "
        "then compare their accuracy against ground-truth numerical facts."
    )

    
    try:
        all_cases = _load_cases()
        case_options = {f"{c['case_id']} — {c['title']}": c for c in all_cases}
    except Exception as e:
        st.error(f"Could not load test cases: {e}")
        st.stop()

    selected_label = st.selectbox(
        "Choose a test case",
        list(case_options.keys()),
        key="bench_case",
    )
    selected_case = case_options[selected_label]

    
    with st.expander("📄 Case details", expanded=False):
        traits = selected_case.get("special_traits", [])
        if traits:
            st.markdown("**Special traits:**")
            for t in traits:
                st.markdown(f"- {t}")
        tool_json = selected_case.get("tool_json", {})
        if show_tool_json:
            st.json(tool_json, expanded=False)

    
    tool_json = selected_case.get("tool_json", {})
    facts = compute_numerical_facts(tool_json)

    with st.expander(f"📐 Ground-truth facts ({len(facts)} facts)", expanded=True):
        for f in facts:
            icon = {"max_value": "🔺", "min_value": "🔻", "turning_point": "↩️"}.get(f["fact_type"], "•")
            st.markdown(f"{icon} `{f['fact_type'].upper()}` {f['description']}")

    run_bench = st.button("▶ Run Benchmark", type="primary", use_container_width=True, key="run_bench")

    if run_bench:
        timeout   = float(os.getenv("OLLAMA_TIMEOUT", "300"))
        rules_text = _load_rules_text()
        formatted_json = _format_tool_json_for_llm(tool_json)

        single_client      = _build_client(temperature=0.15, seed=11, timeout=timeout)
        narrator_client    = _build_client(temperature=0.15, seed=11, timeout=timeout)
        reviewer_client    = _build_client(temperature=0.35, seed=22, timeout=timeout)
        consolidator_client = _build_client(temperature=0.2, seed=303, timeout=timeout)

        prog = st.progress(0, text="Running Single LLM…")
        t0 = time.time()

        single_text = _run_async(_run_single(
            single_client, rules_text=rules_text, tool_json_text=formatted_json
        ))
        prog.progress(40, text="Running Multi-LLM debate…")

        multi_text, debate_log = _run_async(_run_multi(
            narrator_client, reviewer_client, consolidator_client,
            rules_text=rules_text, tool_json_text=formatted_json,
            max_rounds=debate_rounds,
        ))
        prog.progress(90, text="Scoring…")

        single_scores = [score_fact(f, single_text) for f in facts]
        multi_scores  = [score_fact(f, multi_text)  for f in facts]
        single_acc    = accuracy_from_scores(single_scores)
        multi_acc     = accuracy_from_scores(multi_scores)

        prog.progress(100, text=f"Done in {time.time()-t0:.1f}s")
        time.sleep(0.3)
        prog.empty()

        st.session_state["bench_result"] = {
            "single_text":    single_text,
            "multi_text":     multi_text,
            "debate_log":     debate_log,
            "facts":          facts,
            "single_scores":  single_scores,
            "multi_scores":   multi_scores,
            "single_acc":     single_acc,
            "multi_acc":      multi_acc,
            "elapsed":        round(time.time() - t0, 1),
        }

    result = st.session_state.get("bench_result")
    if result:
        st.markdown("---")

        
        st.markdown("### 📊 Accuracy Comparison")
        _render_scoring_table(
            result["facts"],
            result["single_scores"],
            result["multi_scores"],
            result["single_acc"],
            result["multi_acc"],
        )

        sw = result["single_acc"]["weighted_pct"]
        mw = result["multi_acc"]["weighted_pct"]
        if mw > sw:
            winner_msg = f"🏆 **Multi-LLM wins** ({mw:.1f}% vs {sw:.1f}%)"
        elif sw > mw:
            winner_msg = f"🏆 **Single LLM wins** ({sw:.1f}% vs {mw:.1f}%)"
        else:
            winner_msg = f"🤝 **Tie** (both {sw:.1f}%)"
        st.success(winner_msg + f"  |  Elapsed: {result['elapsed']}s")

        
        if show_single:
            st.markdown("### 🤖 Single LLM Analysis")
            _render_analysis_with_scores(
                result["single_text"],
                result["single_scores"],
                result["facts"],
                label="🤖 Single LLM Analysis",
                text_border="#7c3aed",
                text_bg="#faf5ff",
                card_accent="#7c3aed",
                card_title="Single LLM Score",
            )

        
        st.markdown("### 🗣️ Multi-LLM Debate")
        st.caption(
            "🟡 **Highlighted sections** = Objections / Additions raised by that agent in that round. "
            "These are the key corrections and new findings."
        )

        debate_log = result["debate_log"]
        pairs: list[tuple[str, str]] = []
        buf_a: str | None = None
        for msg in debate_log:
            if msg["source"] == "Summary":
                buf_a = msg["content"]
            elif msg["source"] == "Summary2":
                pairs.append((buf_a or "(Missing A)", msg["content"]))
                buf_a = None
        if buf_a:
            pairs.append((buf_a, "(Missing B)"))

        for idx, (a, b) in enumerate(pairs, 1):
            _render_debate_round(idx, a, b, last=(idx == len(pairs)))

        
        st.markdown("### 📋 Final Consolidated Answer (Advice Agent)")
        _render_analysis_with_scores(
            result["multi_text"],
            result["multi_scores"],
            result["facts"],
            label="📋 Final Advice (Multi-LLM)",
            text_border="#0891b2",
            text_bg="#ecfeff",
            card_accent="#0891b2",
            card_title="Multi-LLM Score",
        )

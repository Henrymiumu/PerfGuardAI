from __future__ import annotations

import difflib
import re
from typing import Optional, Sequence

from autogen_agentchat.base import ChatAgent, Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_core import CancellationToken
from autogen_core.models import ChatCompletionClient, SystemMessage, UserMessage

from ai_agents.config import get_summary_format_guard_enabled, get_summary_similarity_guard_enabled


_CONS_RE = re.compile(
    r"CONSENSUS\s+ROUND\s*=\s*(\d+)\s+STATUS\s*=\s*(AGREE|DISAGREE)", re.IGNORECASE
)

SECTION_OPP_KEY_POINTS = "Opponent Key Points"
SECTION_MY_OBJECTIONS = "My Objections / Additions"
SECTION_IMPROVEMENTS = "Improvements"
SECTION_OPP_OBJECTIONS = "Opponent Objections (Previous Round)"
SECTION_MY_RESPONSE = "My Point-by-Point Response"
SECTION_ADDITIONAL = "Additional Questions / Additions"
SECTION_OPP_CLAIMS_R4 = "Opponent Claims / Objections (Previous Round)"
SECTION_REMAINING = "Remaining Disagreements"
SECTION_VERIFIABLE = "Verifiable Consensus (Tool JSON only)"

_ROUND_LINE_RE = re.compile(r"^\s*[*_`-]*\s*Round\s*\d+\s*[:：].*$", re.IGNORECASE)
_STATUS_LINE_RE = re.compile(r"^\s*STATUS\s*=\s*(AGREE|DISAGREE)\s*$", re.IGNORECASE)


def _count_source(messages: Sequence[BaseChatMessage], source: str) -> int:
    c = 0
    for m in messages:
        if getattr(m, "source", None) == source:
            c += 1
    return c


def _last_content_from(messages: Sequence[BaseChatMessage], source: str) -> Optional[str]:
    for m in reversed(messages):
        if getattr(m, "source", None) != source:
            continue
        content = getattr(m, "content", None)
        if isinstance(content, str):
            return content
        if content is not None:
            return str(content)
    return None


def _extract_status(text: str) -> Optional[str]:
    
    
    matches = list(_CONS_RE.finditer(text or ""))
    if not matches:
        return None
    return matches[-1].group(2).upper()


def _strip_after_consensus(text: str) -> str:
    
    matches = list(_CONS_RE.finditer(text or ""))
    if not matches:
        return text
    return (text or "")[: matches[-1].start()].rstrip()


def _clean_for_history(text: str, *, max_chars: int = 1200) -> str:
    
    if not isinstance(text, str):
        text = str(text)
    s = _strip_after_consensus(text)
    s = _CONS_RE.sub("", s).strip()
    if len(s) > max_chars:
        s = s[:max_chars].rstrip() + "\n...(truncated)"
    return s


def _post_clean_output(text: str) -> str:
    
    lines: list[str] = []
    for ln in (text or "").splitlines():
        if _ROUND_LINE_RE.match(ln):
            continue
        if _STATUS_LINE_RE.match(ln):
            continue
        
        if re.match(r"^\s*(Here is|Here's)\s*Round\s*\d+\s*(response)?\s*:\s*$", ln, re.IGNORECASE):
            continue
        if re.match(r"^\s*以下是\s*Round\s*\d+\s*的回應\s*：\s*$", ln, re.IGNORECASE):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def _needs_format_rewrite(text: str) -> bool:
    
    t = text or ""
    
    if any(_ROUND_LINE_RE.match(ln) for ln in t.splitlines()):
        return True
    
    if t.count("【Opponent") >= 2:
        return True
    if t.count(f"【{SECTION_VERIFIABLE}") >= 2:
        return True
    
    for ln in t.splitlines():
        if _STATUS_LINE_RE.match(ln):
            return True
    return False


def _extract_objection_points(opponent_text: str, *, max_points: int = 3) -> list[str]:
    
    text = (opponent_text or "").strip()
    if not text:
        return []

    _p_re = re.compile(r"^\s*[-•*]?\s*P(\d+)\s*:\s*(.+)$")
    
    _bullet_re = re.compile(r"^\s*[-•*]\s*(.{10,})$")

    def _lines_to_candidates(block: str, *, use_bullets: bool = False) -> list[str]:
        out = []
        for ln in block.splitlines():
            s = ln.strip()
            m = _p_re.match(s)
            if m:
                out.append(re.sub(r"\s+", " ", m.group(2)).strip())
                continue
            if use_bullets:
                bm = _bullet_re.match(s)
                if bm:
                    out.append(re.sub(r"\s+", " ", bm.group(1)).strip())
        return out

    def _try_pattern(pat: str, *, use_bullets: bool = False) -> list[str]:
        body_pat = re.compile(
            pat + r"([\s\S]*?)(?=\n【|\n\*\*[A-Z][a-z][^*\n]*\*\*|\n#{1,3}\s|\Z)",
            re.IGNORECASE,
        )
        all_m = list(body_pat.finditer(text))
        if not all_m:
            return []
        return _lines_to_candidates(all_m[-1].group(1), use_bullets=use_bullets)

    
    candidates = _try_pattern(rf"【\s*{re.escape(SECTION_MY_OBJECTIONS)}\s*】")

    
    if not candidates:
        candidates = _try_pattern(rf"\*\*\s*{re.escape(SECTION_MY_OBJECTIONS)}\s*\*\*")

    
    if not candidates:
        candidates = _try_pattern(r"(?:【|##\s*|\*\*)[^】\n*#]*Objections[^】\n*#]*(?:】|\*\*|:)")

    
    
    if len(candidates) < max_points:
        imp_candidates = _try_pattern(
            rf"(?:【\s*{re.escape(SECTION_IMPROVEMENTS)}\s*】|\*\*\s*{re.escape(SECTION_IMPROVEMENTS)}\s*\*\*|##\s+{re.escape(SECTION_IMPROVEMENTS)}\s*)",
            use_bullets=True,
        )
        seen = set(candidates)
        for c in imp_candidates:
            if c not in seen:
                candidates.append(c)
                seen.add(c)
            if len(candidates) >= max_points:
                break

    
    if not candidates:
        obj_kw_pos = -1
        for ln in text.splitlines():
            if re.search(r"objection", ln, re.IGNORECASE):
                obj_kw_pos = text.find(ln)
                break
        search_text = text[obj_kw_pos:] if obj_kw_pos >= 0 else text
        candidates = _lines_to_candidates(search_text)

    
    out: list[str] = []
    for c in candidates:
        c = c.strip()
        if c in out:
            continue
        if len(c) > 220:
            c = c[:220].rstrip() + "…"
        out.append(c)
        if len(out) >= max_points:
            break
    return out


def _rewrite_section(text: str, title: str, body_lines: list[str]) -> str:
    
    header = f"【{title}】"
    block = header + "\n" + "\n".join(body_lines).rstrip() + "\n"
    
    pattern = re.compile(
        rf"(?:【\s*{re.escape(title)}\s*】|\*\*\s*{re.escape(title)}\s*\*\*|##\s+{re.escape(title)}\s*)"
        rf"[\s\S]*?(?=【|\*\*[A-Z]|^#{1,3}\s|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )
    all_matches = list(pattern.finditer(text or ""))
    if not all_matches:
        return (block + "\n" + (text or "").lstrip()).strip()
    
    last = all_matches[-1]
    return ((text or "")[: last.start()] + block + (text or "")[last.end():]).strip()


def _remove_section(text: str, title: str) -> str:
    pattern = re.compile(rf"【\s*{re.escape(title)}\s*】[\s\S]*?(?=【|\Z)")
    return pattern.sub("", text or "").strip()


class DebateSummaryAgent(ChatAgent):
    

    def __init__(
        self,
        *,
        name: str,
        model_client: ChatCompletionClient,
        role_label: str,
        opponent_name: str,
        stance: str,
        max_rounds: int = 4,
        tool_source: str = "Tool",
        hide_opponent_in_round1: bool = True,
        compact_prompt: bool = False,
    ) -> None:
        self._name = name
        self._model_client = model_client
        self._role_label = role_label
        self._opponent_name = opponent_name
        self._stance = stance.upper().strip() or "A"
        self._max_rounds = int(max_rounds)
        self._tool_source = tool_source
        self._hide_opponent_in_round1 = bool(hide_opponent_in_round1)
        self._compact_prompt = bool(compact_prompt)
        
        self._turn_count = 0
        self._tool_json_cache: str = ""
        
        
        self._last_by_source: dict[str, str] = {}
        self._history_by_source: dict[str, list[str]] = {}
        
        
        
        self._objections_cache: dict[str, list[str]] = {}

    @property
    def name(self) -> str:  
        return self._name

    @property
    def description(self) -> str:  
        return f"Debate summary agent ({self._role_label}) with enforced rounds."

    @property
    def produced_message_types(self):  
        return (TextMessage,)

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        
        for m in messages:
            src = getattr(m, "source", None)
            content = getattr(m, "content", None)
            if not isinstance(content, str) and content is not None:
                content = str(content)
            if not isinstance(content, str):
                continue
            if src == self._tool_source:
                self._tool_json_cache = content
            if isinstance(src, str) and src:
                
                
                
                
                if src == self._opponent_name:
                    pts = _extract_objection_points(content, max_points=2)
                    if pts:
                        self._objections_cache[src] = pts

                cleaned = _clean_for_history(content) if src != self._tool_source else content
                self._last_by_source[src] = cleaned
                hist = self._history_by_source.setdefault(src, [])
                
                
                if not hist or hist[-1] != cleaned:
                    hist.append(cleaned)

        self._turn_count += 1
        round_no = min(self._turn_count, self._max_rounds)

        tool_json = self._tool_json_cache
        
        
        
        opponent_last = ""
        opp_hist = self._history_by_source.get(self._opponent_name, [])
        if round_no >= 2 and opp_hist:
            idx = round_no - 2  
            if 0 <= idx < len(opp_hist):
                opponent_last = opp_hist[idx]
            else:
                
                opponent_last = opp_hist[-1]
        else:
            opponent_last = self._last_by_source.get(self._opponent_name, "")

        
        if round_no == 1 and self._hide_opponent_in_round1:
            opponent_last = ""

        if self._compact_prompt:
            if self._stance == "B":
                role_style = "Role: skeptical reviewer. Correct weak claims and unsupported speculation.\n"
            else:
                role_style = "Role: narrator. Summarize trends clearly for operators.\n"

            system = (
                f"You are a summary debate agent ({self._role_label}).\n"
                f"There are at most {self._max_rounds} rounds. Output only Round {round_no}.\n"
                "- Use only Tool JSON facts.\n"
                "- Do not invent values.\n"
                "- Keep it concise and concrete.\n"
                "- Prioritize answering the machine problem itself (state/risk/trend) over discussing query wording.\n"
                "- Query/metric wording mismatch can be mentioned at most once briefly; do not let it dominate the answer.\n"
                "- In factual observations, prioritize performance behavior (trend, volatility, spikes/drops, persistence, risk).\n"
                "- Treat metadata (host name, metric labels, units, query syntax) as secondary unless it changes operational interpretation.\n"
                "- For each major claim, include compact evidence in-line as `Evidence: ts=<...>, value=<...>` or `Evidence: range=<...>, values=<...>`.\n"
                "- Last line must be: CONSENSUS ROUND=<n> STATUS=<AGREE|DISAGREE>\n"
                f"{role_style}"
            )

            if round_no == 1:
                round_instr = (
                    "Round 1: independent analysis only.\n"
                    "- Facts + possible issues (focus on machine behavior first).\n"
                    "- Observed facts should emphasize performance signals, not metadata listing.\n"
                    "- Use 3~5 bullets total; at least 2 bullets must include explicit Claim + Evidence.\n"
                    "- Label speculation clearly.\n"
                    "End with CONSENSUS ROUND=1 STATUS=DISAGREE.\n"
                )
            elif round_no == 2:
                if self._stance == "B":
                    round_instr = (
                        "Round 2: reviewer mode (error-finding priority).\n"
                        f"Use sections: 【{SECTION_OPP_KEY_POINTS}】, 【{SECTION_MY_OBJECTIONS}】, 【{SECTION_IMPROVEMENTS}】.\n"
                        "- Find up to 3 concrete issues: factual error, unsupported claim, or missing important signal.\n"
                        "- Each objection must include `Evidence:` from Tool JSON.\n"
                        "- Prefer correcting trend/risk interpretation errors over query wording debates.\n"
                    )
                else:
                    round_instr = (
                        "Round 2: narrator response mode.\n"
                        f"Use sections: 【{SECTION_OPP_KEY_POINTS}】, 【{SECTION_MY_OBJECTIONS}】, 【{SECTION_IMPROVEMENTS}】.\n"
                        "- Keep each point short and Tool-JSON grounded.\n"
                        "- Address reviewer concerns, then improve clarity of performance conclusions.\n"
                    )
            elif round_no == 3:
                round_instr = (
                    "Round 3: respond to opponent objections.\n"
                    f"Use sections: 【{SECTION_MY_RESPONSE}】 and 【{SECTION_ADDITIONAL}】.\n"
                    "- Address P1/P2 directly with evidence.\n"
                    "- Keep focus on actionable understanding of machine state.\n"
                    "- For each response line, include `Evidence:`.\n"
                )
            else:
                round_instr = (
                    "Round 4: final convergence attempt.\n"
                    f"Use sections: 【{SECTION_REMAINING}】 and 【{SECTION_VERIFIABLE}】.\n"
                    "- Mark what is agreed vs still uncertain.\n"
                    "- Remaining disagreements must be problem-impacting (not wording-only) whenever possible.\n"
                    "- Verifiable consensus must center on performance findings, not metadata recap.\n"
                    "- In 【Verifiable】, include only claims that have direct Tool JSON evidence.\n"
                )
        else:
            
            if self._stance == "B":
                role_style = (
                    "Working style (must follow):\n"
                    "- You are the skeptical reviewer. Your PRIMARY job is NUMERICAL COMPLETENESS AND ACCURACY:\n"
                    "  1. MISSING EVENTS (highest priority): Did the opponent miss a distinct spike, valley, or burst event?\n"
                    "     Scan the Tool JSON pointlist and check if there are multiple separate high/low peaks.\n"
                    "     If you find an event the opponent did not mention, flag it immediately with its exact value and timestamp.\n"
                    "  2. WRONG VALUES: Did the opponent state a number or timestamp that does not match the Tool JSON?\n"
                    "  3. MISSING PEAK/FLOOR: Did the opponent omit the exact maximum or minimum value with its timestamp?\n"
                    "  4. Wording / phrasing issues are LOW PRIORITY — only raise if the numerical facts above are already complete.\n"
                    "- Do NOT argue about synonyms ('gradual decrease' vs 'falling'). That is a waste of a round.\n"
                    "- Still write for an end user: they want to understand the machine state.\n"
                    "- Do not copy the opponent verbatim. If you quote, quote at most ONE sentence using `> quote`.\n"
                    "- From Round 2 onward: add at least 2 NEW objections focused on missing or wrong numerical facts.\n"
                )
            else:
                role_style = (
                    "Working style (must follow):\n"
                    "- You are an infrastructure telemetry analyst and narrator.\n"
                    "- PRIMARY focus: NUMERICAL COMPLETENESS — identify ALL distinct events (spikes, valleys, bursts)\n"
                    "  with exact values AND timestamps from the Tool JSON pointlist.\n"
                    "- When revising based on reviewer feedback, prioritize adding missed numerical events,\n"
                    "  correcting wrong values, and filling in missing timestamps.\n"
                    "- Clearly label speculation. Do NOT invent values.\n"
                    "- Do not copy the opponent verbatim. If you quote, quote at most ONE sentence using `> quote`.\n"
                    "- From Round 2 onward: if the reviewer flags a missing event or wrong number, acknowledge and correct it.\n"
                )

            system = (
                f"You are a 'summary + debate' agent ({self._role_label}).\n"
                f"Rules: there are only {self._max_rounds} rounds total. You MUST output Round {round_no}, and only this round.\n"
                "- Never invent numbers or conclusions.\n"
                "- You may ONLY use information from the Tool JSON.\n"
                "- Do NOT propose action items (Advice will handle that).\n"
                "- Your last line MUST be exactly: CONSENSUS ROUND=<n> STATUS=<AGREE|DISAGREE>\n"
                f"{role_style}"
            )

            
            if round_no == 1:
                round_instr = (
                    "Round 1: write an independent analysis (do NOT reference the opponent).\n"
                    "Use this exact output structure — same as a standalone single-LLM analysis:\n"
                    "1) Observed facts — ALL sub-items are REQUIRED:\n"
                    "   a) Peak value: the GLOBAL maximum across the ENTIRE series.\n"
                    "      IMPORTANT: scan every data point to the LAST entry — do NOT stop at the first\n"
                    "      prominent jump. If the trend is continuously rising, the peak is at the END.\n"
                    "      State the exact maximum value and its timestamp (epoch ms) for EACH series.\n"
                    "   b) Floor value: the GLOBAL minimum across the ENTIRE series.\n"
                    "      IMPORTANT: scan every data point to the LAST entry.\n"
                    "      State the exact minimum value and its timestamp (epoch ms) for EACH series.\n"
                    "   c) Turning points: list every obvious spike, drop, or burst event with exact value and\n"
                    "      timestamp (epoch ms). If none, write 'No significant turning points.'\n"
                    "   d) Overall trend description (rising / falling / stable / burst-then-recover)\n"
                    "2) Potential risks\n"
                    "3) Uncertainty / speculation (label clearly)\n"
                    "The last line MUST be: CONSENSUS ROUND=1 STATUS=DISAGREE\n"
                )
            elif round_no == 2:
                round_instr = (
                    "Round 2: critique the opponent's Round 1 — FOCUS ON NUMERICAL FACTS FIRST.\n"
                    "Before writing, scan the Tool JSON pointlist and ask yourself:\n"
                    "  (a) Are there multiple separate spike or valley events? Did the opponent mention ALL of them?\n"
                    "  (b) Did the opponent state the correct peak value and its exact timestamp?\n"
                    "  (c) Did the opponent state the correct floor value and its exact timestamp?\n"
                    "Use this exact structure:\n"
                    f"【{SECTION_OPP_KEY_POINTS}】\n"
                    "- P1: ... (opponent's key numerical finding; single line)\n"
                    "- P2: ...\n"
                    f"【{SECTION_MY_OBJECTIONS}】\n"
                    "PRIORITY: missing events > wrong values > missing timestamps > wording issues.\n"
                    "IMPORTANT: Every significant numerical finding (missing spike, wrong value, missing valley)\n"
                    "MUST appear here as a P-item — do NOT put critical findings only in Improvements.\n"
                    "- P1: ... (most critical numerical gap — cite exact value + timestamp from Tool JSON)\n"
                    "- P2: ... (second gap — cite exact value + timestamp)\n"
                    "- P3: ... (third gap if any — cite exact value + timestamp)\n"
                    f"【{SECTION_IMPROVEMENTS}】\n"
                    "- 1~3 bullets: supplementary context (e.g. trend clarification, additional occurrences).\n"
                    "  Critical missing events belong in Objections above, not here.\n"
                )
            elif round_no == 3:
                round_instr = (
                    "Round 3: respond to the opponent's Round 2 objections.\n"
                    f"The system will inject 【{SECTION_OPP_OBJECTIONS}】 for you. Do NOT rewrite that section.\n"
                    f"【{SECTION_MY_RESPONSE}】\n"
                    "For EACH objection (P1, P2, P3), follow this verification rule BEFORE accepting:\n"
                    "  VERIFICATION RULE: Look up the opponent's claimed value AND timestamp directly in\n"
                    "  Tool JSON. If you can find [timestamp, value] in the pointlist that matches the\n"
                    "  opponent's claim, ACCEPT and cite it. If you CANNOT find it, REJECT and keep your\n"
                    "  original answer, explaining which Tool JSON entry supports your original value.\n"
                    "- Response to P1 (accept/rebut with Tool JSON evidence):\n"
                    "- Response to P2 (accept/rebut with Tool JSON evidence):\n"
                    "- Response to P3 (accept/rebut with Tool JSON evidence): (if P3 exists)\n"
                    f"【{SECTION_ADDITIONAL}】\n"
                    "- 1~3 bullets: add any REMAINING missed events or corrections not yet covered.\n"
                    "  Must cite exact values and timestamps from Tool JSON.\n"
                    "IMPORTANT: Do NOT drop numerical facts you established in earlier rounds unless\n"
                    "  the opponent's correction is verified in Tool JSON.\n"
                    f"Important: do NOT output any 【{SECTION_OPP_OBJECTIONS}】 section; it is provided by the system.\n"
                )
            else:
                round_instr = (
                    "Round 4: final convergence attempt.\n"
                    "Use this exact structure:\n"
                    f"【{SECTION_OPP_CLAIMS_R4}】\n"
                    "- P1: ... (single line; summarize the opponent's prior-round numerical objection)\n"
                    "- P2: ...\n"
                    "- P3: ... (if exists)\n"
                    "【My Final Response】\n"
                    "VERIFICATION RULE: For each response, look up the claim in Tool JSON pointlist.\n"
                    "  ACCEPT only if the exact [timestamp, value] pair exists in Tool JSON.\n"
                    "  REJECT if it does not exist, and cite your own Tool JSON entry instead.\n"
                    "- Response to P1 (accept/rebut with Tool JSON evidence):\n"
                    "- Response to P2 (accept/rebut with Tool JSON evidence):\n"
                    "- Response to P3 (accept/rebut with Tool JSON evidence): (if P3 exists)\n"
                    f"【{SECTION_REMAINING}】\n"
                    "- Only list NUMERICAL facts still in dispute (wrong value, missing event).\n"
                    f"【{SECTION_VERIFIABLE}】\n"
                    "- List ALL agreed numerical facts across ALL rounds — do not omit findings from earlier rounds:\n"
                    "  * Peak value(s) + timestamp(s) — list EACH distinct spike separately\n"
                    "  * Floor value + timestamp\n"
                    "  * Each turning point (valley, secondary spike, burst) with value + timestamp\n"
                    "  Strictly use Tool JSON values. A fact agreed in Round 1/2 must still appear here.\n"
                    "If all key numerical facts are agreed, STATUS=AGREE; otherwise STATUS=DISAGREE.\n"
                )

        
        
        
        
        if round_no >= 3:
            opp_points = self._objections_cache.get(self._opponent_name) or \
                         _extract_objection_points(opponent_last, max_points=2)
        else:
            opp_points = []
        opp_points_text = ""
        if opp_points:
            opp_points_text = (
                "Opponent objections (extracted from the previous round). "
                "If Round>=3, treat these as P1/P2/P3 exactly:\n"
                + "\n".join([f"- P{i+1}: {p}" for i, p in enumerate(opp_points)])
                + "\n\n"
            )

        user = (
            f"Tool JSON (use ONLY this data):\n{tool_json}\n\n"
            "Metric semantics rule:\n"
            "- Treat each pointlist value as the canonical metric value to analyze.\n"
            "- Do NOT reinterpret pointlist values as another metric (e.g., idle) unless the metric field explicitly indicates that metric.\n\n"
            f"{opp_points_text}"
            f"Opponent previous-round content (may be empty in Round 1):\n{opponent_last}\n\n"
            f"{round_instr}\n"
            "Output in English."
        )

        async def _call(extra_note: str = "") -> str:
            res = await self._model_client.create(
                messages=[
                    SystemMessage(content=system),
                    UserMessage(content=(user + ("\n\n" + extra_note if extra_note else "")), source=self._name),
                ],
                cancellation_token=cancellation_token,
            )
            return res.content if isinstance(res.content, str) else ""

        content = await _call()

        
        if get_summary_format_guard_enabled() and _needs_format_rewrite(content):
            content = await _call(
                "FORMAT WARNING: your output echoed instructions / duplicated sections / included stray STATUS lines.\n"
                "Rewrite and strictly follow:\n"
                "- Output each required section exactly once (no duplicate headings)\n"
                "- Do NOT output any 'Round X: ...' instruction lines\n"
                "- P1/P2 must be single-line each\n"
                "- Do NOT output a standalone 'STATUS=...' line (only the final CONSENSUS line is allowed)\n"
            )

        
        if get_summary_similarity_guard_enabled() and opponent_last:
            ratio = difflib.SequenceMatcher(None, content, opponent_last).ratio()
            if ratio >= 0.75:
                content = await _call(
                    "WARNING: your output is too similar to the opponent.\n"
                    "Rewrite:\n"
                    "- Use different wording (do not mirror sentence structure)\n"
                    "- Round>=2: add at least 2 new objections/additions not mentioned by the opponent\n"
                    "- If quoting the opponent, quote at most ONE sentence using `> quote`.\n"
                )

        
        status = _extract_status(content) or ("DISAGREE" if round_no == 1 else "DISAGREE")
        
        content_wo = _strip_after_consensus(content)
        content_wo = _CONS_RE.sub("", content_wo).rstrip()
        content_wo = _post_clean_output(content_wo)

        
        
        if round_no == 2:
            pts = _extract_objection_points(content_wo, max_points=3)
            if pts:
                content_wo = _rewrite_section(
                    content_wo,
                    SECTION_MY_OBJECTIONS,
                    [f"- P{i+1}: {p}" for i, p in enumerate(pts)],
                )

        
        if round_no == 3 and opp_points:
            injected = f"【{SECTION_OPP_OBJECTIONS}】\n" + "\n".join(
                [f"- P{i+1}: {p}" for i, p in enumerate(opp_points)]
            )
            
            content_wo = _remove_section(content_wo, SECTION_OPP_OBJECTIONS)
            content_wo = (injected + "\n\n" + content_wo.lstrip()).strip()

        final = f"{content_wo}\n\nCONSENSUS ROUND={round_no} STATUS={status}".strip()

        return Response(chat_message=TextMessage(source=self._name, content=final))

    async def on_messages_stream(self, messages, cancellation_token):  
        yield await self.on_messages(messages, cancellation_token)

    
    async def save_state(self) -> dict:  
        return {}

    async def load_state(self, state: dict) -> None:  
        return None

    async def on_reset(self, cancellation_token: CancellationToken) -> None:  
        return None

    async def on_pause(self, cancellation_token: CancellationToken) -> None:  
        return None

    async def on_resume(self, cancellation_token: CancellationToken) -> None:  
        return None

    async def close(self) -> None:  
        return None


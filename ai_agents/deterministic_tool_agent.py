from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Sequence

from autogen_agentchat.base import ChatAgent, Response
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_core import CancellationToken

from ai_agents.datadog_tools import MetricName, fetch_metrics_bundle


_METRICS_RE = re.compile(r"metrics\s*:\s*\[([^\]]+)\]", re.IGNORECASE)
_FROM_RE = re.compile(r"from_ts\s*=\s*(\d+)")
_TO_RE = re.compile(r"to_ts\s*=\s*(\d+)")
_HOST_RE = re.compile(r"host\s*:\s*([^\n\r]*)", re.IGNORECASE)


_DEFAULT_YEAR_IF_MISSING = 2026


def _last_user_text(messages: Sequence[BaseChatMessage]) -> str:
    for m in reversed(messages):
        if getattr(m, "source", None) == "user":
            c = getattr(m, "content", "")
            return c if isinstance(c, str) else str(c)
    return ""


_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _parse_time_token(s: str) -> tuple[int, int, str | None]:
    
    s = (s or "").strip().lower()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", s)
    if not m:
        raise ValueError("invalid time token")
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    ampm = m.group(3)
    return hh, mm, ampm


def _to_24h(hh: int, ampm: str | None) -> int:
    if ampm is None:
        return hh
    if ampm == "am":
        return 0 if hh == 12 else hh
    
    return hh if hh == 12 else hh + 12


def _parse_explicit_time_range_to_utc(user_text: str) -> Optional[tuple[int, int]]:
    
    t = (user_text or "").strip()
    if not t:
        return None

    
    tz = timezone.utc
    if re.search(r"\bHKT\b", t, re.IGNORECASE) or re.search(r"hong\s+kong", t, re.IGNORECASE):
        tz = timezone(timedelta(hours=8))

    
    dm = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
        r"sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?",
        t,
        re.IGNORECASE,
    )
    if not dm:
        return None
    month = _MONTHS[dm.group(1).lower()]
    day = int(dm.group(2))
    year = int(dm.group(3)) if dm.group(3) else _DEFAULT_YEAR_IF_MISSING

    
    tm = re.search(
        r"(?:from\s*)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*"
        r"(?:to|-|–|—)\s*"
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)",
        t,
        re.IGNORECASE,
    )
    if not tm:
        return None

    h1, m1, ap1 = _parse_time_token(tm.group(1))
    h2, m2, ap2 = _parse_time_token(tm.group(2))
    
    if ap2 is None:
        ap2 = ap1

    start_local = datetime(year, month, day, _to_24h(h1, ap1), m1, tzinfo=tz)
    end_local = datetime(year, month, day, _to_24h(h2, ap2), m2, tzinfo=tz)
    if end_local <= start_local:
        end_local = end_local + timedelta(days=1)

    from_ts = int(start_local.astimezone(timezone.utc).timestamp())
    to_ts = int(end_local.astimezone(timezone.utc).timestamp())
    return from_ts, to_ts


def _parse_metrics(text: str) -> List[MetricName]:
    m = _METRICS_RE.search(text)
    if not m:
        return ["cpu_usage"]
    raw = m.group(1)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    cleaned: list[str] = []
    for p in parts:
        
        cleaned.append(p.strip().strip("'\""))
    
    allowed = {
        "cpu_usage",
        "cpu_num_cores",
        "memory_used_pct",
        "memory_pct_usable",
        "disk_usage",
        "network_total",
    }
    out: list[MetricName] = []
    for c in cleaned:
        if c in allowed:
            out.append(c)  
    return out or ["cpu_usage"]


def _parse_int(text: str, regex: re.Pattern[str], default: int) -> int:
    m = regex.search(text)
    if not m:
        return default
    return int(m.group(1))


def _parse_host(text: str) -> Optional[str]:
    m = _HOST_RE.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    if not val:
        return None
    
    if val in ("[]", "none", "null"):
        return None
    return val


class DeterministicToolAgent(ChatAgent):
    

    def __init__(self, name: str = "Tool", *, default_from_ts: int, default_to_ts: int) -> None:
        self._name = name
        self._default_from_ts = int(default_from_ts)
        self._default_to_ts = int(default_to_ts)

    @property
    def name(self) -> str:  
        return self._name

    @property
    def description(self) -> str:  
        return "Deterministic tool runner for Datadog metrics (no LLM)."

    @property
    def produced_message_types(self):  
        return (TextMessage,)

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        
        last = messages[-1].content if messages else ""
        if not isinstance(last, str):
            last = str(last)
        user_text = _last_user_text(messages)

        
        metrics: List[MetricName]
        from_ts: int
        to_ts: int
        host: Optional[str]

        parsed_json = None
        try:
            
            start = last.find("{")
            end = last.rfind("}")
            if start != -1 and end != -1 and end > start:
                parsed_json = json.loads(last[start : end + 1])
        except Exception:
            parsed_json = None

        if isinstance(parsed_json, dict):
            raw_metrics = parsed_json.get("metrics") or []
            if isinstance(raw_metrics, list):
                metrics = [m for m in raw_metrics if isinstance(m, str)]  
                metrics = [m for m in metrics if m in {  
                    "cpu_usage",
                    "cpu_num_cores",
                    "memory_used_pct",
                    "memory_pct_usable",
                    "disk_usage",
                    "network_total",
                }] or ["cpu_usage"]
            else:
                metrics = ["cpu_usage"]

            from_ts = int(parsed_json.get("from_ts") or self._default_from_ts)
            to_ts = int(parsed_json.get("to_ts") or self._default_to_ts)
            host_val = parsed_json.get("host")
            host = str(host_val).strip() if host_val else None

            
            
            explicit = _parse_explicit_time_range_to_utc(user_text)
            if explicit:
                exp_from, exp_to = explicit
                try:
                    pm_year = datetime.fromtimestamp(from_ts, tz=timezone.utc).year
                    exp_year = datetime.fromtimestamp(exp_from, tz=timezone.utc).year
                    
                    if pm_year != exp_year:
                        from_ts, to_ts = exp_from, exp_to
                except Exception:
                    from_ts, to_ts = exp_from, exp_to
        else:
            
            metrics = _parse_metrics(last)
            from_ts = _parse_int(last, _FROM_RE, self._default_from_ts)
            to_ts = _parse_int(last, _TO_RE, self._default_to_ts)
            host = _parse_host(last)

        result = fetch_metrics_bundle(metrics=metrics, from_ts=from_ts, to_ts=to_ts, host=host)
        content = json.dumps(result, ensure_ascii=False, indent=2)

        return Response(chat_message=TextMessage(source=self._name, content=content))

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


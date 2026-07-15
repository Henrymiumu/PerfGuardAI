## Overview

This project uses a **Chain-of-Agents** (AutoGen AgentChat + local Ollama) to query and analyze **Datadog host performance metrics**.  
It is designed for long time-series contexts: data retrieval is deterministic, analysis is debated (A/B), and the final output is consolidated.

### Agent workflow (high level)

- **PM**: understands the user question, decides which metrics/time window to use, and either:
  - outputs a single **task JSON** for Tool (when Datadog data is needed), or
  - answers directly and ends with `TERMINATE` (when the question is just "what can you do / how does it work")
- **Tool**: deterministically calls Datadog API and returns **Tool JSON** (`stats` / `points` / `errors`)
- **Summary A / Summary B**: analyze the same Tool JSON from two angles (A = narrative, B = skeptical) and iterate in a short debate loop (A ↔ B)
- **Advice**: consolidates the latest A+B, extracts verifiable consensus facts from Tool JSON, then outputs actionable recommendations


## Requirements

- Python 3.10+
- An Ollama model running locally (OpenAI-compatible endpoint)
- Datadog API + Application keys

## Install

```bash
pip install -r requirements.txt
```

## Create a `.env` file (recommended)

1) Copy the sample:

```bash
copy env.example .env
```

2) Edit `.env` and fill in your keys and settings.

## Start Ollama (local)

Make sure Ollama is running and the OpenAI-compatible endpoint is available:

- Default endpoint: `http://localhost:11434/v1`

Example:

```bash
ollama pull llama3.2:3b
ollama serve
```

## Run (CLI)

```bash
python -m ai_agents.run_agents
```

Example questions:

- `Check CPU usage in the last 30 minutes. Any spikes?`
- `Analyze memory (RAM) usage recently. Is it close to the limit?`
- `Is disk usage trending toward full?`
- `What can you do?` (PM answers directly without calling Datadog)

## Run (UI / Streamlit)

```bash
streamlit run ui/app.py
```

## Test Data

Benchmark files are stored in `ai_agents/testdata/`.

- `synthetic_datadog_cases.json`: synthetic test cases with ground truth
- `run_numerical_benchmark.py`: benchmark runner
- `numerical_benchmark_results.json`: latest benchmark results
- `numerical_benchmark_report.md` / `full_benchmark_report.md`: generated benchmark reports

## Time window behavior

- Default time window: **last 30 minutes (UTC)** if the user does not specify a range
- If the user specifies a date without a year (e.g., "Jan 4th"), the PM assumes **year = 2026**
- If the user specifies **HKT / Hong Kong time**, the system converts it to UTC (UTC+8)
- Guardrail: the Tool agent can override incorrect PM timestamps when the user explicitly wrote a calendar date/time


## Supported metrics

The built-in Tool supports:

- `cpu_usage`, `cpu_num_cores`
- `memory_used_pct`, `memory_pct_usable`
- `disk_usage`
- `network_total`

See `ai_agents/datadog_tools.py` to extend metrics/queries.

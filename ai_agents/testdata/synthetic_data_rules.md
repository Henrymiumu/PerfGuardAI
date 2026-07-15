# Synthetic Datadog Test Data Rules

These rules define how to interpret the synthetic test payloads in `synthetic_datadog_cases.json`.

## 1) Parsing and priority rules

- Treat `series[].pointlist` values as canonical observed metric values.
- If there is any wording mismatch between `query` text and `series[].metric`/`pointlist`, prioritize `series[].metric` + `pointlist`.
- Do not reinterpret `pointlist` values into a different metric unless explicitly stated by `series[].metric`.
- Use `from_date` and `to_date` as window boundaries (milliseconds since epoch).
- Point timestamps in `pointlist` are also milliseconds since epoch.

## 2) Time and unit rules

- All timestamps are epoch milliseconds (`ms`), not seconds.
- Estimate window length from `to_date - from_date`.
- For each metric, use the unit implied by `series[].metric` and value scale:
  - `system.cpu.usage`: percent (%)
  - `system.mem.used_pct`: percent (%)
  - `system.disk.usage`: percent (%)
  - `system.net.total_bytes_per_sec`: bytes/sec

## 3) Missing and grouped data

- `null` in `pointlist` means missing telemetry, not zero.
- When multiple `series` are present (e.g., different devices), keep per-series/per-scope interpretation and avoid collapsing into a single host average unless explicitly required.

## 4) Trend vocabulary guidance

- Use `spike` for a sudden rise.
- Use `drop`/`dip` for a sudden fall.
- Avoid contradictory wording (for example, calling a clear decrease a spike).

## 5) Case-specific context

- `cpu_spike_then_recover`: one very high point followed by lower/stabilizing values.
- `memory_creeping_risk`: gradual increase over time without a single dramatic spike.
- `disk_multi_device_conflict`: same host has multiple devices with different usage levels.
- `network_missing_points_and_burst`: includes telemetry gaps (`null`) plus a short high-traffic burst.

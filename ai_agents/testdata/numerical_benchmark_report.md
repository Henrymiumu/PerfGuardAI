# Numerical Fact Extraction Benchmark

## Scoring Method

Objective: check if LLM output mentions specific numerical facts from Tool JSON.

| Result | Criteria | Score weight |
|--|--|--|
| FULL | Correct value AND timestamp both mentioned (within tolerance) | 1.0 |
| PARTIAL | Value OR timestamp mentioned, but not both | 0.5 |
| MISS | Neither value nor timestamp found in output | 0.0 |

Value tolerance: ±5% | Timestamp tolerance: ±600,000 ms (1 data point interval)

## Overall Results

| System | Avg Weighted Accuracy |
|--|--|
| Single LLM | 71.9% |
| Multi-LLM  | 90.7% |

## Case: cpu_spike_then_recover
*CPU spike then recovery over extended window*

| System | Weighted | FULL | PARTIAL | MISS | Time |
|--|--|--|--|--|--|
| Single | 75.0% | 1 | 1 | 0 | 7.2s |
| Multi  | 100.0% | 2 | 0 | 0 | 36.09s |
| **Winner** | | | | | **MULTI** |

### Fact Breakdown

| Fact | Type | Single | Multi |
|--|--|--|--|
| Maximum system.cpu.usage value is 100.0 at 17:33:00 UTC (epo | max_value | FULL | FULL |
| Minimum system.cpu.usage value is 8.0 at 17:57:00 UTC (epoch | min_value | PARTIAL | FULL |

## Case: memory_creeping_risk
*Memory usage gradually increases over extended window*

| System | Weighted | FULL | PARTIAL | MISS | Time |
|--|--|--|--|--|--|
| Single | 75.0% | 1 | 1 | 0 | 4.05s |
| Multi  | 100.0% | 2 | 0 | 0 | 43.5s |
| **Winner** | | | | | **MULTI** |

### Fact Breakdown

| Fact | Type | Single | Multi |
|--|--|--|--|
| Maximum system.mem.used_pct value is 96.0 at 19:37:30 UTC (e | max_value | PARTIAL | FULL |
| Minimum system.mem.used_pct value is 55.0 at 18:00:00 UTC (e | min_value | FULL | FULL |

## Case: disk_multi_device_conflict
*Disk usage differs by device over extended window*

| System | Weighted | FULL | PARTIAL | MISS | Time |
|--|--|--|--|--|--|
| Single | 100.0% | 4 | 0 | 0 | 5.74s |
| Multi  | 100.0% | 4 | 0 | 0 | 96.18s |
| **Winner** | | | | | **TIE** |

### Fact Breakdown

| Fact | Type | Single | Multi |
|--|--|--|--|
| Maximum system.disk.usage value is 99.0 at 20:51:00 UTC (epo | max_value | FULL | FULL |
| Minimum system.disk.usage value is 88.0 at 19:00:00 UTC (epo | min_value | FULL | FULL |
| Maximum system.disk.usage value is 42.0 at 19:03:00 UTC (epo | max_value | FULL | FULL |
| Minimum system.disk.usage value is 38.0 at 19:00:00 UTC (epo | min_value | FULL | FULL |

## Case: network_missing_points_and_burst
*Network has missing points and bursts over extended window*

| System | Weighted | FULL | PARTIAL | MISS | Time |
|--|--|--|--|--|--|
| Single | 75.0% | 2 | 2 | 0 | 7.78s |
| Multi  | 87.5% | 3 | 1 | 0 | 53.31s |
| **Winner** | | | | | **MULTI** |

### Fact Breakdown

| Fact | Type | Single | Multi |
|--|--|--|--|
| Maximum system.net.total_bytes_per_sec value is 238000.0 at  | max_value | FULL | FULL |
| Minimum system.net.total_bytes_per_sec value is 12000.0 at 2 | min_value | PARTIAL | FULL |
| Turning point (burst_start) at 20:43:30 UTC with value 19000 | turning_point | FULL | FULL |
| Turning point (burst_end) at 20:54:00 UTC with value 14500.0 | turning_point | PARTIAL | PARTIAL |

## Case: cpu_dual_spike
*CPU has two distinct spike events (75 points, 90-sec interval)*

| System | Weighted | FULL | PARTIAL | MISS | Time |
|--|--|--|--|--|--|
| Single | 50.0% | 2 | 1 | 2 | 7.67s |
| Multi  | 80.0% | 3 | 2 | 0 | 59.14s |
| **Winner** | | | | | **MULTI** |

### Fact Breakdown

| Fact | Type | Single | Multi |
|--|--|--|--|
| Maximum system.cpu.usage value is 95.0 at 23:10:00 UTC (epoc | max_value | FULL | FULL |
| Minimum system.cpu.usage value is 26.0 at 23:59:30 UTC (epoc | min_value | PARTIAL | PARTIAL |
| Turning point (peak) at 23:10:00 UTC with value 95.0 (scope: | turning_point | FULL | FULL |
| Turning point (valley) at 23:46:00 UTC with value 29.0 (scop | turning_point | MISS | PARTIAL |
| Turning point (peak) at 23:47:30 UTC with value 68.0 (scope: | turning_point | MISS | FULL |

## Case: memory_sudden_collapse
*Memory pct_usable collapses suddenly then partially recovers*

| System | Weighted | FULL | PARTIAL | MISS | Time |
|--|--|--|--|--|--|
| Single | 50.0% | 1 | 1 | 1 | 7.58s |
| Multi  | 83.3% | 2 | 1 | 0 | 62.45s |
| **Winner** | | | | | **MULTI** |

### Fact Breakdown

| Fact | Type | Single | Multi |
|--|--|--|--|
| Maximum system.mem.pct_usable value is 0.62 at 01:29:40 UTC  | max_value | MISS | PARTIAL |
| Minimum system.mem.pct_usable value is 0.17 at 01:59:40 UTC  | min_value | FULL | FULL |
| Turning point (peak) at 01:55:10 UTC with value 0.62 (scope: | turning_point | PARTIAL | FULL |

## Case: disk_two_devices
*Disk usage: C drive gradual fill, D drive sudden explosion*

| System | Weighted | FULL | PARTIAL | MISS | Time |
|--|--|--|--|--|--|
| Single | 100.0% | 4 | 0 | 0 | 8.92s |
| Multi  | 100.0% | 4 | 0 | 0 | 83.9s |
| **Winner** | | | | | **TIE** |

### Fact Breakdown

| Fact | Type | Single | Multi |
|--|--|--|--|
| Maximum system.disk.usage value is 73.0 at 06:04:20 UTC (epo | max_value | FULL | FULL |
| Minimum system.disk.usage value is 68.0 at 04:13:20 UTC (epo | min_value | FULL | FULL |
| Maximum system.disk.usage value is 91.7 at 05:23:50 UTC (epo | max_value | FULL | FULL |
| Minimum system.disk.usage value is 21.8 at 04:14:50 UTC (epo | min_value | FULL | FULL |

## Case: network_gradual_surge
*Network traffic gradual build-up then sudden peak and drop*

| System | Weighted | FULL | PARTIAL | MISS | Time |
|--|--|--|--|--|--|
| Single | 50.0% | 1 | 0 | 1 | 8.23s |
| Multi  | 75.0% | 1 | 1 | 0 | 72.7s |
| **Winner** | | | | | **MULTI** |

### Fact Breakdown

| Fact | Type | Single | Multi |
|--|--|--|--|
| Maximum system.net.total_bytes_per_sec value is 196000.0 at  | max_value | MISS | PARTIAL |
| Minimum system.net.total_bytes_per_sec value is 3200.0 at 07 | min_value | FULL | FULL |

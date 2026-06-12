#!/usr/bin/env python3
"""
Fetch health data from InfluxDB for LED matrix dashboards.
Uses the health-query skill's influx_query.sh script.

Data fetched:
  - cycling_power (Avg) → avg_power_watts
  - active_energy (qty sum) → active_calories
  - basal_energy_burned (qty sum) → basal_calories  
  - dietary_energy (qty sum) → calories_in
  - protein (qty sum) → protein_grams
  - sleep_analysis (totalSleep) → sleep_hours
"""

import subprocess
import sys
from datetime import datetime
from typing import Optional, Dict, Any

QUERY_SCRIPT = "/home/drew/.openclaw/workspace/skills/health-query/scripts/influx_query.sh"


def sf(val, default=0.0):
    """Safe float conversion."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def influx_query(flux: str) -> Optional[Dict[str, str]]:
    """Run a Flux query against the health bucket. Returns parsed row dict or None."""
    try:
        result = subprocess.run(
            [QUERY_SCRIPT, flux],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            print(f"[fetch] query error (rc={result.returncode}): {result.stderr.strip()[:200]}", file=sys.stderr)
            return None

        lines = result.stdout.strip().split('\n')
        if len(lines) < 2:
            return None

        data_lines = []
        header = ""
        for line in lines:
            if line.startswith('#group') or line.startswith('#datatype') or line.startswith('#default'):
                continue
            if not header and line:
                header = line
                continue
            if line and not line.startswith('#'):
                data_lines.append(line)

        if not header or not data_lines:
            return None

        cols = header.split(',')
        vals = data_lines[-1].split(',')
        return {cols[i]: vals[i] for i in range(min(len(cols), len(vals)))}
    except Exception as e:
        print(f"[fetch] exception: {e}", file=sys.stderr)
        return None


# ──────────────────────────────────────────────────
# Individual metric fetchers
# ──────────────────────────────────────────────────

def get_avg_power_watts(lookback: str = "1d") -> Optional[float]:
    """Average cycling power in watts for the period."""
    flux = (
        f'from(bucket:"health")'
        f'|> range(start: -{lookback})'
        f'|> filter(fn: (r) => r["_measurement"] == "cycling_power")'
        f'|> filter(fn: (r) => r["_field"] == "Avg")'
        f'|> last()'
    )
    row = influx_query(flux)
    if row:
        return sf(row.get("_value"))
    return None


def get_active_energy(lookback: str = "1d") -> float:
    """Active energy burned (kcal) summed over the period."""
    flux = (
        f'from(bucket:"health")'
        f'|> range(start: -{lookback})'
        f'|> filter(fn: (r) => r["_measurement"] == "active_energy")'
        f'|> filter(fn: (r) => r["_field"] == "qty")'
        f'|> sum()'
    )
    row = influx_query(flux)
    return sf(row.get("_value")) if row else 0.0


def get_basal_energy(lookback: str = "1d") -> float:
    """Basal (resting) energy burned (kcal) summed over the period."""
    flux = (
        f'from(bucket:"health")'
        f'|> range(start: -{lookback})'
        f'|> filter(fn: (r) => r["_measurement"] == "basal_energy_burned")'
        f'|> filter(fn: (r) => r["_field"] == "qty")'
        f'|> sum()'
    )
    row = influx_query(flux)
    return sf(row.get("_value")) if row else 0.0


def get_calories_in(lookback: str = "1d") -> float:
    """Dietary energy consumed (kcal) summed over the period."""
    flux = (
        f'from(bucket:"health")'
        f'|> range(start: -{lookback})'
        f'|> filter(fn: (r) => r["_measurement"] == "dietary_energy")'
        f'|> filter(fn: (r) => r["_field"] == "qty")'
        f'|> sum()'
    )
    row = influx_query(flux)
    return sf(row.get("_value")) if row else 0.0


def get_protein(lookback: str = "1d") -> float:
    """Protein consumed (grams) summed over the period."""
    flux = (
        f'from(bucket:"health")'
        f'|> range(start: -{lookback})'
        f'|> filter(fn: (r) => r["_measurement"] == "protein")'
        f'|> filter(fn: (r) => r["_field"] == "qty")'
        f'|> sum()'
    )
    row = influx_query(flux)
    return sf(row.get("_value")) if row else 0.0


def get_sleep(lookback: str = "2d") -> Optional[Dict[str, float]]:
    """Sleep analysis for the last night."""
    flux = (
        f'from(bucket:"health")'
        f'|> range(start: -{lookback})'
        f'|> filter(fn: (r) => r["_measurement"] == "sleep_analysis")'
        f'|> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'
        f'|> last()'
    )
    row = influx_query(flux)
    if not row:
        return None
    return {
        "total": sf(row.get("totalSleep")),
        "deep": sf(row.get("deep")),
        "rem": sf(row.get("rem")),
        "core": sf(row.get("core")),
        "awake": sf(row.get("awake")),
    }


# ──────────────────────────────────────────────────
# Unified fetcher — returns all dashboard data
# ──────────────────────────────────────────────────

def fetch_all(lookback: str = "1d") -> Dict[str, Any]:
    """Fetch all health data needed for the 5 LED dashboards."""
    sleep = get_sleep("2d")
    active_cal = get_active_energy(lookback)
    basal_cal = get_basal_energy(lookback)
    cals_in = get_calories_in(lookback)
    protein = get_protein(lookback)
    avg_power = get_avg_power_watts(lookback)
    
    calories_out = active_cal + basal_cal
    net_calories = cals_in - calories_out
    
    sleep_hours = sleep["total"] if sleep else 0.0
    
    data = {
        "timestamp": datetime.now().isoformat(),
        "avg_power_watts": avg_power if avg_power is not None else 0.0,
        "active_calories": active_cal,
        "basal_calories": basal_cal,
        "calories_out": calories_out,
        "calories_in": cals_in,
        "net_calories": net_calories,
        "protein_grams": protein,
        "sleep_hours": sleep_hours,
        "sleep_detail": sleep or {"total": 0, "deep": 0, "rem": 0, "core": 0, "awake": 0},
    }
    
    print(f"[fetch] {datetime.now().strftime('%H:%M:%S')} — "
          f"PWR:{data['avg_power_watts']:.0f}W "
          f"IN:{data['calories_in']:.0f}kcal "
          f"OUT:{data['calories_out']:.0f}kcal "
          f"NET:{data['net_calories']:+.0f}kcal "
          f"PRO:{data['protein_grams']:.0f}g "
          f"SLP:{data['sleep_hours']:.1f}h",
          file=sys.stderr)
    
    return data


if __name__ == "__main__":
    import json
    data = fetch_all()
    # Convert to serializable
    print(json.dumps(data, indent=2, default=str))

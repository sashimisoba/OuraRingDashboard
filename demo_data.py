"""Sample data in the same shape as the Oura API v2 responses.

Used by `python3 server.py --demo` so the dashboard can be tried without an
Oura account. Each day is generated from its own seed, so a given day always
looks the same whatever range is requested.
"""
from __future__ import annotations

import datetime as dt
import math
import random

AGE = 38
BASE_HRV = 46
BASE_RHR = 53
PHASE_DEEP, PHASE_LIGHT, PHASE_REM, PHASE_AWAKE = "1", "2", "3", "4"


def _tz():
    return dt.datetime.now().astimezone().tzinfo


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _rng(*key) -> random.Random:
    return random.Random("|".join(str(k) for k in key))


def _phases(rng: random.Random, epochs: int, latency_epochs: int) -> str:
    """5-minute hypnogram: ~90 min cycles, more deep sleep early and more REM late."""
    out = [PHASE_AWAKE] * latency_epochs
    cycle = 0
    while len(out) < epochs:
        late = cycle / 4
        deep = max(0, round(rng.gauss(4.5 - 3.5 * late, 1.2)))
        rem = max(1, round(rng.gauss(2 + 3.5 * late, 1)))
        light_a = max(2, round(rng.gauss(5, 1.5)))
        light_b = max(1, round(rng.gauss(3, 1)))
        block = [PHASE_LIGHT] * light_a + [PHASE_DEEP] * deep + [PHASE_LIGHT] * light_b + [PHASE_REM] * rem
        if rng.random() < 0.55:
            block += [PHASE_AWAKE] * rng.randint(1, 3)
        out.extend(block)
        cycle += 1
    out = out[:epochs]
    out[-1] = PHASE_AWAKE
    return "".join(out)


def _rough_night(day: dt.date) -> int:
    """About one night in six is short and restless (late night, alcohol, travel)."""
    return 1 if _rng("rough", day).random() < 0.17 else 0


def _sleep(day: dt.date, tz) -> dict:
    rng = _rng("sleep", day)
    weekend = day.weekday() >= 5
    rough = _rough_night(day)
    bed_minutes = rng.gauss(23 * 60 + (40 if weekend else 15) + rough * 60, 30)
    start = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(0, 0), tz) + dt.timedelta(minutes=bed_minutes)
    start = start.replace(second=0) - dt.timedelta(minutes=start.minute % 5)
    in_bed_min = _clamp(rng.gauss((470 if weekend else 445) - rough * 70, 40), 300, 590)
    epochs = int(in_bed_min // 5)
    latency = _clamp(round(rng.gauss(3, 1.5)), 1, 8)
    phases = _phases(rng, epochs, latency)
    count = {p: phases.count(p) * 300 for p in "1234"}
    total = count["1"] + count["2"] + count["3"]
    end = start + dt.timedelta(minutes=epochs * 5)

    hrv_level = BASE_HRV + rng.gauss(0, 6) - rough * 12
    rhr_level = BASE_RHR + rng.gauss(0, 2) + rough * 4
    hr_items, hrv_items = [], []
    for i in range(epochs):
        x = i / max(epochs - 1, 1)
        dip = math.sin(math.pi * min(x / 0.75, 1)) if x < 0.75 else 1 - (x - 0.75) / 0.25 * 0.6
        bpm = rhr_level + 9 * (1 - dip) + rng.gauss(0, 1.3) + (4 if phases[i] == PHASE_AWAKE else 0)
        hr_items.append(None if rng.random() < 0.02 else round(bpm, 1))
        hrv = hrv_level * (0.75 + 0.45 * dip) + rng.gauss(0, 6) - (8 if phases[i] == PHASE_AWAKE else 0)
        hrv_items.append(None if rng.random() < 0.03 else round(max(hrv, 8)))
    hr_valid = [v for v in hr_items if v is not None]
    hrv_valid = [v for v in hrv_items if v is not None]

    return {
        "id": f"demo-sleep-{day}",
        "average_breath": round(rng.gauss(14.6, 0.5), 3),
        "average_heart_rate": round(sum(hr_valid) / len(hr_valid), 3),
        "average_hrv": round(sum(hrv_valid) / len(hrv_valid)),
        "awake_time": count["4"],
        "bedtime_end": _iso(end),
        "bedtime_start": _iso(start),
        "day": day.isoformat(),
        "deep_sleep_duration": count["1"],
        "efficiency": round(total / (epochs * 300) * 100),
        "heart_rate": {"interval": 300.0, "items": hr_items, "timestamp": _iso(start)},
        "hrv": {"interval": 300.0, "items": hrv_items, "timestamp": _iso(start)},
        "latency": latency * 300,
        "light_sleep_duration": count["2"],
        "low_battery_alert": False,
        "lowest_heart_rate": round(min(hr_valid)),
        "movement_30_sec": None,
        "period": 0,
        "readiness": None,
        "readiness_score_delta": None,
        "rem_sleep_duration": count["3"],
        "restless_periods": rng.randint(80, 260),
        "sleep_algorithm_version": "v2",
        "sleep_analysis_reason": "foreground_sleep_analysis",
        "sleep_phase_30_sec": None,
        "sleep_phase_5_min": phases,
        "sleep_score_delta": None,
        "time_in_bed": epochs * 300,
        "total_sleep_duration": total,
        "type": "long_sleep",
        "ring_id": "demo-ring",
        "app_sleep_phase_5_min": phases,
    }


def _nap(day: dt.date, tz) -> dict | None:
    rng = _rng("nap", day)
    if rng.random() > 0.12:
        return None
    start = dt.datetime.combine(day, dt.time(14, 0), tz) + dt.timedelta(minutes=rng.randint(0, 90))
    epochs = rng.randint(4, 8)
    phases = PHASE_AWAKE + PHASE_LIGHT * (epochs - 2) + PHASE_AWAKE
    return {
        "id": f"demo-nap-{day}",
        "bedtime_start": _iso(start),
        "bedtime_end": _iso(start + dt.timedelta(minutes=epochs * 5)),
        "day": day.isoformat(),
        "type": "late_nap",
        "period": 1,
        "low_battery_alert": False,
        "time_in_bed": epochs * 300,
        "total_sleep_duration": (epochs - 2) * 300,
        "light_sleep_duration": (epochs - 2) * 300,
        "deep_sleep_duration": 0,
        "rem_sleep_duration": 0,
        "awake_time": 600,
        "sleep_phase_5_min": phases,
    }


def _stretch(mean: float) -> float:
    return 79 + (mean - 85) * 1.8


def _score(rng, center, spread=4):
    return int(_clamp(round(center + rng.gauss(0, spread)), 30, 100))


def _activity(day: dt.date, tz, prev_steps: int) -> dict:
    rng = _rng("activity", day)
    weekend = day.weekday() >= 5
    steps = int(_clamp(rng.gauss(10500 if weekend else 8200, 2800), 1800, 23000))
    active_cal = int(steps * 0.042 + rng.gauss(60, 40))
    high = int(_clamp(rng.gauss(900 if weekend else 500, 500), 0, 5400))
    medium = int(_clamp(rng.gauss(3600, 1200), 600, 9000))
    low = int(_clamp(rng.gauss(13000, 2500), 5000, 20000))
    sedentary = int(_clamp(rng.gauss(30000, 4000), 18000, 40000))
    score_center = 60 + steps / 400 + high / 300 - abs(prev_steps - steps) / 2000
    return {
        "id": f"demo-activity-{day}",
        "active_calories": active_cal,
        "average_met_minutes": round(1.3 + steps / 30000, 4),
        "class_5_min": None,
        "contributors": {
            "meet_daily_targets": _score(rng, 55 + steps / 300, 10),
            "move_every_hour": _score(rng, 88, 8),
            "recovery_time": _score(rng, 90, 6),
            "stay_active": _score(rng, 70 + (steps - 8000) / 400, 8),
            "training_frequency": _score(rng, 75, 10),
            "training_volume": _score(rng, 82, 8),
        },
        "day": day.isoformat(),
        "equivalent_walking_distance": int(steps * 0.78),
        "high_activity_met_minutes": high // 60 * 8,
        "high_activity_time": high,
        "inactivity_alerts": rng.randint(0, 3),
        "low_activity_met_minutes": low // 60 * 2,
        "low_activity_time": low,
        "medium_activity_met_minutes": medium // 60 * 4,
        "medium_activity_time": medium,
        "met": {"interval": 60.0, "items": [], "timestamp": _iso(dt.datetime.combine(day, dt.time(4, 0), tz))},
        "meters_to_target": max(0, 7000 - int(steps * 0.78)),
        "non_wear_time": int(_clamp(rng.gauss(1800, 1200), 0, 7200)),
        "resting_time": int(_clamp(rng.gauss(30000, 2500), 20000, 38000)),
        "score": _score(rng, score_center, 4),
        "sedentary_met_minutes": sedentary // 60,
        "sedentary_time": sedentary,
        "steps": steps,
        "target_calories": 450,
        "target_meters": 7000,
        "timestamp": _iso(dt.datetime.combine(day, dt.time(4, 0), tz)),
        "total_calories": 1950 + active_cal,
    }


def _workouts(day: dt.date, tz) -> list:
    rng = _rng("workout", day)
    if rng.random() > 0.45:
        return []
    kind, speed, kcal_per_min = rng.choice(
        [("walking", 80, 4.5), ("running", 165, 11), ("cycling", 330, 8), ("strengthTraining", 0, 6), ("yoga", 0, 3)]
    )
    minutes = int(_clamp(rng.gauss(40, 15), 15, 95))
    start = dt.datetime.combine(day, dt.time(7, 0), tz) + dt.timedelta(minutes=rng.choice([0, 30, 600, 660, 720]))
    return [
        {
            "id": f"demo-workout-{day}",
            "activity": kind,
            "calories": round(minutes * kcal_per_min + rng.gauss(0, 20), 1),
            "day": day.isoformat(),
            "distance": round(minutes * speed * rng.uniform(0.9, 1.1), 1) if speed else None,
            "end_datetime": _iso(start + dt.timedelta(minutes=minutes)),
            "intensity": rng.choice(["easy", "moderate", "moderate", "hard"]),
            "label": None,
            "source": rng.choice(["autodetected", "confirmed", "manual"]),
            "start_datetime": _iso(start),
        }
    ]


def _heartrate(now: dt.datetime) -> list:
    """Last 24 hours of 5-minute heart rate samples (UTC timestamps)."""
    rows = []
    local_tz = _tz()
    t = (now - dt.timedelta(hours=24)).replace(second=0, microsecond=0)
    t -= dt.timedelta(minutes=t.minute % 5)
    while t <= now:
        local = t.astimezone(local_tz)
        rng = _rng("hr", t.isoformat())
        h = local.hour + local.minute / 60
        if h >= 23.5 or h < 6.75:
            source, bpm = "sleep", BASE_RHR + 4 + 4 * math.cos((h if h < 12 else h - 24) / 7 * math.pi) + rng.gauss(0, 1.5)
        elif 18 <= h < 18.75 and local.weekday() in (1, 3, 5):
            source, bpm = "workout", 128 + 20 * math.sin((h - 18) / 0.75 * math.pi) + rng.gauss(0, 4)
        else:
            source = "awake"
            bpm = 72 + 8 * math.sin((h - 9) / 12 * math.pi) + rng.gauss(0, 5) + (12 if rng.random() < 0.05 else 0)
        if rng.random() > 0.08:  # the ring skips some samples
            ts = t.astimezone(dt.timezone.utc)
            rows.append(
                {"timestamp": ts.isoformat().replace("+00:00", "Z"), "timestamp_unix": int(ts.timestamp() * 1000), "bpm": int(round(bpm)), "source": source}
            )
        t += dt.timedelta(minutes=5)
    return rows


def generate(start: dt.date, end: dt.date, now: dt.datetime | None = None) -> dict:
    tz = _tz()
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(tz).date()
    end = min(end, today)
    data = {k: [] for k in (
        "daily_readiness", "daily_sleep", "daily_activity", "sleep", "daily_stress", "daily_spo2",
        "daily_resilience", "daily_cardiovascular_age", "vO2_max", "workout",
    )}

    prev_steps = 8000
    day = start
    while day <= end:
        rng = _rng("daily", day)
        stamp = _iso(dt.datetime.combine(day, dt.time(0, 0), tz))
        sleep = _sleep(day, tz)
        data["sleep"].append(sleep)
        nap = _nap(day, tz)
        if nap:
            data["sleep"].append(nap)

        hours = sleep["total_sleep_duration"] / 3600
        deep_share = sleep["deep_sleep_duration"] / max(sleep["total_sleep_duration"], 1)
        rem_share = sleep["rem_sleep_duration"] / max(sleep["total_sleep_duration"], 1)
        sleep_contrib = {
            "deep_sleep": _score(rng, 45 + deep_share * 260, 5),
            "efficiency": _score(rng, sleep["efficiency"] - 4, 3),
            "latency": _score(rng, 95 - sleep["latency"] / 120, 4),
            "rem_sleep": _score(rng, 35 + rem_share * 240, 5),
            "restfulness": _score(rng, 78, 8),
            "timing": _score(rng, 88, 7),
            "total_sleep": _score(rng, hours * 12, 4),
        }
        # Averaging contributors flattens the spread, so stretch it out.
        sleep_score = _score(rng, _stretch(sum(sleep_contrib.values()) / len(sleep_contrib)), 2)
        data["daily_sleep"].append(
            {"id": f"demo-dsleep-{day}", "contributors": sleep_contrib, "day": day.isoformat(), "score": sleep_score, "timestamp": stamp}
        )

        activity = None
        if day < today or now.astimezone(tz).hour >= 9:
            activity = _activity(day, tz, prev_steps)
            prev_steps = activity["steps"]
            data["daily_activity"].append(activity)

        hrv_delta = sleep["average_hrv"] - BASE_HRV
        rhr_delta = sleep["lowest_heart_rate"] - BASE_RHR
        temp = round(rng.gauss(0, 0.22), 2)
        readiness_contrib = {
            "activity_balance": _score(rng, 82, 6),
            "body_temperature": _score(rng, 98 - abs(temp) * 60, 2),
            "hrv_balance": _score(rng, 78 + hrv_delta * 0.9, 5),
            "previous_day_activity": _score(rng, 85, 8),
            "previous_night": _score(rng, sleep_score, 3),
            "recovery_index": _score(rng, 80 - rhr_delta * 3, 6),
            "resting_heart_rate": _score(rng, 88 - rhr_delta * 4, 4),
            "sleep_balance": _score(rng, 80, 5),
            "sleep_regularity": _score(rng, 84, 6),
        }
        readiness = _score(rng, _stretch(sum(readiness_contrib.values()) / len(readiness_contrib)), 2)
        data["daily_readiness"].append(
            {
                "id": f"demo-readiness-{day}",
                "contributors": readiness_contrib,
                "day": day.isoformat(),
                "score": readiness,
                "temperature_deviation": temp,
                "temperature_trend_deviation": round(temp * 0.6 + rng.gauss(0, 0.05), 2),
                "timestamp": stamp,
            }
        )

        if activity:
            stress_high = int(_clamp(rng.gauss(4800 + (activity["high_activity_time"] < 300) * 1500, 2400), 0, 14400)) // 900 * 900
            recovery_high = int(_clamp(rng.gauss(3600 + (readiness - 75) * 120, 1800), 0, 12600)) // 900 * 900
            summary = "stressful" if stress_high > recovery_high * 2 else "restored" if recovery_high > stress_high * 1.5 else "normal"
            data["daily_stress"].append(
                {"id": f"demo-stress-{day}", "day": day.isoformat(), "day_summary": summary, "recovery_high": recovery_high, "stress_high": stress_high}
            )

        data["daily_spo2"].append(
            {
                "id": f"demo-spo2-{day}",
                "breathing_disturbance_index": int(_clamp(rng.gauss(4, 2), 0, 15)),
                "day": day.isoformat(),
                "spo2_percentage": {"average": round(_clamp(rng.gauss(97.2, 0.6), 94, 99.5), 3)},
            }
        )
        data["daily_resilience"].append(
            {
                "id": f"demo-resilience-{day}",
                "day": day.isoformat(),
                "contributors": {
                    "sleep_recovery": round(_clamp(rng.gauss(68, 8), 0, 100), 1),
                    "daytime_recovery": round(_clamp(rng.gauss(44, 10), 0, 100), 1),
                    "stress": round(_clamp(rng.gauss(55, 9), 0, 100), 1),
                },
                "level": rng.choice(["adequate", "solid", "solid", "strong"]),
            }
        )
        data["daily_cardiovascular_age"].append(
            {"id": f"demo-cva-{day}", "day": day.isoformat(), "pulse_wave_velocity": round(rng.gauss(7.1, 0.15), 2), "vascular_age": AGE - 2 + rng.randint(-1, 1)}
        )
        if day.toordinal() % 7 == 0:
            data["vO2_max"].append({"id": f"demo-vo2-{day}", "day": day.isoformat(), "timestamp": stamp, "vo2_max": 41 + rng.randint(-1, 2)})
        data["workout"].extend(_workouts(day, tz))
        day += dt.timedelta(days=1)

    return {
        "personal_info": {"id": "demo-user", "age": AGE, "weight": 68.0, "height": 1.72, "biological_sex": "male", "email": None},
        "data": data,
        "heartrate": _heartrate(now),
    }

from __future__ import annotations
from flask import Flask, jsonify, request, send_from_directory
import os
import json
import datetime as dt
from typing import Dict, List

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FOOD_FILE = os.path.join(APP_DIR, "foods_cut.json")
DAY_LOG_FILE = os.path.join(APP_DIR, "meals_day.json")

app = Flask(__name__, static_folder=None)

# ---------------- Utils ----------------


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sanitize_foods(db: Dict[str, dict]) -> Dict[str, dict]:
    req = ["kcal", "protein", "carbs", "fat"]
    out = {}
    for name, meta in db.items():
        if not isinstance(meta, dict):
            continue
        if "kcal" not in meta and "kkal" in meta:
            meta["kcal"] = meta.pop("kkal")
        for k in req:
            try:
                meta[k] = float(meta.get(k, 0))
            except Exception:
                meta[k] = 0.0
        out[name] = meta
    return out


FOODS_RAW = load_json(FOOD_FILE, {})
FOODS = sanitize_foods(FOODS_RAW)


def tdee(profile=None):
    p = profile or {"sex": "male", "age": 25, "height_cm": 175,
                    "weight_kg": 70, "activity": "moderate"}
    base = 10*p["weight_kg"] + 6.25*p["height_cm"] - \
        5*p["age"] + (5 if p["sex"] == "male" else -161)
    # For Men:{BMR} = 10 * {weight(kg)} + 6.25 * {height(cm)} - 5 * {age} + 5
    # For Women:{BMR} = 10 * {weight(kg)} + 6.25 * {height(cm)} - 5 * {age} - 161
    factor = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55,
              "active": 1.725}.get(p["activity"], 1.55)
    cut_adj = 0.85
    day_kcal = int(round(base * factor * cut_adj))  # 基础代谢率 × 活动系数 × 热量调整系数
    macro = {
        "protein": int(round(day_kcal*0.25/4)),
        "carbs":   int(round(day_kcal*0.50/4)),
        "fat":     int(round(day_kcal*0.25/9)),
    }
    return day_kcal, macro


DAY_KCAL, DAY_MACRO = tdee()


def ensure_today_entry(history: List[dict]) -> dict:
    """Get today's entry; if not exists, create a new day skeleton."""
    today = dt.date.today().isoformat()
    if history and history[-1].get("date") == today:
        return history[-1]
    # create new empty day
    day = {"date": today, "day_number": len(history)+1,
           "meals": {"Breakfast": {"items": []},
                     "Lunch": {"items": []},
                     "Dinner": {"items": []},
                     "Snack": {"items": []}},
           "totals": {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0}}
    history.append(day)
    return day


def sum_items(items: List[dict]) -> dict:
    t = {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0}
    for it in items:
        n = it["name"]
        portion = float(it.get("portion", 1))
        meta = FOODS.get(n)
        if not meta:
            continue
        t["kcal"] += meta["kcal"] * portion
        t["protein"] += meta["protein"] * portion
        t["carbs"] += meta["carbs"] * portion
        t["fat"] += meta["fat"] * portion
    return t


def recompute_day_totals(day: dict):
    total = {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0}
    for name in ["Breakfast", "Lunch", "Dinner", "Snack"]:
        items = day["meals"].get(name, {}).get("items", [])
        t = sum_items(items)
        day["meals"][name]["totals"] = {k: round(v, 2) for k, v in t.items()}
        for k in total:
            total[k] += t[k]
    day["totals"] = {k: int(round(v)) for k, v in total.items()}


def build_day_report(day: dict) -> str:
    lines = []
    lines.append(
        f"LeanCut Daily Report — Day {day.get('day_number', '?')} ({day.get('date', '')})")
    lines.append(
        f"Target: {DAY_KCAL} kcal | P{DAY_MACRO['protein']} C{DAY_MACRO['carbs']} F{DAY_MACRO['fat']}")
    lines.append("-"*40)
    for meal in ["Breakfast", "Lunch", "Dinner", "Snack"]:
        info = day["meals"].get(meal, {"items": []})
        items = info.get("items", [])
        if not items:
            lines.append(f"{meal}: (skipped)")
            continue
        lines.append(f"{meal}:")
        for it in items:
            meta = FOODS.get(
                it["name"], {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0})
            lines.append(
                f"  - {it['name']} x{it['portion']} \u2192 {int(meta['kcal'])} kcal P{int(meta['protein'])} C{int(meta['carbs'])} F{int(meta['fat'])}")
    lines.append("-"*40)
    t = day["totals"]
    lines.append(
        f"Total: {t['kcal']} kcal | P{t['protein']} C{t['carbs']} F{t['fat']}")
    return "\n".join(lines)

# ---------------- Routes ----------------


@app.route("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.route("/api/foods")
def api_foods():
    foods = [{"name": n, **{k: int(round(v)) for k, v in m.items() if k in ("kcal", "protein", "carbs", "fat")}}
             for n, m in FOODS.items()]
    foods.sort(key=lambda x: x["name"].lower())
    return jsonify({"foods": foods})


@app.route("/api/state")
def api_state():
    hist = load_json(DAY_LOG_FILE, [])
    today = ensure_today_entry(hist)  # create if missing (doesn't save yet)
    return jsonify({
        "today": today["date"],
        "dayNumber": today["day_number"],
        "target": {"kcal": DAY_KCAL, **DAY_MACRO}
    })


@app.route("/api/meal", methods=["POST"])
def api_meal():
    data = request.get_json(force=True) or {}
    meal = (data.get("meal") or "").strip()
    items = data.get("items", [])
    mode = (data.get("mode") or "replace").lower()
    if meal not in {"Breakfast", "Lunch", "Dinner", "Snack"}:
        return jsonify({"ok": False, "error": "invalid meal"}), 400
    # load day
    hist = load_json(DAY_LOG_FILE, [])
    day = ensure_today_entry(hist)
    cur = day["meals"].get(meal, {"items": []})

    if mode == "append":
        # merge by name
        by_name = {it["name"]: it for it in cur["items"]}
        for it in items:
            nm = it["name"]
            pt = float(it.get("portion", 1))
            if nm in by_name:
                by_name[nm]["portion"] = float(
                    by_name[nm].get("portion", 1)) + pt
            else:
                cur["items"].append({"name": nm, "portion": pt})
        day["meals"][meal] = cur
    else:  # replace
        day["meals"][meal] = {"items": [
            {"name": it["name"], "portion": float(it.get("portion", 1))} for it in items]}

    # recompute and persist
    recompute_day_totals(day)
    save_json(DAY_LOG_FILE, hist)

    return jsonify({"ok": True, "totals": day["meals"][meal]["totals"]})


@app.route("/api/report/day", methods=["POST"])
def api_report_day():
    hist = load_json(DAY_LOG_FILE, [])
    if not hist:
        return jsonify({"ok": False, "report": "No records."})
    day = ensure_today_entry(hist)
    # ensure totals up to date
    recompute_day_totals(day)
    save_json(DAY_LOG_FILE, hist)
    return jsonify({"ok": True, "report": build_day_report(day)})


# ------------- Run -------------
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)

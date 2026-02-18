from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Tuple

from dateutil.relativedelta import relativedelta
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "wonderland.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Workout(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_date = db.Column(db.Date, nullable=False, index=True)

    # type: incline, flat, strength, recovery
    type = db.Column(db.String(20), nullable=False)

    duration_min = db.Column(db.Integer, nullable=False)
    distance_mi = db.Column(db.Float, nullable=False, default=0.0)
    incline_pct = db.Column(db.Float, nullable=False, default=0.0)
    pack_lb = db.Column(db.Float, nullable=False, default=0.0)
    rpe = db.Column(db.Integer, nullable=False, default=5)  # 1-10
    notes = db.Column(db.Text, nullable=True)

    def vertical_ft(self) -> float:
        # vertical (ft) = distance(miles) * 5280 * incline%
        return float(self.distance_mi * 5280.0 * (self.incline_pct / 100.0))

    def load_score(self) -> float:
        # vertical-weighted load
        v = self.vertical_ft()
        return float(v * (1.0 + (self.pack_lb / 50.0)))

    def session_stress(self) -> float:
        # simple stress proxy
        load_factor = 1.0 + (self.pack_lb / 40.0)
        return float(self.duration_min * max(self.rpe, 1) * load_factor)


@dataclass(frozen=True)
class PhaseTargets:
    name: str
    vert_min: int
    vert_max: int
    long_min: int
    long_max: int
    pack_min: int
    pack_max: int


def phase_for(d: date) -> PhaseTargets:
    # Assumes target hike in late Aug; shift these ranges as needed.
    # Uses the year of the given date.
    y = d.year
    feb1 = date(y, 2, 1)
    apr1 = date(y, 4, 1)
    jun1 = date(y, 6, 1)
    aug1 = date(y, 8, 1)

    if d < apr1:
        return PhaseTargets("Base", 1500, 2000, 45, 60, 0, 5)
    if d < jun1:
        return PhaseTargets("Build", 3000, 4500, 60, 75, 10, 20)
    if d < aug1:
        return PhaseTargets("Peak", 6000, 9000, 75, 90, 20, 35)
    return PhaseTargets("Taper", 2000, 3000, 45, 60, 0, 15)


def week_range(d: date) -> Tuple[date, date]:
    # Monday-Sunday
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=7)
    return start, end


def sum_week_metrics(start: date, end: date) -> Dict[str, float]:
    rows = (
        db.session.query(Workout)
        .filter(Workout.session_date >= start, Workout.session_date < end)
        .order_by(Workout.session_date.asc())
        .all()
    )
    vert = sum(w.vertical_ft() for w in rows)
    stress = sum(w.session_stress() for w in rows)
    avg_pack = (sum(w.pack_lb for w in rows) / len(rows)) if rows else 0.0
    max_long = max((w.duration_min for w in rows), default=0)
    missed = 0  # placeholder if you later schedule planned sessions
    return {
        "vert": vert,
        "stress": stress,
        "avg_pack": avg_pack,
        "max_long": max_long,
        "count": float(len(rows)),
        "missed": float(missed),
    }


def last_n_weeks_series(n: int = 12) -> Dict[str, list]:
    today = date.today()
    start_this_week, _ = week_range(today)
    weeks = []
    verts = []
    packs = []
    stresses = []

    for i in range(n - 1, -1, -1):
        ws = start_this_week - timedelta(days=7 * i)
        we = ws + timedelta(days=7)
        m = sum_week_metrics(ws, we)
        weeks.append(ws.strftime("%b %d"))
        verts.append(round(m["vert"]))
        packs.append(round(m["avg_pack"], 1))
        stresses.append(round(m["stress"], 1))

    return {"weeks": weeks, "verts": verts, "packs": packs, "stresses": stresses}


def risk_flags(today: date) -> Dict[str, str]:
    # Overuse: vert increase >25% WoW OR pack increase >5lb WoW OR high RPE avg
    this_ws, this_we = week_range(today)
    prev_ws = this_ws - timedelta(days=7)
    prev_we = this_we - timedelta(days=7)

    this = sum_week_metrics(this_ws, this_we)
    prev = sum_week_metrics(prev_ws, prev_we)

    flags: Dict[str, str] = {}

    if prev["vert"] > 0 and this["vert"] > prev["vert"] * 1.25:
        flags["overuse_vertical"] = "Vertical increased >25% week-over-week."

    if (this["avg_pack"] - prev["avg_pack"]) > 5.0:
        flags["overuse_pack"] = "Average pack increased >5 lb week-over-week."

    # RPE avg (last 7 days)
    seven_days_ago = today - timedelta(days=7)
    rpe_avg = (
        db.session.query(func.avg(Workout.rpe))
        .filter(Workout.session_date >= seven_days_ago, Workout.session_date <= today)
        .scalar()
    )
    if rpe_avg is not None and rpe_avg >= 7.0:
        flags["overuse_rpe"] = "Average RPE ≥7 over the last 7 days."

    # Undertraining: <70% target for 2 consecutive weeks
    tgt = phase_for(today)
    this_ratio = (this["vert"] / max(tgt.vert_min, 1)) if tgt.vert_min else 0.0
    prev_ratio = (prev["vert"] / max(tgt.vert_min, 1)) if tgt.vert_min else 0.0
    if this_ratio < 0.7 and prev_ratio < 0.7:
        flags["undertraining"] = "You hit <70% of weekly vertical target for 2 weeks."

    # Injury keyword scan (last 14 days)
    fourteen_days_ago = today - timedelta(days=14)
    recent_notes = (
        db.session.query(Workout.notes)
        .filter(Workout.session_date >= fourteen_days_ago, Workout.notes.isnot(None))
        .all()
    )
    keywords = ("pain", "knee", "foot", "shin", "achilles", "hip")
    if any(n and any(k in n.lower() for k in keywords) for (n,) in recent_notes):
        flags["injury_signal"] = "Notes mention possible injury signals in the last 14 days."

    return flags


def readiness_score(today: date) -> int:
    # Simple score 0-100 based on 4-week avg vertical, avg pack, longest session
    series = last_n_weeks_series(4)
    verts = series["verts"]
    packs = series["packs"]

    four_week_avg_vert = sum(verts) / max(len(verts), 1)
    four_week_avg_pack = sum(packs) / max(len(packs), 1)

    # Longest session last 4 weeks
    start_this_week, _ = week_range(today)
    start_4 = start_this_week - timedelta(days=28)
    max_long = (
        db.session.query(func.max(Workout.duration_min))
        .filter(Workout.session_date >= start_4, Workout.session_date <= today)
        .scalar()
        or 0
    )

    tgt = phase_for(today)
    peak_target = 9000.0 if tgt.name != "Peak" else 9000.0
    goal_pack = 30.0

    s = (
        (four_week_avg_vert / peak_target) * 0.4
        + (four_week_avg_pack / goal_pack) * 0.3
        + (max_long / 90.0) * 0.3
    )
    return int(max(0, min(100, round(s * 100))))


@app.before_request
def ensure_db():
    # Create tables once.
    db.create_all()


@app.get("/")
def dashboard():
    today = date.today()
    ws, we = week_range(today)
    metrics = sum_week_metrics(ws, we)
    tgt = phase_for(today)
    flags = risk_flags(today)
    score = readiness_score(today)
    series = last_n_weeks_series(12)

    # week target band
    target_mid = (tgt.vert_min + tgt.vert_max) // 2
    weekly_progress = 0 if target_mid == 0 else min(100, int((metrics["vert"] / target_mid) * 100))

    return render_template(
        "dashboard.html",
        today=today,
        week_start=ws,
        week_end=we - timedelta(days=1),
        metrics=metrics,
        target=tgt,
        flags=flags,
        readiness=score,
        weekly_progress=weekly_progress,
        series=series,
    )


@app.get("/log")
def log_workout_form():
    today = date.today()
    tgt = phase_for(today)
    return render_template("log.html", today=today, target=tgt)


@app.post("/log")
def log_workout_submit():
    try:
        session_date = datetime.strptime(request.form["session_date"], "%Y-%m-%d").date()
        wtype = request.form["type"]
        duration_min = int(request.form["duration_min"])
        distance_mi = float(request.form.get("distance_mi", 0) or 0)
        incline_pct = float(request.form.get("incline_pct", 0) or 0)
        pack_lb = float(request.form.get("pack_lb", 0) or 0)
        rpe = int(request.form.get("rpe", 5) or 5)
        notes = request.form.get("notes", "").strip() or None

        if wtype not in ("incline", "flat", "strength", "recovery"):
            raise ValueError("Invalid workout type.")
        if duration_min <= 0:
            raise ValueError("Duration must be > 0.")
        if not (1 <= rpe <= 10):
            raise ValueError("RPE must be 1-10.")
        if distance_mi < 0 or incline_pct < 0 or pack_lb < 0:
            raise ValueError("Distance, incline, and pack must be ≥ 0.")

        w = Workout(
            session_date=session_date,
            type=wtype,
            duration_min=duration_min,
            distance_mi=distance_mi,
            incline_pct=incline_pct,
            pack_lb=pack_lb,
            rpe=rpe,
            notes=notes,
        )
        db.session.add(w)
        db.session.commit()
        flash("Saved workout.", "success")
        return redirect(url_for("workouts"))

    except Exception as e:
        flash(f"Could not save workout: {e}", "error")
        return redirect(url_for("log_workout_form"))


@app.get("/workouts")
def workouts():
    items = Workout.query.order_by(Workout.session_date.desc(), Workout.id.desc()).limit(200).all()
    return render_template("workouts.html", workouts=items)


@app.post("/workouts/<int:workout_id>/delete")
def delete_workout(workout_id: int):
    w = Workout.query.get_or_404(workout_id)
    db.session.delete(w)
    db.session.commit()
    flash("Deleted workout.", "success")
    return redirect(url_for("workouts"))


@app.get("/settings")
def settings():
    # For now, show phase targets based on today. Extend to editable config if you want.
    today = date.today()
    phases = []
    # display future 6 months phases
    d = today.replace(day=1)
    for _ in range(7):
        p = phase_for(d)
        phases.append((d.strftime("%b %Y"), p))
        d = (d + relativedelta(months=1)).replace(day=1)
    return render_template("settings.html", phases=phases)


@app.template_filter("round0")
def round0(v):
    try:
        return int(round(float(v)))
    except Exception:
        return v


if __name__ == "__main__":
    app.run(debug=True)

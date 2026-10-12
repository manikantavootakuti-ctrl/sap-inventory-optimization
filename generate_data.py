"""
Fake but realistic inventory data for one plant:

  marc_mbew.csv   material master - MRP type, reorder point, safety stock,
                  planned delivery time, price, current stock
  mseg_issues.csv goods issues (mvt 201/261) for the last 2 years

Field names follow SAP (MARC/MBEW/MARD/MSEG) so the analysis can run on a
real extract later.

    python generate_data.py [--seed 1] [--materials 80]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "data"
END = pd.Timestamp("2026-09-30")
WEEKS = 104

GROUPS = {
    "ROH": ("raw material", ["Steel sheet", "Aluminium bar", "Copper wire", "PP granulate",
                             "ABS sheet", "Rubber seal", "Glass fibre mat"]),
    "HALB": ("semi-finished", ["Housing", "Bracket", "Shaft", "Cover plate", "Frame"]),
    "ERSA": ("spare part", ["Bearing", "V-belt", "Hydraulic hose", "Sensor", "Motor",
                            "Pump seal", "Fuse", "Contactor"]),
    "VERP": ("packaging", ["Carton", "Pallet", "Stretch film", "Label roll", "Foam insert"]),
    "HIBE": ("operating supplies", ["Gloves", "Degreaser", "Cutting oil", "Welding wire"]),
}

# demand patterns: name, weight
PATTERNS = [("stable", 0.30), ("seasonal", 0.12), ("trend_up", 0.06), ("irregular", 0.16),
            ("sporadic", 0.18), ("declining", 0.08), ("dead", 0.10)]


def weekly_demand(pattern, base, rng):
    t = np.arange(WEEKS)
    if pattern == "stable":
        d = rng.normal(base, base * rng.uniform(0.1, 0.3), WEEKS)
    elif pattern == "seasonal":
        d = base * (1 + 0.7 * np.sin(2 * np.pi * t / 52 + rng.uniform(0, 6))) \
            + rng.normal(0, base * 0.3, WEEKS)
    elif pattern == "trend_up":
        d = base * (0.6 + 0.8 * t / WEEKS) + rng.normal(0, base * 0.2, WEEKS)
    elif pattern == "irregular":
        hit = rng.random(WEEKS) < rng.uniform(0.55, 0.85)
        d = np.where(hit, rng.gamma(3, base / 3, WEEKS), 0)
    elif pattern == "sporadic":
        hit = rng.random(WEEKS) < rng.uniform(0.08, 0.3)
        d = np.where(hit, rng.gamma(2, base * 1.5, WEEKS), 0)
    elif pattern == "declining":
        stop = int(rng.integers(40, 80))
        d = np.where(t < stop, rng.normal(base, base * 0.3, WEEKS), 0)
    else:  # dead - nothing moved for the last ~1.5 years
        stop = int(rng.integers(5, 25))
        d = np.where(t < stop, rng.normal(base * 0.5, base * 0.2, WEEKS), 0)
    return np.clip(np.round(d), 0, None).astype(int)


def build(seed=1, n=80):
    rng = np.random.default_rng(seed)
    week_start = END - pd.Timedelta(weeks=WEEKS) + pd.Timedelta(days=1)
    names, pw = zip(*PATTERNS)
    pw = np.array(pw) / sum(pw)

    mats, issues = [], []
    for i in range(n):
        mtart = rng.choice(list(GROUPS))
        _, words = GROUPS[mtart]
        matnr = f"{mtart[:2]}-{1000 + i}"
        pattern = rng.choice(names, p=pw)
        # price roughly log-normal, spare parts pricier
        price = float(np.round(rng.lognormal(2.5 if mtart == "ERSA" else 1.2, 1.0), 2))
        base = max(1.0, float(rng.lognormal(3.0, 1.0)) / (1 + price / 20))
        d = weekly_demand(pattern, base, rng)

        # current MRP settings - deliberately a bit random, like real systems
        plifz = int(rng.choice([5, 7, 10, 14, 21, 28, 42]))
        avg = d[-52:].mean()
        dismm = rng.choice(["VB", "PD", "ND"], p=[0.45, 0.45, 0.10])
        minbe = int(round(avg * plifz / 7 * rng.uniform(0.6, 2.5))) if dismm == "VB" else 0
        eisbe = int(round(avg * rng.uniform(0, 4)))
        labst = int(round(avg * rng.uniform(1, 12))) if pattern not in ("dead", "declining") \
            else int(round(base * rng.uniform(5, 40)))

        mats.append({
            "MATNR": matnr, "MAKTX": f"{rng.choice(words)} {rng.integers(10, 99)}",
            "MTART": mtart, "WERKS": "1100", "DISMM": dismm, "MINBE": minbe,
            "EISBE": eisbe, "PLIFZ": plifz, "MEINS": "PC", "VERPR": price,
            "PEINH": 1, "LABST": labst, "_pattern": pattern,
        })

        # explode weekly demand into a few issue documents per week
        for w, qty in enumerate(d):
            if qty == 0:
                continue
            parts = rng.integers(1, 4)
            split = np.diff(np.sort(np.r_[0, rng.integers(0, qty + 1, parts - 1), qty]))
            for q in split:
                if q == 0:
                    continue
                day = week_start + pd.Timedelta(weeks=w, days=int(rng.integers(0, 5)))
                issues.append({
                    "MATNR": matnr, "WERKS": "1100",
                    "BWART": "261" if mtart in ("ROH", "HALB") else "201",
                    "BUDAT": day.date(), "MENGE": int(q),
                })

    mat = pd.DataFrame(mats)
    return {
        "marc_mbew": mat.drop(columns="_pattern"),
        "mseg_issues": pd.DataFrame(issues).sort_values("BUDAT"),
        "_patterns": mat[["MATNR", "_pattern"]],  # only for testing, not written
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--materials", type=int, default=80)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    for name, df in build(a.seed, a.materials).items():
        if name.startswith("_"):
            continue
        df.to_csv(OUT / f"{name}.csv", index=False)
        print(f"{name:12s} {len(df):6d} rows")


if __name__ == "__main__":
    main()

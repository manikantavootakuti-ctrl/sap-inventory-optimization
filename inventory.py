"""
Inventory analysis: ABC/XYZ, safety stock, reorder point, lot size,
slow movers and a suggestion for the MRP type.

Everything is plain pandas + the standard library (no scipy needed).
"""
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"


@dataclass
class Settings:
    service_level: float = 0.95     # cycle service level for safety stock
    history_weeks: int = 52         # weeks of consumption used for stats
    abc_a: float = 0.80             # cumulative value share for A
    abc_b: float = 0.95             # ... and for B
    xyz_x: float = 0.5              # CV below this = X
    xyz_y: float = 1.0              # CV below this = Y, above = Z
    order_cost: float = 50.0        # EUR per purchase order (for EOQ)
    holding_rate: float = 0.20      # yearly holding cost as share of price
    slow_weeks: int = 26            # no issue for this long = slow mover
    dead_weeks: int = 52            # ... this long = dead stock


def load(folder=DATA):
    if not (folder / "marc_mbew.csv").exists():
        import generate_data
        folder.mkdir(exist_ok=True)
        for name, df in generate_data.build().items():
            if not name.startswith("_"):
                df.to_csv(folder / f"{name}.csv", index=False)
    mat = pd.read_csv(folder / "marc_mbew.csv", dtype={"WERKS": str})
    iss = pd.read_csv(folder / "mseg_issues.csv", dtype={"WERKS": str, "BWART": str},
                      parse_dates=["BUDAT"])
    return mat, iss


def weekly_matrix(iss, weeks, end=None):
    """Materials x weeks consumption, including the zero weeks."""
    end = pd.Timestamp(end) if end is not None else iss["BUDAT"].max()
    start = end - pd.Timedelta(weeks=weeks) + pd.Timedelta(days=1)
    x = iss[(iss["BUDAT"] >= start) & (iss["BUDAT"] <= end)].copy()
    x["week"] = ((x["BUDAT"] - start).dt.days // 7).clip(upper=weeks - 1)
    m = x.pivot_table(index="MATNR", columns="week", values="MENGE", aggfunc="sum", fill_value=0)
    return m.reindex(columns=range(weeks), fill_value=0)


def analyse(mat, iss, s: Settings = Settings()):
    end = iss["BUDAT"].max()
    wk = weekly_matrix(iss, s.history_weeks, end).reindex(mat["MATNR"], fill_value=0)

    df = mat.set_index("MATNR").copy()
    df["price"] = df["VERPR"] / df["PEINH"]
    df["avg_week"] = wk.mean(axis=1)
    df["std_week"] = wk.std(axis=1, ddof=1)
    df["annual_qty"] = wk.sum(axis=1) * 52 / s.history_weeks
    df["annual_value"] = df["annual_qty"] * df["price"]
    df["weeks_with_demand"] = (wk > 0).sum(axis=1)
    df["stock_value"] = df["LABST"] * df["price"]

    # last movement - over the full history, not just the analysis window
    last = iss.groupby("MATNR")["BUDAT"].max()
    df["last_issue"] = last.reindex(df.index)
    df["weeks_since_issue"] = ((end - df["last_issue"]).dt.days / 7).round(0)

    # ABC on consumption value
    df = df.sort_values("annual_value", ascending=False)
    total = df["annual_value"].sum()
    df["cum_share"] = df["annual_value"].cumsum() / total if total else 0
    df["ABC"] = np.select([df["cum_share"] <= s.abc_a, df["cum_share"] <= s.abc_b], ["A", "B"], "C")
    if len(df):
        df.iloc[0, df.columns.get_loc("ABC")] = "A"

    # XYZ on coefficient of variation of weekly demand
    df["cv"] = (df["std_week"] / df["avg_week"]).replace([np.inf, -np.inf], np.nan)
    df["XYZ"] = np.select([df["cv"] < s.xyz_x, df["cv"] < s.xyz_y], ["X", "Y"], "Z")
    df.loc[df["avg_week"] == 0, "XYZ"] = "Z"
    df["class"] = df["ABC"] + df["XYZ"]

    # safety stock and reorder point (demand variability only, fixed lead time)
    z = NormalDist().inv_cdf(s.service_level)
    lt_weeks = df["PLIFZ"] / 7
    df["ss_calc"] = np.ceil(z * df["std_week"] * np.sqrt(lt_weeks)).fillna(0)
    df["rop_calc"] = np.ceil(df["avg_week"] * lt_weeks + df["ss_calc"])

    # economic order quantity
    h = df["price"] * s.holding_rate
    df["eoq"] = np.ceil(np.sqrt(2 * df["annual_qty"] * s.order_cost / h.replace(0, np.nan))).fillna(0)

    # coverage and excess
    df["coverage_weeks"] = (df["LABST"] / df["avg_week"]).replace(np.inf, np.nan).round(1)
    target_max = df["rop_calc"] + df["eoq"]
    df["excess_qty"] = (df["LABST"] - target_max).clip(lower=0)
    df["excess_value"] = df["excess_qty"] * df["price"]

    df["status"] = "ok"
    df.loc[df["weeks_since_issue"] >= s.slow_weeks, "status"] = "slow"
    df.loc[(df["weeks_since_issue"] >= s.dead_weeks) | df["last_issue"].isna(), "status"] = "dead"

    df["mrp_suggestion"] = df.apply(suggest_mrp, axis=1)
    df["comment"] = df.apply(comment, axis=1)
    return df.reset_index()


def suggest_mrp(r):
    """
    Rough rule set, the way I'd discuss it in a workshop:
      X (steady)      -> reorder point (VB / VM, same family here)
      Y (fluctuating) -> MRP (PD) for A/B, reorder point OK for cheap C
      Z (sporadic)    -> MRP (PD) for A/B, no planning (ND) / on demand for C
      dead stock      -> ND, and look at scrapping / returning
    """
    if r["status"] == "dead":
        return "ND"
    if r["XYZ"] == "X":
        return "VB"
    if r["XYZ"] == "Y":
        return "VB" if r["ABC"] == "C" else "PD"
    return "ND" if r["ABC"] == "C" else "PD"


def family(dismm):
    """VB and VM are both reorder point planning - don't flag a switch between them."""
    return "RP" if dismm in ("VB", "VM", "V1", "V2") else dismm


def comment(r):
    notes = []
    if r["status"] == "dead":
        notes.append(f"no issue for {int(r['weeks_since_issue'])} weeks" if pd.notna(r["weeks_since_issue"])
                     else "never issued")
    elif r["status"] == "slow":
        notes.append(f"slow mover ({int(r['weeks_since_issue'])} weeks)")
    if family(r["mrp_suggestion"]) != family(r["DISMM"]):
        notes.append(f"MRP type {r['DISMM']} -> {r['mrp_suggestion']}")
    if family(r["DISMM"]) == "RP" and family(r["mrp_suggestion"]) == "RP" and r["status"] == "ok" and r["rop_calc"] > 0:
        diff = r["MINBE"] / r["rop_calc"] - 1
        if abs(diff) > 0.3:
            notes.append(f"reorder point {int(r['MINBE'])} vs calc {int(r['rop_calc'])}")
    if r["excess_value"] > 500:
        notes.append(f"excess ~€{r['excess_value']:,.0f}")
    return "; ".join(notes)


def matrix(df, value="count"):
    """ABC x XYZ matrix - count of materials or stock/consumption value."""
    if value == "count":
        m = df.pivot_table(index="ABC", columns="XYZ", values="MATNR", aggfunc="count", fill_value=0)
    else:
        m = df.pivot_table(index="ABC", columns="XYZ", values=value, aggfunc="sum", fill_value=0)
    return m.reindex(index=list("ABC"), columns=list("XYZ"), fill_value=0)

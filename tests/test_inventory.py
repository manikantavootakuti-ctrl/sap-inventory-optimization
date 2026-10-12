"""Small sanity tests - run with `pytest`."""
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inventory as inv  # noqa: E402


def make(materials, issues):
    mat = pd.DataFrame(materials)
    iss = pd.DataFrame(issues)
    iss["BUDAT"] = pd.to_datetime(iss["BUDAT"])
    return mat, iss


def base_mat(matnr, **kw):
    m = {"MATNR": matnr, "MAKTX": matnr, "MTART": "ROH", "WERKS": "1100", "DISMM": "VB",
         "MINBE": 0, "EISBE": 0, "PLIFZ": 7, "MEINS": "PC", "VERPR": 10.0, "PEINH": 1,
         "LABST": 0}
    m.update(kw)
    return m


def weekly(matnr, qty_per_week, weeks=52, end="2026-09-30"):
    end = pd.Timestamp(end)
    return [{"MATNR": matnr, "WERKS": "1100", "BWART": "261",
             "BUDAT": end - pd.Timedelta(weeks=w), "MENGE": q}
            for w, q in zip(range(weeks), qty_per_week)]


def test_steady_demand_is_x_and_needs_no_safety_stock():
    mat, iss = make([base_mat("M1")], weekly("M1", [10] * 52))
    r = inv.analyse(mat, iss).iloc[0]
    assert r["XYZ"] == "X"
    assert r["avg_week"] == 10
    assert r["ss_calc"] == 0
    assert r["rop_calc"] == 10          # 1 week lead time * 10/week


def test_safety_stock_formula():
    q = [5, 15] * 26                     # mean 10, std ~5.05
    mat, iss = make([base_mat("M1", PLIFZ=28)], weekly("M1", q))
    s = inv.Settings(service_level=0.95)
    r = inv.analyse(mat, iss, s).iloc[0]
    std = pd.Series(q).std()
    expected = math.ceil(1.6449 * std * math.sqrt(4))
    assert abs(r["ss_calc"] - expected) <= 1
    assert r["rop_calc"] == math.ceil(10 * 4 + r["ss_calc"])


def test_abc_split_on_value():
    mats = [base_mat("BIG", VERPR=100.0), base_mat("MID", VERPR=10.0), base_mat("SMALL", VERPR=1.0)]
    iss = weekly("BIG", [10] * 52) + weekly("MID", [10] * 52) + weekly("SMALL", [10] * 52)
    df = inv.analyse(*make(mats, iss)).set_index("MATNR")
    assert df.loc["BIG", "ABC"] == "A"
    assert df.loc["SMALL", "ABC"] == "C"


def test_dead_stock_is_flagged_and_counted_as_excess():
    mats = [base_mat("OLD", LABST=100), base_mat("NEW")]
    iss = weekly("OLD", [5] * 4, end="2025-01-15") + weekly("NEW", [10] * 52)
    df = inv.analyse(*make(mats, iss)).set_index("MATNR")
    assert df.loc["OLD", "status"] == "dead"
    assert df.loc["OLD", "mrp_suggestion"] == "ND"
    assert df.loc["OLD", "excess_value"] == 100 * 10.0


def test_vb_to_vm_is_not_reported_as_change():
    assert inv.family("VB") == inv.family("VM")
    assert inv.family("PD") != inv.family("VB")

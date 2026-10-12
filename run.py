"""
Runs the analysis and writes:
  output/recommendations.csv   one line per material with all the numbers
  output/summary.txt           short summary
  images/*.png                 charts

    python run.py
    python run.py --service-level 0.98 --order-cost 80
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

import inventory as inv

HERE = Path(__file__).parent
BLUE, LIGHT, RED, GREY = "#2f6db5", "#dbe6f4", "#c8553d", "#9aa5b1"
plt.rcParams.update({"figure.dpi": 110, "axes.spines.top": False,
                     "axes.spines.right": False, "font.size": 10})

COLS = ["MATNR", "MAKTX", "MTART", "class", "status", "DISMM", "mrp_suggestion",
        "PLIFZ", "avg_week", "cv", "MINBE", "rop_calc", "EISBE", "ss_calc", "eoq",
        "LABST", "coverage_weeks", "price", "stock_value", "excess_value",
        "weeks_since_issue", "comment"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-level", type=float, default=0.95)
    ap.add_argument("--order-cost", type=float, default=50.0)
    ap.add_argument("--holding-rate", type=float, default=0.20)
    a = ap.parse_args()
    s = inv.Settings(service_level=a.service_level, order_cost=a.order_cost,
                     holding_rate=a.holding_rate)

    mat, iss = inv.load()
    df = inv.analyse(mat, iss, s)

    (HERE / "output").mkdir(exist_ok=True)
    (HERE / "images").mkdir(exist_ok=True)
    df[COLS].round(2).to_csv(HERE / "output" / "recommendations.csv", index=False)

    changes = df[df["mrp_suggestion"].map(inv.family) != df["DISMM"].map(inv.family)]
    lines = [
        f"Materials analysed:   {len(df)}",
        f"Stock value:          €{df['stock_value'].sum():,.0f}",
        f"Excess stock:         €{df['excess_value'].sum():,.0f}  "
        f"(above reorder point + EOQ)",
        f"Dead stock:           €{df.loc[df.status == 'dead', 'stock_value'].sum():,.0f}  "
        f"({(df.status == 'dead').sum()} materials, no issue for {s.dead_weeks}+ weeks)",
        f"Slow movers:          €{df.loc[df.status == 'slow', 'stock_value'].sum():,.0f}  "
        f"({(df.status == 'slow').sum()} materials)",
        f"MRP type changes:     {len(changes)} suggested",
        "",
        "ABC/XYZ - number of materials",
        inv.matrix(df).to_string(),
        "",
        f"Service level {s.service_level:.0%}, order cost €{s.order_cost:.0f}, "
        f"holding rate {s.holding_rate:.0%}",
    ]
    text = "\n".join(lines)
    (HERE / "output" / "summary.txt").write_text(text + "\n")
    print(text)

    charts(df)
    print("\nwritten: output/recommendations.csv, output/summary.txt, images/")


def charts(df):
    # ABC/XYZ heat map - count and consumption value
    cnt = inv.matrix(df)
    val = inv.matrix(df, "annual_value")
    fig, ax = plt.subplots(figsize=(5, 3.6))
    ax.imshow(val.values, cmap="Blues")
    for i in range(3):
        for j in range(3):
            v = val.values[i, j]
            dark = v > val.values.max() * 0.5
            ax.text(j, i, f"{cnt.values[i, j]} mat.\n€{v/1000:,.0f}k/yr",
                    ha="center", va="center", fontsize=9, color="white" if dark else "black")
    ax.set_xticks(range(3), ["X steady", "Y fluctuating", "Z sporadic"])
    ax.set_yticks(range(3), ["A", "B", "C"])
    ax.set_title("ABC / XYZ matrix (consumption value)", loc="left", fontweight="bold")
    for sp in ax.spines.values():
        sp.set_visible(False)
    fig.tight_layout()
    fig.savefig(HERE / "images" / "abc_xyz.png")
    plt.close(fig)

    # where is the money sitting
    split = df.assign(needed=df["stock_value"] - df["excess_value"]).groupby("status")[
        ["needed", "excess_value"]].sum().reindex(["ok", "slow", "dead"]).fillna(0)
    fig, ax = plt.subplots(figsize=(5.5, 3))
    ax.barh(split.index, split["needed"], color=BLUE, label="within target")
    ax.barh(split.index, split["excess_value"], left=split["needed"], color=RED, label="excess")
    ax.xaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"€{x/1000:,.0f}k"))
    ax.invert_yaxis()
    ax.legend(frameon=False, loc="lower right")
    ax.set_title("Stock value by status", loc="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(HERE / "images" / "stock_value.png")
    plt.close(fig)

    # current vs calculated reorder point for VB materials
    vb = df[(df["DISMM"] == "VB") & (df["status"] == "ok") & (df["rop_calc"] > 0)]
    fig, ax = plt.subplots(figsize=(4.6, 4))
    ax.scatter(vb["rop_calc"], vb["MINBE"], s=22, color=BLUE, alpha=0.8)
    lim = max(vb["rop_calc"].max(), vb["MINBE"].max()) * 1.05 if len(vb) else 1
    ax.plot([1, lim], [1, lim], color=GREY, lw=1, ls="--")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("calculated reorder point")
    ax.set_ylabel("reorder point in system (MINBE)")
    ax.set_title("Reorder points: system vs calculated", loc="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(HERE / "images" / "reorder_points.png")
    plt.close(fig)

    # top 10 excess
    top = df.nlargest(10, "excess_value").iloc[::-1]
    fig, ax = plt.subplots(figsize=(6, 3.6))
    colors = [RED if s != "ok" else BLUE for s in top["status"]]
    ax.barh(top["MATNR"] + "  " + top["MAKTX"], top["excess_value"], color=colors)
    ax.xaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"€{x/1000:,.1f}k"))
    ax.set_title("Top 10 excess stock (red = slow/dead)", loc="left", fontweight="bold")
    fig.tight_layout()
    fig.savefig(HERE / "images" / "top_excess.png")
    plt.close(fig)


if __name__ == "__main__":
    main()

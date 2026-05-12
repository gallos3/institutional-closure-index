import os
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# ----------------------------
# Configuration
# ----------------------------
PANEL_PARQUET = "authority_panel.parquet"
PANEL_XLSX = "authority_panel_full.xlsx"
PANEL_XLSX_SHEET = "panel_all"

OUT_DIR = Path("paper_outputs")
OUT_DIR.mkdir(exist_ok=True)

BASE_YEAR_MAIN = 2022

# Robustness filters (edit if you want)
ROBUST_EXCLUDE_DEGA1 = True     # exclude degA == 1
ROBUST_MIN_AWARDS = 3           # keep observations with total_awards >= this


# ----------------------------
# Helpers
# ----------------------------
def load_panel() -> pd.DataFrame:
    if Path(PANEL_PARQUET).exists():
        df = pd.read_parquet(PANEL_PARQUET)
        return df
    elif Path(PANEL_XLSX).exists():
        df = pd.read_excel(PANEL_XLSX, sheet_name=PANEL_XLSX_SHEET)
        return df
    else:
        raise FileNotFoundError("Cannot find authority_panel.parquet or authority_panel_full.xlsx")

def coerce_numeric(df: pd.DataFrame, cols) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

def safe_div(a, b, eps=1e-12):
    return a / (b.replace(0, np.nan) + eps)

def cpv_sort_key(x: str):
    # numeric sort if possible
    try:
        return int(str(x))
    except Exception:
        return str(x)

def write_table(df: pd.DataFrame, name: str):
    csv_path = OUT_DIR / f"{name}.csv"
    xlsx_path = OUT_DIR / f"{name}.xlsx"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_excel(xlsx_path, index=False)
    return csv_path, xlsx_path

def save_fig(fig_name: str):
    path = OUT_DIR / fig_name
    plt.tight_layout()
    plt.savefig(path, dpi=200)
    plt.close()
    return path


# ----------------------------
# Main
# ----------------------------
def main():
    df = load_panel()

    # Standardize key fields
    if "cpv" in df.columns:
        df["cpv"] = df["cpv"].astype(str)
    if "base_year" in df.columns:
        df["base_year"] = pd.to_numeric(df["base_year"], errors="coerce").astype("Int64")

    numeric_cols = [
        "ICI_count", "ICI_value",
        "HHI_count", "HHI_value",
        "degA", "n_suppliers", "n_dyads",
        "total_awards", "total_value",
        "rel_component_count", "rel_component_value"
    ]
    coerce_numeric(df, numeric_cols)

    # Derived diagnostics (for interpretation)
    eps = 1e-12
    df["delta_ici"] = df["ICI_value"] - df["ICI_count"]
    df["abs_delta_ici"] = (df["delta_ici"]).abs()
    df["ratio_ici_value_over_count"] = safe_div(df["ICI_value"] + eps, df["ICI_count"] + eps)
    df["avg_value_per_award"] = safe_div(df["total_value"], df["total_awards"], eps=eps)

    # ----------------------------
    # MAIN SNAPSHOT (2022)
    # ----------------------------
    df_main = df[df["base_year"] == BASE_YEAR_MAIN].copy()
    df_main = df_main.dropna(subset=["cpv", "aid", "ICI_count", "ICI_value"])

    # Order CPVs
    cpvs = sorted(df_main["cpv"].unique().tolist(), key=cpv_sort_key)

    # ----------------------------
    # Table 1: CPV-level summary (2022) for BOTH ICI_count and ICI_value
    # ----------------------------
    def q(s, p):
        return s.quantile(p) if len(s) else np.nan

    table1 = (
        df_main.groupby("cpv", as_index=False)
        .agg(
            n_authorities=("aid", "nunique"),
            n_obs=("aid", "size"),
            median_ici_count=("ICI_count", "median"),
            p90_ici_count=("ICI_count", lambda s: q(s, 0.90)),
            median_ici_value=("ICI_value", "median"),
            p90_ici_value=("ICI_value", lambda s: q(s, 0.90)),
            median_hhi_count=("HHI_count", "median"),
            median_hhi_value=("HHI_value", "median"),
            median_degA=("degA", "median"),
            share_degA1=("degA", lambda s: (s == 1).mean()),
            median_total_awards=("total_awards", "median"),
            median_total_value=("total_value", "median"),
            median_delta=("delta_ici", "median"),
            p90_abs_delta=("abs_delta_ici", lambda s: q(s, 0.90)),
            median_avg_value_per_award=("avg_value_per_award", "median"),
        )
    )

    table1 = table1.sort_values("median_ici_count", ascending=False)
    write_table(table1, f"table1_cpv_summary_{BASE_YEAR_MAIN}")

    # ----------------------------
    # Table 2: Divergence typology (quadrants) (2022)
    # Thresholds: medians (pooled)
    # ----------------------------
    med_c = df_main["ICI_count"].median()
    med_v = df_main["ICI_value"].median()

    def quadrant(row):
        hc = row["ICI_count"] >= med_c
        hv = row["ICI_value"] >= med_v
        if hc and hv:
            return "Q1_high_count_high_value"
        if hc and (not hv):
            return "Q2_high_count_low_value"
        if (not hc) and hv:
            return "Q3_low_count_high_value"
        return "Q4_low_count_low_value"

    df_main["quadrant"] = df_main.apply(quadrant, axis=1)

    table2 = (
        df_main.groupby(["cpv", "quadrant"], as_index=False)
        .agg(
            n_obs=("aid", "size"),
            n_authorities=("aid", "nunique"),
            median_ici_count=("ICI_count", "median"),
            median_ici_value=("ICI_value", "median"),
            median_hhi_count=("HHI_count", "median"),
            median_hhi_value=("HHI_value", "median"),
            median_degA=("degA", "median"),
            median_avg_value_per_award=("avg_value_per_award", "median"),
        )
    )
    write_table(table2, f"table2_quadrants_{BASE_YEAR_MAIN}")

    # ----------------------------
    # Table 3: Correlations between ICI_count and ICI_value (2022)
    # ----------------------------
    def corr_block(g):
        pearson = g[["ICI_count", "ICI_value"]].corr(method="pearson").iloc[0, 1]
        spearman = g[["ICI_count", "ICI_value"]].corr(method="spearman").iloc[0, 1]
        return pearson, spearman

    overall_p, overall_s = corr_block(df_main)

    rows = [("ALL", len(df_main), df_main["aid"].nunique(), overall_p, overall_s)]
    for c in cpvs:
        g = df_main[df_main["cpv"] == c]
        if len(g) >= 10:
            p, s = corr_block(g)
            rows.append((c, len(g), g["aid"].nunique(), p, s))

    table3 = pd.DataFrame(rows, columns=["cpv", "n_obs", "n_authorities", "pearson_r", "spearman_rho"])
    write_table(table3, f"table3_corr_{BASE_YEAR_MAIN}")

    # ----------------------------
    # FIGURE 1: Scatter ICI_count vs ICI_value (2022)
    # (log1p transform to make it readable)
    # ----------------------------
    x = np.log1p(df_main["ICI_count"].clip(lower=0))
    y = np.log1p(df_main["ICI_value"].clip(lower=0))

    plt.figure()
    plt.scatter(x, y, s=8, alpha=0.5)

    # y=x line in log1p space
    mn = float(min(x.min(), y.min()))
    mx = float(max(x.max(), y.max()))
    plt.plot([mn, mx], [mn, mx])

    plt.xlabel("log(1 + ICI_count)")
    plt.ylabel("log(1 + ICI_value)")
    plt.title(f"ICI_count vs ICI_value (base_year={BASE_YEAR_MAIN})")
    save_fig(f"fig1_scatter_ici_count_vs_value_{BASE_YEAR_MAIN}.png")

    # ---------------------------------
    # FIGURE 2: Paired boxplots (ICI_count vs ICI_value) by CPV
    # ---------------------------------
    data_count = [df_main.loc[df_main["cpv"] == c, "ICI_count"].dropna().values for c in cpvs]
    data_value = [df_main.loc[df_main["cpv"] == c, "ICI_value"].dropna().values for c in cpvs]

    plt.figure(figsize=(12, 6))

    positions = np.arange(1, len(cpvs) + 1)
    offset = 0.18
    pos_count = positions - offset
    pos_value = positions + offset

    bp1 = plt.boxplot(
        data_count,
        positions=pos_count,
        widths=0.30,
        showfliers=False,
        patch_artist=True,
    )
    bp2 = plt.boxplot(
        data_value,
        positions=pos_value,
        widths=0.30,
        showfliers=False,
        patch_artist=True,
    )
    #Apply hatching 
    for b in bp1["boxes"]:
        b.set_hatch("//")
    for b in bp2["boxes"]:
        b.set_hatch("..")

    plt.xticks(positions, cpvs, rotation=45, ha="right")
    plt.xlabel("CPV")
    plt.ylabel("ICI")
    plt.title(f"ICI distributions by CPV: count- vs value-weighted (base_year={BASE_YEAR_MAIN})")

    # Legend (with proxy artists)
    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor="white", edgecolor="black", hatch="//", label="ICI_count"),
        Patch(facecolor="white", edgecolor="black", hatch="..", label="ICI_value"),
    ]
    plt.legend(handles=legend_handles, loc="upper right", frameon=True)

    save_fig(f"fig2_paired_boxplot_ici_count_vs_value_by_cpv_{BASE_YEAR_MAIN}.png")
    # ---------------------------------
    # FIGURE 3: Boxplot of delta (ICI_value - ICI_count) by CPV
    # ---------------------------------
    delta_data = []
    for c in cpvs:
        sub = df_main[df_main["cpv"] == c][["ICI_count", "ICI_value"]].dropna()
        delta = (sub["ICI_value"] - sub["ICI_count"]).values
        delta_data.append(delta)

    plt.figure(figsize=(12, 6))
    plt.boxplot(delta_data, labels=cpvs, showfliers=False)
    plt.axhline(0.0, linewidth=1)
    plt.xlabel("CPV")
    plt.ylabel("ΔICI (value − count)")
    plt.title(f"Difference between value- and count-weighted closure (base_year={BASE_YEAR_MAIN})")
    plt.xticks(rotation=45, ha="right")
    save_fig(f"fig3_boxplot_delta_ici_by_cpv_{BASE_YEAR_MAIN}.png")
    # ---------------------------------
    # OPTIONAL: Zoomed paired boxplots using pooled quantile limits
    # ---------------------------------
    all_vals = df_main[["ICI_count", "ICI_value"]].stack().dropna().values
    lo, hi = np.quantile(all_vals, [0.01, 0.99])

    plt.figure(figsize=(12, 6))

    positions = np.arange(1, len(cpvs) + 1)
    offset = 0.18
    pos_count = positions - offset
    pos_value = positions + offset

    bp1 = plt.boxplot(data_count, positions=pos_count, widths=0.30, showfliers=False, patch_artist=True)
    bp2 = plt.boxplot(data_value, positions=pos_value, widths=0.30, showfliers=False, patch_artist=True)

    for b in bp1["boxes"]:
        b.set_hatch("//")
    for b in bp2["boxes"]:
        b.set_hatch("..")

    plt.ylim(lo, hi)
    plt.xticks(positions, cpvs, rotation=45, ha="right")
    plt.xlabel("CPV")
    plt.ylabel("ICI (zoomed to 1–99th pct)")
    plt.title(f"ICI by CPV (zoomed): count vs value (base_year={BASE_YEAR_MAIN})")

    from matplotlib.patches import Patch
    plt.legend(
        handles=[
            Patch(facecolor="white", edgecolor="black", hatch="//", label="ICI_count"),
            Patch(facecolor="white", edgecolor="black", hatch="..", label="ICI_value"),
        ],
        loc="upper right",
        frameon=True,
    )

    save_fig(f"fig_optional_zoomed_paired_boxplot_{BASE_YEAR_MAIN}.png")

    # ----------------------------
    # FIGURE 4: Boxplot delta = ICI_value - ICI_count by CPV (2022)
    # ----------------------------
    data = [df_main.loc[df_main["cpv"] == c, "delta_ici"].dropna().values for c in cpvs]
    plt.figure()
    plt.boxplot(data, labels=cpvs, showfliers=False)
    plt.axhline(0.0)
    plt.xlabel("CPV")
    plt.ylabel("Δ ICI (value − count)")
    plt.title(f"Difference between value- and count-weighted closure (base_year={BASE_YEAR_MAIN})")
    plt.xticks(rotation=45, ha="right")
    save_fig(f"fig4_boxplot_delta_by_cpv_{BASE_YEAR_MAIN}.png")

    # ----------------------------
    # FIGURE 5: Trend of median ICI_count (2018–2022) by CPV
    # (one figure; may be busy but reviewer-friendly in appendix)
    # ----------------------------
    trend = (
        df.groupby(["cpv", "base_year"], as_index=False)
        .agg(median_ici_count=("ICI_count", "median"),
             median_ici_value=("ICI_value", "median"),
             n_obs=("aid", "size"))
    )

    plt.figure()
    for c in sorted(trend["cpv"].unique(), key=cpv_sort_key):
        g = trend[trend["cpv"] == c].sort_values("base_year")
        plt.plot(g["base_year"], g["median_ici_count"], marker="o", linewidth=1, label=c)
    plt.xlabel("Base year (cumulative window end)")
    plt.ylabel("Median ICI_count")
    plt.title("Median ICI_count by CPV across cumulative windows (2018–2022)")
    plt.legend(ncol=2, fontsize=8)
    save_fig("fig5_trend_median_ici_count_2018_2022.png")

    # ----------------------------
    # FIGURE 6: Trend of median ICI_value (2018–2022) by CPV
    # ----------------------------
    plt.figure()
    for c in sorted(trend["cpv"].unique(), key=cpv_sort_key):
        g = trend[trend["cpv"] == c].sort_values("base_year")
        plt.plot(g["base_year"], g["median_ici_value"], marker="o", linewidth=1, label=c)
    plt.xlabel("Base year (cumulative window end)")
    plt.ylabel("Median ICI_value")
    plt.title("Median ICI_value by CPV across cumulative windows (2018–2022)")
    plt.legend(ncol=2, fontsize=8)
    save_fig("fig6_trend_median_ici_value_2018_2022.png")

    # ----------------------------
    # ROBUSTNESS: exclude degA=1 and require min awards
    # ----------------------------
    df_rob = df_main.copy()

    if ROBUST_EXCLUDE_DEGA1 and "degA" in df_rob.columns:
        df_rob = df_rob[df_rob["degA"] >= 2]

    if "total_awards" in df_rob.columns:
        df_rob = df_rob[df_rob["total_awards"] >= ROBUST_MIN_AWARDS]

    if len(df_rob) >= 50:
        # Robust scatter
        xr = np.log1p(df_rob["ICI_count"].clip(lower=0))
        yr = np.log1p(df_rob["ICI_value"].clip(lower=0))

        plt.figure()
        plt.scatter(xr, yr, s=8, alpha=0.5)
        mn = float(min(xr.min(), yr.min()))
        mx = float(max(xr.max(), yr.max()))
        plt.plot([mn, mx], [mn, mx])
        plt.xlabel("log(1 + ICI_count)")
        plt.ylabel("log(1 + ICI_value)")
        plt.title(f"Robustness: degA≥2 & awards≥{ROBUST_MIN_AWARDS} (base_year={BASE_YEAR_MAIN})")
        save_fig(f"rob_fig1_scatter_{BASE_YEAR_MAIN}.png")

        # Robust Table: CPV summary
        table1r = (
            df_rob.groupby("cpv", as_index=False)
            .agg(
                n_authorities=("aid", "nunique"),
                n_obs=("aid", "size"),
                median_ici_count=("ICI_count", "median"),
                p90_ici_count=("ICI_count", lambda s: q(s, 0.90)),
                median_ici_value=("ICI_value", "median"),
                p90_ici_value=("ICI_value", lambda s: q(s, 0.90)),
                median_hhi_count=("HHI_count", "median"),
                median_hhi_value=("HHI_value", "median"),
                median_degA=("degA", "median"),
                median_total_awards=("total_awards", "median"),
                median_total_value=("total_value", "median"),
                median_delta=("delta_ici", "median"),
            )
            .sort_values("median_ici_count", ascending=False)
        )
        write_table(table1r, f"rob_table1_cpv_summary_{BASE_YEAR_MAIN}_degA2_awards{ROBUST_MIN_AWARDS}")

    # ----------------------------
    # Write a small text report (for your Section 5 notes)
    # ----------------------------
    report_lines = []
    report_lines.append(f"MAIN SNAPSHOT base_year={BASE_YEAR_MAIN}")
    report_lines.append(f"n_obs={len(df_main):,} ; unique_authorities={df_main['aid'].nunique():,} ; unique_cpvs={df_main['cpv'].nunique():,}")
    report_lines.append(f"Correlation (ALL) Pearson r={overall_p:.3f} ; Spearman rho={overall_s:.3f}")
    report_lines.append("")
    report_lines.append("Top 10 by ICI_count (2022):")
    topc = df_main.sort_values("ICI_count", ascending=False).head(10)
    report_lines.append(topc[["cpv", "aid", "authority", "ICI_count", "ICI_value", "HHI_count", "HHI_value", "degA", "n_suppliers", "total_awards", "total_value"]]
                        .to_string(index=False))
    report_lines.append("")
    report_lines.append("Top 10 by ICI_value (2022):")
    topv = df_main.sort_values("ICI_value", ascending=False).head(10)
    report_lines.append(topv[["cpv", "aid", "authority", "ICI_count", "ICI_value", "HHI_count", "HHI_value", "degA", "n_suppliers", "total_awards", "total_value"]]
                        .to_string(index=False))

    report_path = OUT_DIR / f"report_key_stats_{BASE_YEAR_MAIN}.txt"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Done. Outputs in: {OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()

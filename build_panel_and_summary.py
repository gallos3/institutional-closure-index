import re
from pathlib import Path
import pandas as pd

OUT_DIR = Path("out")
PANEL_PARQUET = Path("authority_panel.parquet")
SUMMARY_XLSX = Path("ici_summary.xlsx")
PANEL_CSV = Path("authority_panel.csv")  # optional: easier to inspect
WRITE_PANEL_CSV = True  # set False if too big

# filename pattern: metrics_cpv{CPV}_y{YEAR}.jsonl
PAT = re.compile(r"metrics_cpv(?P<cpv>\d+)_y(?P<year>\d{4})\.jsonl$", re.IGNORECASE)

def compute_authority_panel(df: pd.DataFrame, weight_col: str, hhi_col: str) -> pd.DataFrame:
    """
    Build authority-level panel from dyad-level rows.
    weight_col: 'awards_pair' or 'value_pair'
    hhi_col: 'HHI_count' or 'HHI_value'
    """
    # Safety: avoid division by zero
    denom = (df["AA"] + df["HF"] + df["PA"]).replace(0, pd.NA)
    rel_frac = (df["AA"] + df["HF"]) / denom
    rel_frac = rel_frac.fillna(0.0)

    # weights per authority within a CPV-year snapshot
    wsum = df.groupby(["cpv", "base_year", "aid"], as_index=False)[weight_col].sum().rename(columns={weight_col: "w_sum"})
    df2 = df.merge(wsum, on=["cpv", "base_year", "aid"], how="left")
    df2["w"] = df2[weight_col] / df2["w_sum"].replace(0, pd.NA)
    df2["w"] = df2["w"].fillna(0.0)

    df2["rel_term"] = df2["w"] * rel_frac

    # Aggregate to authority
    g = df2.groupby(["cpv", "base_year", "aid"], as_index=False).agg(
        authority=("authority", "first"),
        degA=("degA", "first"),
        HHI=(hhi_col, "first"),
        n_suppliers=("cid", "nunique"),
        n_dyads=("cid", "size"),
        total_awards=("awards_pair", "sum"),
        total_value=("value_pair", "sum"),
        rel_component=("rel_term", "sum"),
    )

    g["ICI"] = g["HHI"] * g["rel_component"]

    # Make sure types are clean
    for c in ["HHI", "rel_component", "ICI"]:
        g[c] = pd.to_numeric(g[c], errors="coerce").fillna(0.0)

    return g

def main():
    files = sorted(OUT_DIR.glob("metrics_cpv*_y*.jsonl"))
    if not files:
        raise FileNotFoundError(f"No jsonl files found in {OUT_DIR.resolve()}")

    chunks = []
    for fp in files:
        m = PAT.search(fp.name)
        if not m:
            continue
        cpv = m.group("cpv")
        year = int(m.group("year"))

        # Read jsonl
        df = pd.read_json(fp, lines=True)

        # Add identifiers from filename
        df["cpv"] = cpv
        df["base_year"] = year

        # Ensure numeric cols are numeric
        num_cols = ["awards_pair","value_pair","HHI_count","HHI_value","PA","AA","HF","degA","degC"]
        for c in num_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        chunks.append(df)

    all_df = pd.concat(chunks, ignore_index=True)

    # Build authority panel: count-weighted (recommended main)
    panel_count = compute_authority_panel(all_df, weight_col="awards_pair", hhi_col="HHI_count")
    panel_count = panel_count.rename(columns={
        "HHI": "HHI_count",
        "rel_component": "rel_component_count",
        "ICI": "ICI_count"
    })

    # Build authority panel: value-weighted (optional robustness)
    panel_value = compute_authority_panel(all_df, weight_col="value_pair", hhi_col="HHI_value")
    panel_value = panel_value.rename(columns={
        "HHI": "HHI_value",
        "rel_component": "rel_component_value",
        "ICI": "ICI_value"
    })

    # Merge panels
    panel = panel_count.merge(
        panel_value[["cpv","base_year","aid","HHI_value","rel_component_value","ICI_value"]],
        on=["cpv","base_year","aid"],
        how="left"
    )

    # Save panel
    panel.to_parquet(PANEL_PARQUET, index=False)
    if WRITE_PANEL_CSV:
        panel.to_csv(PANEL_CSV, index=False, encoding="utf-8")

    # Build CPV×year summary (for paper tables/figures)
    def q(x, p): 
        return x.quantile(p) if len(x) else 0.0

    summary = panel.groupby(["cpv","base_year"], as_index=False).agg(
        n_authorities=("aid","nunique"),
        median_ici=("ICI_count","median"),
        p75_ici=("ICI_count", lambda s: q(s, 0.75)),
        p90_ici=("ICI_count", lambda s: q(s, 0.90)),
        median_hhi=("HHI_count","median"),
        median_degA=("degA","median"),
        share_degA1=("degA", lambda s: (s==1).mean()),
        share_degA2=("degA", lambda s: (s<=2).mean()),
    )

    # Write Excel with two sheets
    with pd.ExcelWriter(SUMMARY_XLSX, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="cpv_year_summary", index=False)
        # Provide a smaller extract of panel for inspection (optional)
        panel.sort_values(["cpv","base_year","ICI_count"], ascending=[True, True, False]).head(5000).to_excel(
            xw, sheet_name="panel_top5000_by_ici", index=False
        )

    print("Done.")
    print(f"Authority panel: {PANEL_PARQUET.resolve()}")
    if WRITE_PANEL_CSV:
        print(f"Authority panel (csv): {PANEL_CSV.resolve()}")
    print(f"Summary excel: {SUMMARY_XLSX.resolve()}")

if __name__ == "__main__":
    main()

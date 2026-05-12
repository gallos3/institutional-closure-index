import pandas as pd
import numpy as np

INPUT_XLSX = "authority_panel_full.xlsx"
SHEET = "panel_all"
BASE_YEAR = 2022

CPVS = [
    "90500","60130","34300","66510","45233","45000",
    "33100","33141","33140","34144","33600","33690"
]

PER_STRATUM = 6
SEED = 42

MIN_SUPPLIERS = 2
MIN_TOTAL_AWARDS = 3

OUT_XLSX = f"validation_cases_18_per_cpv_base{BASE_YEAR}.xlsx"
OUT_CSV  = f"validation_cases_18_per_cpv_base{BASE_YEAR}.csv"

def main():
    rng = np.random.default_rng(SEED)

    df = pd.read_excel(INPUT_XLSX, sheet_name=SHEET)
    df = df[df["base_year"] == BASE_YEAR].copy()

    # paper-style restrictions
    df = df[(df["n_suppliers"] >= MIN_SUPPLIERS) & (df["total_awards"] >= MIN_TOTAL_AWARDS)].copy()

    # normalize cpv to string (to match list)
    df["cpv"] = df["cpv"].astype(str)

    all_cases = []
    status_rows = []
    stats_frames = []

    # Build everything in memory first; write Excel at the end (avoids "no visible sheet" error)
    per_cpv_outputs = {}

    for cpv in CPVS:
        sub = df[df["cpv"] == cpv].dropna(subset=["aid", "ICI_count"]).copy()

        if sub.empty:
            status_rows.append({"cpv": cpv, "status": "no rows after filters", "n_rows": 0, "n_authorities": 0})
            continue

        n_auth = int(sub["aid"].nunique())
        n_rows = int(len(sub))

        # need at least 18 rows overall AND at least 6 per tercile (roughly)
        if n_rows < 3 * PER_STRATUM:
            status_rows.append({"cpv": cpv, "status": f"insufficient rows (need >= {3*PER_STRATUM})", "n_rows": n_rows, "n_authorities": n_auth})
            continue

        q1, q2 = sub["ICI_count"].quantile([0.33, 0.66]).values
        sub["stratum"] = pd.cut(
            sub["ICI_count"],
            [-np.inf, q1, q2, np.inf],
            labels=["low", "mid", "high"],
            include_lowest=True,
        )

        cpv_cases = []
        ok = True
        for s in ["low", "mid", "high"]:
            ss = sub[sub["stratum"] == s]
            if len(ss) < PER_STRATUM:
                status_rows.append({"cpv": cpv, "status": f"insufficient in '{s}' (need {PER_STRATUM}, have {len(ss)})", "n_rows": n_rows, "n_authorities": n_auth})
                ok = False
                break
            pick_idx = rng.choice(ss.index.to_numpy(), size=PER_STRATUM, replace=False)
            cpv_cases.append(ss.loc[pick_idx])

        if not ok:
            continue

        out = pd.concat(cpv_cases, axis=0).sort_values(["stratum", "ICI_count"]).reset_index(drop=True)

        # Avoid re-inserting cpv if already present
        if "cpv" not in out.columns:
            out.insert(0, "cpv", cpv)

        # -------------------------
        # Component sensitivity / stress-test fields (counterfactuals)
        # Counterfactual variants for validation (component sensitivity)
        # Computed for count- and value-based variants (when available)
        # -------------------------

        # Uniform concentration baseline: HHI if spend/awards were uniformly split across n_suppliers
        if "n_suppliers" in out.columns:
            out["HHI_uniform"] = 1.0 / out["n_suppliers"].replace({0: np.nan})

        # Count-based counterfactuals
        if all(c in out.columns for c in ["HHI_count", "rel_component_count"]):
            # "No relational persistence" as neutral multiplier 1: ICI reduces to HHI
            out["ICI_count_no_rel"] = out["HHI_count"]

            # "No concentration scaling": ICI reduces to relational component alone
            out["ICI_count_rel_only"] = out["rel_component_count"]

            # Uniform concentration * observed relational
            if "HHI_uniform" in out.columns:
                out["ICI_count_uniform_conc"] = out["HHI_uniform"] * out["rel_component_count"]

        # Value-based counterfactuals
        if all(c in out.columns for c in ["HHI_value", "rel_component_value"]):
            out["ICI_value_no_rel"] = out["HHI_value"]
            out["ICI_value_rel_only"] = out["rel_component_value"]
            if "HHI_uniform" in out.columns:
                out["ICI_value_uniform_conc"] = out["HHI_uniform"] * out["rel_component_value"]

        keep = [
            "cpv", "base_year", "aid", "authority", "stratum",
            "ICI_count", "HHI_count", "rel_component_count",
            "ICI_value", "HHI_value", "rel_component_value",
            "HHI_uniform", "n_dyads",
            "ICI_count_no_rel", "ICI_count_rel_only", "ICI_count_uniform_conc",
            "ICI_value_no_rel", "ICI_value_rel_only", "ICI_value_uniform_conc",
            "degA", "n_suppliers", "total_awards", "total_value",
        ]
        keep = [c for c in keep if c in out.columns]
        out = out[keep]

        per_cpv_outputs[cpv] = out
        all_cases.append(out)

        status_rows.append({"cpv": cpv, "status": "ok", "n_rows": n_rows, "n_authorities": n_auth})

        # Summary stats (stratum medians). Keep it robust to missing columns.
        agg_dict = {
            "n_cases": ("aid", "count"),
            "median_ICI": ("ICI_count", "median"),
            "min_ICI": ("ICI_count", "min"),
            "max_ICI": ("ICI_count", "max"),
        }
        if "HHI_count" in out.columns:
            agg_dict["median_HHI"] = ("HHI_count", "median")
        if "rel_component_count" in out.columns:
            agg_dict["median_rel"] = ("rel_component_count", "median")

        # Counterfactual medians (count)
        if "HHI_uniform" in out.columns:
            agg_dict["median_HHI_uniform"] = ("HHI_uniform", "median")
        if "ICI_count_no_rel" in out.columns:
            agg_dict["median_ICI_no_rel"] = ("ICI_count_no_rel", "median")
        if "ICI_count_rel_only" in out.columns:
            agg_dict["median_ICI_rel_only"] = ("ICI_count_rel_only", "median")
        if "ICI_count_uniform_conc" in out.columns:
            agg_dict["median_ICI_uniform_conc"] = ("ICI_count_uniform_conc", "median")

        # Counterfactual medians (value) — optional, only if present in out
        if "ICI_value" in out.columns:
            agg_dict["median_ICI_value"] = ("ICI_value", "median")
        if "ICI_value_no_rel" in out.columns:
            agg_dict["median_ICI_value_no_rel"] = ("ICI_value_no_rel", "median")
        if "ICI_value_rel_only" in out.columns:
            agg_dict["median_ICI_value_rel_only"] = ("ICI_value_rel_only", "median")
        if "ICI_value_uniform_conc" in out.columns:
            agg_dict["median_ICI_value_uniform_conc"] = ("ICI_value_uniform_conc", "median")

        ssum = out.groupby("stratum").agg(**agg_dict).reset_index()
        ssum.insert(0, "cpv", cpv)
        stats_frames.append(ssum)

    # write combined CSV (even if some CPVs fail)
    if all_cases:
        all_df = pd.concat(all_cases, axis=0).reset_index(drop=True)
    else:
        all_df = pd.DataFrame()

    all_df.to_csv(OUT_CSV, index=False, encoding="utf-8")

    # write Excel safely: always create at least one sheet
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        # Always write status sheet first (guarantees visible sheet)
        pd.DataFrame(status_rows).to_excel(w, sheet_name="status", index=False)

        if stats_frames:
            pd.concat(stats_frames, axis=0, ignore_index=True).to_excel(w, sheet_name="summary_stats", index=False)

        for cpv, out in per_cpv_outputs.items():
            out.to_excel(w, sheet_name=f"cases_{cpv}"[:31], index=False)

        # Also write all cases in one sheet (handy)
        all_df.to_excel(w, sheet_name="cases_all", index=False)

    print(f"Wrote: {OUT_XLSX}")
    print(f"Wrote: {OUT_CSV}")

if __name__ == "__main__":
    main()

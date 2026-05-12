import pandas as pd
import numpy as np

df = pd.read_excel("authority_panel_full.xlsx", sheet_name="panel_all")
df22 = df[df["base_year"] == 2022].copy()

d = (df22["ICI_value"] - df22["ICI_count"]).abs()
print("share exactly equal:", (d < 1e-12).mean())
print("max abs diff:", d.max())
print("median abs diff:", d.median())
print("corr:", df22["ICI_count"].corr(df22["ICI_value"]))

# per CPV: How medians differ
g = df22.groupby("cpv").agg(
    med_count=("ICI_count","median"),
    med_value=("ICI_value","median")
)
g["med_delta"] = g["med_value"] - g["med_count"]
print(g.sort_values("med_delta", ascending=False))

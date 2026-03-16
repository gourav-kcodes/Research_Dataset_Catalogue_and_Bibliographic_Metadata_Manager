import pandas as pd

df = pd.read_csv("../data/raw/zenodo_metadata.csv")

df["title"] = df["title"].str.strip()
df["authors"] = df["authors"].str.strip()

df["year"] = df["year"].astype(str).str[:4]

df = df.drop_duplicates(subset=["title","authors"])

df.to_csv("../data/cleaned/metadata_cleaned.csv", index=False)

print("Metadata cleaned successfully")

import pandas as pd

# Load raw metadata
df = pd.read_csv("data/raw/zenodo_metadata.csv")

# Remove extra spaces
df["title"] = df["title"].str.strip()
df["authors"] = df["authors"].str.strip()

# Keep only the year from publication date
df["year"] = df["year"].astype(str).str[:4]

# Remove duplicate records
df = df.drop_duplicates(subset=["title", "authors"])

# Save cleaned metadata
df.to_csv("data/cleaned/metadata_cleaned.csv", index=False)

print("Metadata cleaned successfully")

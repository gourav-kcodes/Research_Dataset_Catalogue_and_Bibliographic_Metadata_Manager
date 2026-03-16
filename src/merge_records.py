import pandas as pd

df = pd.read_csv("data/cleaned/metadata_cleaned.csv")

# Remove duplicates based on DOI if present
df = df.drop_duplicates(subset=["doi"], keep="first")

# Remove duplicates based on title
df = df.drop_duplicates(subset=["title"], keep="first")

df.to_csv("data/cleaned/metadata_final.csv", index=False)

print("Duplicate records merged successfully")

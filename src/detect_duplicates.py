import pandas as pd
from rapidfuzz import fuzz

# Load cleaned metadata
df = pd.read_csv("data/cleaned/metadata_cleaned.csv")

duplicates = []

for i in range(len(df)):
    for j in range(i + 1, len(df)):

        title1 = str(df.iloc[i]["title"])
        title2 = str(df.iloc[j]["title"])

        score = fuzz.ratio(title1, title2)

        if score >= 90:
            duplicates.append((i, j, score))

print("Potential duplicate records:")
for d in duplicates:
    print(d)

import sqlite3
import pandas as pd

df = pd.read_csv("../data/cleaned/metadata_cleaned.csv")

conn = sqlite3.connect("../database/datasets.db")

df.to_sql("datasets", conn, if_exists="replace", index=False)

conn.close()

print("Database created")

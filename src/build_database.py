import sqlite3
import pandas as pd

# Load cleaned metadata
df = pd.read_csv("data/cleaned/metadata_cleaned.csv")

# Create database connection
conn = sqlite3.connect("database/datasets.db")

# Store metadata in table
df.to_sql("datasets", conn, if_exists="replace", index=False)

# Create cursor
cursor = conn.cursor()

# Create indexes for faster search
cursor.execute("CREATE INDEX IF NOT EXISTS idx_author ON datasets(authors)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_title ON datasets(title)")
cursor.execute("CREATE INDEX IF NOT EXISTS idx_year ON datasets(year)")

conn.commit()

conn.close()

print("Database created successfully with indexes")

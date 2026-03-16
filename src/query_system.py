import sqlite3

conn = sqlite3.connect("database/datasets.db")

author = input("Enter author name: ")

query = """
SELECT title, authors, year, repository
FROM datasets
WHERE authors LIKE ?
"""

cursor = conn.cursor()

cursor.execute(query, ("%" + author + "%",))

results = cursor.fetchall()

for r in results:
    print(r)

conn.close()

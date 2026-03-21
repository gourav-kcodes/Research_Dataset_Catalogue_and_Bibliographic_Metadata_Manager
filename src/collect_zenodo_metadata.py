import requests
import pandas as pd

base_url = "https://zenodo.org/api/records"

records = []

params = {
    "q": "machine learning",
    "size": 24,
}

response = requests.get(base_url, params=params)

print("Status:", response.status_code)
print(response.text[:500])   # print first 500 characters

data = response.json()

for item in data.get("hits", {}).get("hits", []):

    metadata = item.get("metadata", {})

    title = metadata.get("title", "")

    creators = metadata.get("creators", [])
    authors = ", ".join([c.get("name", "") for c in creators])

    year = metadata.get("publication_date", "")

    doi = item.get("doi", "")

    description = metadata.get("description", "")

    keywords = metadata.get("keywords", [])
    keywords = ", ".join(keywords)

    link = item.get("links", {}).get("html", "")

    records.append({
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "keywords": keywords,
        "description": description,
        "url": link,
        "repository": "Zenodo"
    })

df = pd.DataFrame(records)

df.to_csv("data/raw/zenodo_metadata.csv", index=False)

print("Collected", len(df), "metadata records")

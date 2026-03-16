import requests
import pandas as pd

url = "https://zenodo.org/api/records"

params = {
    "q": "dataset",
    "size": 20
}

response = requests.get(url, params=params)

# Check if request succeeded
if response.status_code != 200:
    print("Error:", response.status_code)
    print(response.text)
    exit()

data = response.json()

# Check structure
if "hits" not in data:
    print("Unexpected response:")
    print(data)
    exit()

records = []

for item in data["hits"]["hits"]:

    metadata = item.get("metadata", {})

    title = metadata.get("title", "")

    creators = metadata.get("creators", [])
    authors = ", ".join([c.get("name","") for c in creators])

    year = metadata.get("publication_date", "")

    doi = item.get("doi", "")

    description = metadata.get("description", "")

    keywords = metadata.get("keywords", [])
    keywords = ", ".join(keywords)

    url_link = item["links"]["html"]

    records.append({
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "keywords": keywords,
        "description": description,
        "url": url_link,
        "repository": "Zenodo"
    })

df = pd.DataFrame(records)

df.to_csv("../data/raw/zenodo_metadata.csv", index=False)

print("Metadata collected successfully!")

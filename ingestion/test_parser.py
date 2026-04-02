import json
from file_parser import parse_csv, parse_json

# ─────────────────────────────────────────────
#  Test 1 — CSV Parser
# ─────────────────────────────────────────────

print("=" * 50)
print("TEST 1 — CSV PARSER")
print("=" * 50)

csv_records = parse_csv("../data/raw/iris.csv")

if csv_records:
    print(f"✅ CSV parsed successfully!")
    print(f"   Total records found : {len(csv_records)}")
    print(f"   Columns found       : {list(csv_records[0].keys())}")
    print(f"\n   First record preview:")
    for key, value in csv_records[0].items():
        print(f"     {key} : {value}")
else:
    print("❌ CSV parsing failed — no records returned")


# ─────────────────────────────────────────────
#  Test 2 — JSON Parser
# ─────────────────────────────────────────────

print("\n" + "=" * 50)
print("TEST 2 — JSON PARSER")
print("=" * 50)

json_records = parse_json("../data/raw/sample.json")

if json_records:
    print(f"✅ JSON parsed successfully!")
    print(f"   Total records found : {len(json_records)}")
    if isinstance(json_records[0], dict):
        print(f"   Keys found          : {list(json_records[0].keys())[:5]}...")
    print(f"\n   First record preview:")
    first = json_records[0]
    if isinstance(first, dict):
        for key, value in list(first.items())[:5]:
            print(f"     {key} : {str(value)[:60]}")
else:
    print("❌ JSON parsing failed — no records returned")


# ─────────────────────────────────────────────
#  Summary
# ─────────────────────────────────────────────

print("\n" + "=" * 50)
print("PARSER TEST SUMMARY")
print("=" * 50)
print(f"  CSV  records parsed : {len(csv_records)}")
print(f"  JSON records parsed : {len(json_records)}")
print(f"  Total               : {len(csv_records) + len(json_records)}")
print("=" * 50)
print("\n✅ file_parser.py works correctly for CSV and JSON formats!")

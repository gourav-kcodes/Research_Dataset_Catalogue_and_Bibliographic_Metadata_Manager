import os, sqlite3, time
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

DB = "datasets.db"
app = FastAPI(title="NEXUS Research API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_cache = {}

def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def cols():
    with get_conn() as c:
        return [r[1] for r in c.execute("PRAGMA table_info(datasets)")]

# ── API Info ──────────────────────────────────────────────────────────────────
@app.get("/api/info")
def info():
    with get_conn() as c:
        total = c.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        min_y = c.execute("SELECT MIN(year) FROM datasets").fetchone()[0] or 1990
        max_y = c.execute("SELECT MAX(year) FROM datasets").fetchone()[0] or 2026
    return {"total": total, "min_year": int(min_y), "max_year": int(max_y), "columns": cols()}

# ── Search (Restored with Original Logic) ─────────────────────────────────────
@app.get("/api/search")
def search(q: str = "", field: str = "all", year_from: int = 1990, year_to: int = 2026, limit: int = 50, offset: int = 0):
    t0 = time.time()
    params = [year_from, year_to]
    where = "WHERE year BETWEEN ? AND ?"
    
    if q.strip():
        term = f"%{q.strip()}%"
        if field == "author": where += " AND authors LIKE ?"; params.append(term)
        elif field == "doi": where += " AND doi LIKE ?"; params.append(term)
        else: 
            where += " AND (title LIKE ? OR keywords LIKE ? OR description LIKE ?)"
            params += [term, term, term]

    count_sql = f"SELECT COUNT(*) FROM datasets {where}"
    # Re-mapping 'keywords' to 'categories' so app.js doesn't break
    data_sql = f"SELECT title, authors, year, doi, keywords as categories, url as arxiv_id, repository FROM datasets {where} ORDER BY year DESC LIMIT ? OFFSET ?"
    
    with get_conn() as c:
        total = c.execute(count_sql, params).fetchone()[0]
        rows = c.execute(data_sql, params + [limit, offset]).fetchall()
        
    return {"results": [dict(r) for r in rows], "total_count": total, "elapsed_ms": round((time.time()-t0)*1000, 1)}

# ── Analytics & Network (Emulated) ────────────────────────────────────────────
@app.get("/api/analytics")
def analytics(year_from: int = 1990, year_to: int = 2026):
    with get_conn() as c:
        by_year = dict(c.execute("SELECT year, COUNT(*) FROM datasets WHERE year BETWEEN ? AND ? GROUP BY year", (year_from, year_to)).fetchall())
        total = sum(by_year.values())
    return {"total": total, "with_doi": total, "by_year": by_year, "by_category": {"General": total}}

@app.get("/api/author_match")
def author_match(name: str):
    with get_conn() as c:
        rows = c.execute("SELECT DISTINCT authors FROM datasets WHERE authors LIKE ?", (f"%{name}%",)).fetchall()
    return {"matches": [{"name": r[0], "count": 1} for r in rows]}

@app.get("/api/researcher")
def researcher(name: str):
    with get_conn() as c:
        rows = c.execute("SELECT title, year, doi, keywords as categories FROM datasets WHERE authors LIKE ?", (f"%{name}%",)).fetchall()
    return {"papers": [dict(r) for r in rows], "by_year": {}}

@app.get("/api/orcid_lookup")
def orcid(name: str): return {"results": []}

# ── Serving ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    with open("INDEX2.html", encoding="utf-8") as f: return f.read()

@app.get("/app.js")
def serve_js(): return FileResponse("app.js")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
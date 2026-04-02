"""
NEXUS Research Discovery Engine — FastAPI Backend
Install: pip install fastapi uvicorn
Run:     uvicorn server:app --reload --port 8000
Then open: http://localhost:8000
"""

import os, sqlite3, time, re
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

DB = "datasets.db"
app = FastAPI(title="NEXUS Research API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Global Cache ──────────────────────────────────────────────────────────────
_cache = {}

# ── DB Helpers ────────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def cols():
    """Dynamically fetch columns to avoid SQL errors if schema differs."""
    try:
        with get_conn() as c:
            return [r[1] for r in c.execute("PRAGMA table_info(datasets)")]
    except:
        return []

# ── Error Handling (Prevents JSON.parse Errors) ───────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all to ensure we ALWAYS return JSON, never HTML."""
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "details": "Check if your DB columns match the query."}
    )

# ── Author Parsing Logic ──────────────────────────────────────────────────────
_AFF_PARENS = re.compile(r'\([^)]*\)')

def _parse_authors(raw: str) -> list[str]:
    if not raw: return []
    # Strip affiliations and normalize separators
    text = _AFF_PARENS.sub(' ', raw)
    text = re.sub(r'\s+and\s+', ';', text, flags=re.IGNORECASE)
    parts = text.split(';') if ';' in text else text.split(',')
    
    results = []
    seen = set()
    for p in parts:
        name = p.strip().strip(',').strip()
        if len(name) > 2 and name.lower() not in seen:
            seen.add(name.lower())
            results.append(name)
    return results

# ── Search (Robust Version) ───────────────────────────────────────────────────
@app.get("/api/search")
def search(q: str = "", field: str = "all", year_from: int = 1990, year_to: int = 2026, limit: int = 50, offset: int = 0):
    t0 = time.time()
    available = cols()
    
    # Build dynamic WHERE clause based on what columns actually exist
    params = [year_from, year_to]
    where = "WHERE year BETWEEN ? AND ?"
    
    if q.strip():
        term = f"%{q.strip()}%"
        if field == "author": 
            where += " AND authors LIKE ?"
            params.append(term)
        elif field == "doi": 
            where += " AND doi LIKE ?"
            params.append(term)
        else:
            # Only search columns that exist in your datasets.db
            conditions = ["title LIKE ?"]
            params.append(term)
            if "keywords" in available:
                conditions.append("keywords LIKE ?")
                params.append(term)
            if "description" in available:
                conditions.append("description LIKE ?")
                params.append(term)
            where += f" AND ({' OR '.join(conditions)})"

    # Map your DB columns to what the frontend expects
    cat_col = "keywords" if "keywords" in available else "NULL"
    url_col = "url" if "url" in available else "doi"
    repo_col = "repository" if "repository" in available else "NULL"

    data_sql = f"""
        SELECT title, authors, year, doi, 
        {cat_col} as categories, {url_col} as arxiv_id, {repo_col} as repository 
        FROM datasets {where} 
        ORDER BY year DESC LIMIT ? OFFSET ?
    """
    
    with get_conn() as c:
        total = c.execute(f"SELECT COUNT(*) FROM datasets {where}", params).fetchone()[0]
        rows = c.execute(data_sql, params + [limit, offset]).fetchall()
        
    return {
        "results": [dict(r) for r in rows], 
        "total_count": total, 
        "elapsed_ms": round((time.time()-t0)*1000, 1)
    }

# ── Author Profile ────────────────────────────────────────────────────────────
@app.get("/api/author_profile")
def author_profile(exact_name: str, year_from: int=1991, year_to: int=2026):
    available = cols()
    cat_col = "keywords" if "keywords" in available else "NULL"
    
    with get_conn() as c:
        rows = c.execute(
            f"SELECT title, authors, year, doi, {cat_col} as categories FROM datasets "
            f"WHERE authors LIKE ? AND year BETWEEN ? AND ? ORDER BY year DESC",
            (f"%{exact_name}%", year_from, year_to)
        ).fetchall()
        
    papers = [dict(r) for r in rows]
    collab_map = {}
    yr_map = {}

    for p in papers:
        yr = p['year']
        yr_map[yr] = yr_map.get(yr, 0) + 1
        # Track collaborators
        for other in _parse_authors(p['authors']):
            if other.lower() != exact_name.lower():
                collab_map[other] = collab_map.get(other, 0) + 1

    return {
        "papers": papers,
        "count": len(papers),
        "by_year": dict(sorted(yr_map.items())),
        "top_collabs": dict(sorted(collab_map.items(), key=lambda x:-x[1])[:10])
    }

# ── Author Network (The Collaboration Graph) ──────────────────────────────────
@app.get("/api/network")
def network(focus: str = "", year_from: int = 1990, year_to: int = 2026, max_nodes: int = 50):
    with get_conn() as c:
        # Fetch papers for the year range
        sql = "SELECT authors FROM datasets WHERE year BETWEEN ? AND ? AND authors IS NOT NULL"
        params = [year_from, year_to]
        if focus:
            sql += " AND authors LIKE ?"
            params.append(f"%{focus}%")
        
        rows = c.execute(sql + " LIMIT 1000", params).fetchall()

    author_counts = {}
    connections = [] # List of (authorA, authorB)

    for row in rows:
        names = _parse_authors(row[0])
        if len(names) < 2: continue
        
        for n in names:
            author_counts[n] = author_counts.get(n, 0) + 1
            
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                connections.append(tuple(sorted((names[i], names[j]))))

    # Filter to top nodes
    top_authors = sorted(author_counts.items(), key=lambda x: -x[1])[:max_nodes]
    top_names = {name for name, count in top_authors}

    edges = {}
    for edge in connections:
        if edge[0] in top_names and edge[1] in top_names:
            edges[edge] = edges.get(edge, 0) + 1

    nodes = [{"id": name, "label": name, "count": count, "group": hash(name)%6} 
             for name, count in top_authors]
    formatted_edges = [{"source": e[0], "target": e[1], "weight": w} for e, w in edges.items()]

    return {"nodes": nodes, "edges": formatted_edges}

# ── Original Dashboard API fallbacks ──────────────────────────────────────────
@app.get("/api/info")
def info():
    with get_conn() as c:
        total = c.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        min_y = c.execute("SELECT MIN(year) FROM datasets WHERE year>0").fetchone()[0] or 1990
        max_y = c.execute("SELECT MAX(year) FROM datasets WHERE year>0").fetchone()[0] or 2026
    return {"total": total, "min_year": int(min_y), "max_year": int(max_y), "columns": cols()}

@app.get("/api/author_match")
def author_match(name: str):
    with get_conn() as c:
        rows = c.execute("SELECT authors FROM datasets WHERE authors LIKE ? LIMIT 500", (f"%{name}%",)).fetchall()
        counts = {}
        for r in rows:
            for a in _parse_authors(r[0]):
                if name.lower() in a.lower():
                    counts[a] = counts.get(a, 0) + 1
    return {"matches": [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x:-x[1])[:20]]}

@app.get("/")
def root():
    return HTMLResponse(content=open("INDEX2.html", encoding="utf-8").read())

@app.get("/app.js")
def serve_js():
    return FileResponse("app.js")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
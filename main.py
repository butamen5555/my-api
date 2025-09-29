import os
import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

DATABASE_URL = os.environ["DATABASE_URL"]

# ----------------------
# FastAPI 初期化
# ----------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------
# モデル定義
# ----------------------
class User(BaseModel):
    username: str
    password: str

class Features(BaseModel):
    ally_early_win: Optional[bool] = None
    ally_late_win: Optional[bool] = None
    close_game: Optional[bool] = None
    pachinko: Optional[bool] = None
    last_hit: Optional[bool] = None

class Match(BaseModel):
    ally_win: bool
    patch: Optional[str] = "シーズン30"
    ally_team: List[str]
    enemy_team: List[str]
    features: Optional[Features] = None
    user_id: Optional[int] = None

# ----------------------
# DB 初期化関数
# ----------------------
def init_db():
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        match_id SERIAL PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        ally_win BOOLEAN,
        patch TEXT,
        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teams (
        team_id SERIAL PRIMARY KEY,
        match_id INTEGER REFERENCES matches(match_id),
        pokemon TEXT,
        team TEXT CHECK(team IN ('ally','enemy'))
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS features (
        feature_id SERIAL PRIMARY KEY,
        match_id INTEGER REFERENCES matches(match_id),
        ally_early_win BOOLEAN,
        ally_late_win BOOLEAN,
        close_game BOOLEAN,
        pachinko BOOLEAN,
        last_hit BOOLEAN
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_pokemon ON teams(pokemon)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_user ON matches(user_id)")
    conn.commit()
    conn.close()
    print("DB初期化完了")

init_db()

# ----------------------
# DB登録処理
# ----------------------
def add_match_to_db(match: Match, user_id:int):
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO matches (user_id, ally_win, patch) VALUES (%s, %s, %s) RETURNING match_id",
        (user_id, match.ally_win, match.patch)
    )
    match_id = cursor.fetchone()[0]

    for p in match.ally_team:
        cursor.execute(
            "INSERT INTO teams (match_id, pokemon, team) VALUES (%s, %s, %s)",
            (match_id, p, "ally")
        )
    for p in match.enemy_team:
        cursor.execute(
            "INSERT INTO teams (match_id, pokemon, team) VALUES (%s, %s, %s)",
            (match_id, p, "enemy")
        )

    f = match.features
    if f:
        cursor.execute(
            """INSERT INTO features 
               (match_id, ally_early_win, ally_late_win, close_game, pachinko, last_hit)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (match_id, f.ally_early_win, f.ally_late_win, f.close_game, f.pachinko, f.last_hit)
        )

    conn.commit()
    conn.close()

# ----------------------
# ユーザー管理
# ----------------------
@app.post("/register/")
def register(user: User):
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s)",
            (user.username, user.password)
        )
        conn.commit()
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        raise HTTPException(status_code=400, detail="ユーザー名はすでに存在します")
    finally:
        conn.close()
    return {"status": "success", "message": "ユーザー登録完了"}

@app.post("/login/")
def login(user: User):
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM users WHERE username=%s AND password=%s",
        (user.username, user.password)
    )
    row = cursor.fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが違います")
    return {"status": "success", "user_id": row[0]}

# ----------------------
# マッチ追加
# ----------------------
@app.post("/add_match/")
def add_match(match: Match):
    add_match_to_db(match, match.user_id)
    return {"status": "success", "message": "Match added!"}

@app.post("/add_match_reverse/")
def add_match_reverse(match: Match):
    reversed_features = None
    if match.features:
        f = match.features
        reversed_features = Features(
            ally_early_win=(not f.ally_early_win if f.ally_early_win is not None else None),
            ally_late_win=(not f.ally_late_win if f.ally_late_win is not None else None),
            close_game=f.close_game,
            pachinko=f.pachinko,
            last_hit=(not f.last_hit if f.last_hit is not None else None)
        )
    reversed_match = Match(
        ally_win=not match.ally_win,
        patch=match.patch,
        ally_team=match.enemy_team,
        enemy_team=match.ally_team,
        features=reversed_features,
        user_id=match.user_id
    )
    add_match_to_db(reversed_match, match.user_id or 0)
    return {"status": "success", "message": "Reversed match added!"}

# ----------------------
# 検索・分析
# ----------------------
def search_matches_core(ally: List[str] = None, enemy: List[str] = None, user_id: Optional[int] = None):
    conn = psycopg2.connect(DATABASE_URL)
    ally = ally or []
    enemy = enemy or []
    cursor = conn.cursor()
    params = []
    conds = []

    # ally条件
    if ally:
        placeholders = ",".join(["%s"] * len(ally))
        conds.append(f"SUM(CASE WHEN t.team='ally' AND t.pokemon IN ({placeholders}) THEN 1 ELSE 0 END) = %s")
        params.extend(ally)
        params.append(len(ally))

    # enemy条件
    if enemy:
        placeholders = ",".join(["%s"] * len(enemy))
        conds.append(f"SUM(CASE WHEN t.team='enemy' AND t.pokemon IN ({placeholders}) THEN 1 ELSE 0 END) = %s")
        params.extend(enemy)
        params.append(len(enemy))

    # query
    query = """
        SELECT
            m.match_id,
            m.ally_win,
            m.patch,
            t.pokemon,
            t.team,
            f.ally_early_win,
            f.ally_late_win,
            f.close_game,
            f.pachinko,
            f.last_hit
        FROM matches m
        JOIN teams t ON m.match_id = t.match_id
        LEFT JOIN features f ON m.match_id = f.match_id
    """

    query_conds = []
    if user_id is not None:
        query_conds.append("m.user_id = %s")
        params.insert(0, user_id)

    if query_onds := query_conds:
        query += " WHERE " + " AND ".join(query_conds)

    query += " GROUP BY m.match_id, t.pokemon, t.team, f.ally_early_win, f.ally_late_win, f.close_game, f.pachinko, f.last_hit"

    if conds:
        query += " HAVING " + " AND ".join(conds)

    cursor.execute(query, tuple(params))
    rows = cursor.fetchall()

    # match_idごとにまとめる
    matches_dict = {}
    for row in rows:
        match_id, ally_win, patch, pokemon, team, early, late, close, pachinko, last_hit = row
        if match_id not in matches_dict:
            matches_dict[match_id] = {
                "match_id": match_id,
                "ally_win": ally_win,
                "patch": patch,
                "ally_team": [],
                "enemy_team": [],
                "features": {
                    "ally_early_win": early,
                    "ally_late_win": late,
                    "close_game": close,
                    "pachinko": pachinko,
                    "last_hit": last_hit
                }
            }
        if team == "ally":
            matches_dict[match_id]["ally_team"].append(pokemon)
        else:
            matches_dict[match_id]["enemy_team"].append(pokemon)

    return {"matches": list(matches_dict.values())}



def analyze_data(ally: List[str], enemy: List[str], user_id: Optional[int]=None):
    matches_data = search_matches_core(ally, enemy, user_id)["matches"]
    total = len(matches_data)
    wins = sum(1 for m in matches_data if m["ally_win"])
    feature_counts = {}
    for m in matches_data:
        if m["features"]:
            for k, v in m["features"].items():
                if isinstance(v, bool):
                    feature_counts[k] = feature_counts.get(k, 0) + int(v)
                else:
                    feature_counts[k] = feature_counts.get(k, 0) + 1
    summary = {
        "total_matches": total,
        "win_rate": wins / total if total > 0 else None,
        "feature_rates": {k: c / total for k, c in feature_counts.items()} if total > 0 else {}
    }
    return {"matches": matches_data, "summary": summary}

@app.get("/search_matches/")
def search_matches(ally: List[str] = Query(default=[]), enemy: List[str] = Query(default=[])):
    return search_matches_core(ally, enemy)

@app.get("/search_next1/")
def search_next1(ally: List[str] = Query(default=[]), enemy: List[str] = Query(default=[]),
                 excess: List[str] = Query(default=[]), user_id: Optional[int] = Query(default=None)):
    suggest = {}
    for i in excess:
        new_ally = ally + [i]
        data_analyzed = analyze_data(new_ally, enemy, user_id)
        if data_analyzed["summary"]["total_matches"] > 0:
            suggest[i] = data_analyzed["summary"]
    suggest = dict(sorted(suggest.items(), key=lambda x: (x[1]["win_rate"] or 0), reverse=True)[:5])
    return suggest

@app.get("/search_next2/")
def search_next2(ally: List[str] = Query(default=[]), enemy: List[str] = Query(default=[]),
                 excess: List[str] = Query(default=[])):
    suggest = {}
    single_scores = {}
    for p in excess:
        new_ally = ally + [p]
        data_analyzed = analyze_data(new_ally, enemy)
        if data_analyzed["summary"]["total_matches"] > 0:
            single_scores[p] = data_analyzed["summary"]
    top10 = sorted(single_scores.items(), key=lambda x: (x[1]["win_rate"] or 0), reverse=True)[:10]
    top10_names = [p for p, _ in top10]

    for idx_i, i in enumerate(top10_names):
        for j in top10_names[idx_i+1:]:
            new_ally = ally + [i, j]
            data_analyzed = analyze_data(new_ally, enemy)
            if data_analyzed["summary"]["total_matches"] > 0:
                suggest[f"{i},{j}"] = data_analyzed["summary"]

    suggest = dict(sorted(suggest.items(), key=lambda x: (x[1]["win_rate"] or 0), reverse=True)[:5])
    return suggest

# ----------------------
# 起動
# ----------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        reload=True
    )




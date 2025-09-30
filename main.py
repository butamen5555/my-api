import os
import psycopg2
from psycopg2 import pool
from fastapi import FastAPI, HTTPException, Query
from fastapi import APIRouter
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import time
import json
from psycopg2.extras import Json, execute_values
from datetime import datetime, timedelta

CACHE_EXPIRE_MINUTES = 60

DATABASE_URL = os.environ["DATABASE_URL"]
# ①接続プールを作成（アプリ起動時に1回だけ）
# アプリ起動時に接続プールを作成（最小1, 最大10接続）
db_pool = pool.SimpleConnectionPool(
    1, 10,
    dsn="postgresql://user:vSETJ5tNjJIu5Y88jawMJgFq9lvitWgG@dpg-d3d1ag3uibrs738athdg-a.singapore-postgres.render.com/unitehub"
)


# ----------------------
# FastAPI 初期化
# ----------------------
app = FastAPI()
router = APIRouter()
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

class SuggestRequest(BaseModel):
    ally: List[str]
    enemy: List[str]
    excess: List[str]
    user_id: Optional[str] = None

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
 # キャッシュテーブル
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cache (
        key TEXT PRIMARY KEY,
        value JSONB NOT NULL,
        expires_at TIMESTAMP NOT NULL
    )
    """)

    # index
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_pokemon ON teams(pokemon)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_user ON matches(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at)")

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
# キャッシュ作成処理
# ----------------------
def get_cache(conn, key: str):
    cursor = conn.cursor()
    cursor.execute(
        "SELECT value, expires_at FROM cache WHERE key = %s",
        (key,)
    )
    row = cursor.fetchone()
    if row:
        value, expires_at = row
        if expires_at > datetime.utcnow():
            return value
        else:
            # 期限切れ
            cursor.execute("DELETE FROM cache WHERE key = %s", (key,))
            conn.commit()
    return None

def set_cache(conn, key: str, value: dict):
    cursor = conn.cursor()
    expires_at = datetime.utcnow() + timedelta(minutes=CACHE_EXPIRE_MINUTES)
    cursor.execute(
        """
        INSERT INTO cache (key, value, expires_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value,
            expires_at = EXCLUDED.expires_at
        """,
        (key, json.dumps(value), expires_at)
    )
    conn.commit()
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
    ally = ally or []
    enemy = enemy or []

    start_total = time.time()

    # プールから接続を取得
    conn_start = time.time()
    conn = db_pool.getconn()
    print("DB connect time:", time.time() - conn_start)

    matches_data = []

    try:
        with conn.cursor() as cursor:
            params = []
            conds = []

            if ally:
                placeholders = ",".join(["%s"] * len(ally))
                conds.append(f"SUM(CASE WHEN t.team='ally' AND t.pokemon IN ({placeholders}) THEN 1 ELSE 0 END) = %s")
                params.extend(ally)
                params.append(len(ally))

            if enemy:
                placeholders = ",".join(["%s"] * len(enemy))
                conds.append(f"SUM(CASE WHEN t.team='enemy' AND t.pokemon IN ({placeholders}) THEN 1 ELSE 0 END) = %s")
                params.extend(enemy)
                params.append(len(enemy))

            query = "SELECT t.match_id FROM teams t JOIN matches m ON t.match_id = m.match_id"
            query_conds = []
            if user_id is not None:
                query_conds.append("m.user_id = %s")
                params.insert(0, user_id)

            if query_conds:
                query += " WHERE " + " AND ".join(query_conds)
            query += " GROUP BY t.match_id"
            if conds:
                query += " HAVING " + " AND ".join(conds)

            start_query = time.time()
            cursor.execute(query, tuple(params))
            match_ids = [row[0] for row in cursor.fetchall()]
            print("Query match_ids time:", time.time() - start_query, "matches found:", len(match_ids))

            if not match_ids:
                return {"matches": []}

            # match_ids に対する情報をまとめて取得（IN句）
            t0 = time.time()
            cursor.execute(
                "SELECT match_id, ally_win, patch FROM matches WHERE match_id = ANY(%s)",
                (match_ids,)
            )
            matches_rows = {row[0]: {"ally_win": row[1], "patch": row[2]} for row in cursor.fetchall()}
            print("Query all matches time:", time.time() - t0)

            t1 = time.time()
            cursor.execute(
                "SELECT match_id, pokemon, team FROM teams WHERE match_id = ANY(%s)",
                (match_ids,)
            )
            teams_dict = {}
            for match_id_row, pokemon, team in cursor.fetchall():
                if match_id_row not in teams_dict:
                    teams_dict[match_id_row] = {"ally": [], "enemy": []}
                teams_dict[match_id_row][team].append(pokemon)
            print("Query all teams time:", time.time() - t1)

            t2 = time.time()
            cursor.execute(
                "SELECT match_id, ally_early_win, ally_late_win, close_game, pachinko, last_hit FROM features WHERE match_id = ANY(%s)",
                (match_ids,)
            )
            features_dict = {}
            keys = ["ally_early_win", "ally_late_win", "close_game", "pachinko", "last_hit"]
            for row in cursor.fetchall():
                match_id_row = row[0]
                features_dict[match_id_row] = {k: v for k, v in zip(keys, row[1:])}
            print("Query all features time:", time.time() - t2)

            # 結合
            for match_id in match_ids:
                matches_data.append({
                    "match_id": match_id,
                    "ally_win": matches_rows.get(match_id, {}).get("ally_win"),
                    "patch": matches_rows.get(match_id, {}).get("patch"),
                    "ally_team": teams_dict.get(match_id, {}).get("ally", []),
                    "enemy_team": teams_dict.get(match_id, {}).get("enemy", []),
                    "features": features_dict.get(match_id, {k: None for k in keys})
                })

    finally:
        # プールに接続を返却
        db_pool.putconn(conn)

    print("Total function time:", time.time() - start_total)
    return {"matches": matches_data}



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

@app.post("/search_next1/")
def search_next1_post(req: SuggestRequest):
    suggest = {}
    conn = psycopg2.connect(DATABASE_URL)

    for p in req.excess:
        # キャッシュキー作成
        key = f"ally:{','.join(req.ally + [p])}|enemy:{','.join(req.enemy)}|user:{req.user_id}"
        cached = get_cache(conn, key)
        if cached:
            suggest[p] = cached
            continue

        # キャッシュなし → 計算
        new_ally = req.ally + [p]
        data_analyzed = analyze_data(new_ally, req.enemy, req.user_id)
        summary = data_analyzed["summary"]

        if summary["total_matches"] > 0:
            suggest[p] = summary
            set_cache(conn, key, summary)

    conn.close()
    # 勝率でソートして上位5件
    suggest = dict(sorted(suggest.items(), key=lambda x: (x[1]["win_rate"] or 0), reverse=True)[:5])
    return suggest



@app.post("/search_next2/")
def search_next2_post(req: SuggestRequest):
    suggest = {}
    single_scores = {}
    for p in req.excess:
        new_ally = req.ally + [p]
        data_analyzed = analyze_data(new_ally, req.enemy)
        if data_analyzed["summary"]["total_matches"] > 0:
            single_scores[p] = data_analyzed["summary"]

    top10 = sorted(single_scores.items(), key=lambda x: (x[1]["win_rate"] or 0), reverse=True)[:10]
    top10_names = [p for p, _ in top10]

    for idx_i, i in enumerate(top10_names):
        for j in top10_names[idx_i+1:]:
            new_ally = req.ally + [i, j]
            data_analyzed = analyze_data(new_ally, req.enemy)
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




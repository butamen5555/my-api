import os
import sqlite3
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from fastapi import Query

DB_FILE = "matches.db"

# ----------------------
# DB 初期化関数
# ----------------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS matches (
        match_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        ally_win BOOLEAN,
        patch TEXT,
        date TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS teams (
        team_id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER,
        pokemon TEXT,
        team TEXT CHECK(team IN ('ally','enemy')),
        FOREIGN KEY(match_id) REFERENCES matches(match_id))
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS features (
        feature_id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER,
        ally_early_win BOOLEAN DEFAULT NULL,
        ally_late_win BOOLEAN DEFAULT NULL,
        close_game BOOLEAN DEFAULT NULL,
        pachinko BOOLEAN DEFAULT NULL,
        last_hit BOOLEAN DEFAULT NULL,
        FOREIGN KEY(match_id) REFERENCES matches(match_id)
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_teams_pokemon ON teams(pokemon)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_user ON matches(user_id)")
    conn.commit()
    conn.close()
    print("DBとテーブルを作成しました")

# ----------------------
# DBがなければ作る
# ----------------------
if not os.path.exists(DB_FILE):
    init_db()

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
    user_id: Optional[int] = None  # ユーザーIDを追加

# ----------------------
# DB登録処理
# ----------------------
def add_match_to_db(match: Match, user_id:int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # matchesテーブル
    cursor.execute(
        "INSERT INTO matches (user_id, ally_win, patch) VALUES (?, ?, ?)",
        (user_id, match.ally_win, match.patch)
    )
    match_id = cursor.lastrowid
    print("Inserted match_id:", match_id, "user_id:", user_id)  # ←追加

    # teamsテーブル
    for p in match.ally_team:
        cursor.execute(
            "INSERT INTO teams (match_id, pokemon, team) VALUES (?, ?, ?)",
            (match_id, p, "ally")
        )
    for p in match.enemy_team:
        cursor.execute(
            "INSERT INTO teams (match_id, pokemon, team) VALUES (?, ?, ?)",
            (match_id, p, "enemy")
        )

    # featuresテーブル
    f = match.features
    if f:
        cursor.execute(
            """INSERT INTO features 
               (match_id, ally_early_win, ally_late_win, close_game, pachinko, last_hit)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (match_id, f.ally_early_win, f.ally_late_win, f.close_game, f.pachinko, f.last_hit)
        )

    conn.commit()
    conn.close()

# ----------------------
# APIエンドポイント
# ----------------------
@app.post("/add_match/")#試合を追加
def add_match(match: Match):
    print("Received match:", match)
    add_match_to_db(match, match.user_id)
    return {"status": "success", "message": "Match added!"}

@app.post("/add_match_reverse/")
def add_match_reverse(match: Match):
    reversed_features = None
    if match.features:
        f = match.features
        reversed_features = Features(
            ally_early_win = (not f.ally_early_win if f.ally_early_win is not None else None),
            ally_late_win  = (not f.ally_late_win if f.ally_late_win is not None else None),
            close_game     = f.close_game,
            pachinko       = f.pachinko,
            last_hit       = (not f.last_hit if f.last_hit is not None else None)  # 修正済み
        )

    reversed_match = Match(
        ally_win = not match.ally_win,
        patch    = match.patch,
        ally_team = match.enemy_team,
        enemy_team = match.ally_team,
        features = reversed_features,
        user_id = match.user_id  # ユーザーID引き継ぎ
    )
    add_match_to_db(reversed_match, 0)
    return {"status": "success", "message": "Reversed match added!"}

@app.get("/matches/")#全情報をget
def get_matches():
    conn = sqlite3.connect(DB_FILE)
    cursor1 = conn.cursor()
    cursor1.execute("SELECT * FROM matches")
    rows1 = cursor1.fetchall()

    cursor2 = conn.cursor()
    cursor2.execute("SELECT * FROM teams")
    rows2 = cursor2.fetchall()

    cursor3 = conn.cursor()
    cursor3.execute("SELECT * FROM features")
    rows3 = cursor3.fetchall()
    conn.close()
    return {"matches": rows1 ,"teams" : rows2 , "features" : rows3}

@app.post("/register/")
def register(user: User):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                       (user.username, user.password))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="ユーザー名はすでに存在します")
    finally:
        conn.close()

    return {"status": "success", "message": "ユーザー登録完了"}

@app.post("/login/")
def login(user: User):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM users WHERE username=? AND password=?", 
                   (user.username, user.password))
    row = cursor.fetchone()
    conn.close()

    if row is None:
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが違います")

    return {"status": "success", "user_id": row[0]}

# ----------------------
# 検索エンジン
# ----------------------
def search_matches_core(ally: List[str] = None, enemy: List[str] = None, user_id: Optional[int] = None):
    import sqlite3
    ally = ally or []
    enemy = enemy or []

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    params = []
    conds = []

    # --- 味方チーム条件 ---
    if ally:
        placeholders = ",".join("?" for _ in ally)
        conds.append(f"SUM(CASE WHEN t.team='ally' AND t.pokemon IN ({placeholders}) THEN 1 ELSE 0 END) = ?")
        params.extend(ally)
        params.append(len(ally))

    # --- 敵チーム条件 ---
    if enemy:
        placeholders = ",".join("?" for _ in enemy)
        conds.append(f"SUM(CASE WHEN t.team='enemy' AND t.pokemon IN ({placeholders}) THEN 1 ELSE 0 END) = ?")
        params.extend(enemy)
        params.append(len(enemy))

    # --- クエリ作成 ---
    query = """
        SELECT t.match_id
        FROM teams t
        JOIN matches m ON t.match_id = m.match_id
    """
    query_conds = []

    if user_id is not None:
        query_conds.append("m.user_id = ?")
        params.insert(0, user_id)  # user_idを先頭に

    query += f" WHERE {' AND '.join(query_conds)}" if query_conds else ""
    query += " GROUP BY t.match_id"
    if conds:
        query += f" HAVING {' AND '.join(conds)}"

    cursor.execute(query, tuple(params))
    match_ids = [row[0] for row in cursor.fetchall()]

    # --- 実際の試合データ取得 ---
    matches_data = []
    for match_id in match_ids:
        cursor.execute("SELECT ally_win, patch FROM matches WHERE match_id = ?", (match_id,))
        match_row = cursor.fetchone()
        if not match_row:
            continue
        ally_win, patch = match_row

        cursor.execute("SELECT pokemon, team FROM teams WHERE match_id = ?", (match_id,))
        teams_rows = cursor.fetchall()
        ally_team = [p for p, t in teams_rows if t == "ally"]
        enemy_team = [p for p, t in teams_rows if t == "enemy"]

        cursor.execute(
            "SELECT ally_early_win, ally_late_win, close_game, pachinko, last_hit FROM features WHERE match_id = ?",
            (match_id,)
        )
        features_row = cursor.fetchone()
        features = None
        if features_row:
            keys = ["ally_early_win", "ally_late_win", "close_game", "pachinko", "last_hit"]
            features = {k: v for k, v in zip(keys, features_row) if v is not None}

        matches_data.append({
            "match_id": match_id,
            "ally_win": ally_win,
            "patch": patch,
            "ally_team": ally_team,
            "enemy_team": enemy_team,
            "features": features
        })

    conn.close()
    return {"matches": matches_data}



def analyze_data(ally: List[str], enemy: List[str] ,user_id:Optional[int]= None):
    matches_data = search_matches_core(ally, enemy,user_id)["matches"]

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
def search_matches(
    ally: List[str] = Query(default=[]),
    enemy: List[str] = Query(default=[])
):
    return search_matches_core(ally, enemy)

@app.get("/search_next1/")
def search_next1(
    ally: List[str] = Query(default=[]),
    enemy: List[str] = Query(default=[]),
    excess: List[str] = Query(default=[]),
    user_id: Optional[int] = Query(default=None) 
):  
    print("aaaaaaaaaaaaaaaaaaaaaa:",user_id)
    suggest = {}
    for i in excess:
        # 新しいリストを作って渡す
        new_ally = ally + [i]
        data_analyzed = analyze_data(new_ally, enemy , user_id)
        if data_analyzed["summary"]["total_matches"] > 0:   # ★ ここでフィルタ
            key = f"{i}"
            suggest[key] = data_analyzed["summary"]
     # ④ 上位5件に絞る
    suggest = dict(sorted(suggest.items(), key=lambda x: (x[1]["win_rate"] or 0), reverse=True)[:5])
    return suggest

@app.get("/search_next2/")
def search_next2(
    ally: List[str] = Query(default=[]),
    enemy: List[str] = Query(default=[]),
    excess: List[str] = Query(default=[])
):
    suggest = {}

    # ① 単体勝率を計算
    single_scores = {}
    for p in excess:
        new_ally = ally + [p]
        data_analyzed = analyze_data(new_ally, enemy)
        # ある程度試合数がある場合のみ候補にする
        if data_analyzed["summary"]["total_matches"] > 0:
            single_scores[p] = data_analyzed["summary"]

    # ② 上位10を抽出
    top10 = sorted(single_scores.items(), key=lambda x: (x[1]["win_rate"] or 0), reverse=True)[:10]
    top10_names = [p for p, _ in top10]

    # ③ 上位10でペアを作って勝率計算
    for idx_i, i in enumerate(top10_names):
        for j in top10_names[idx_i+1:]:
            new_ally = ally + [i, j]
            data_analyzed = analyze_data(new_ally, enemy)
            if data_analyzed["summary"]["total_matches"] > 0:   # ★ ここでフィルタ
                key = f"{i},{j}"
                suggest[key] = data_analyzed["summary"]

    # ④ 上位5件に絞る
    suggest = dict(sorted(suggest.items(), key=lambda x: (x[1]["win_rate"] or 0), reverse=True)[:5])

    return suggest







#   uvicorn main:app --reload --port 8080







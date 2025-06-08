# db.py
import sqlite3
from datetime import datetime, date, timedelta

conn = sqlite3.connect("trainer.db", check_same_thread=False)
cursor = conn.cursor()

# 사용자
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    nickname TEXT,
    badge_weekly INTEGER DEFAULT 0,
    badge_monthly INTEGER DEFAULT 0,
    badge_bikini INTEGER DEFAULT 0
)
""")

# 목표(최대 3개, type: weight, freq_exercise, freq_diet, last_modified 추가)
cursor.execute("""
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    type TEXT,
    start_date TEXT,
    end_date TEXT,
    target_weight REAL,
    current_weight REAL,
    freq_per_week INTEGER,
    last_modified TEXT,
    active INTEGER DEFAULT 1,
    UNIQUE(user_id, type)
)
""")

# 운동/식단 인증 로그
cursor.execute("""
CREATE TABLE IF NOT EXISTS exercise_log (
    user_id TEXT,
    date TEXT,
    count INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, date)
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS diet_log (
    user_id TEXT,
    date TEXT,
    count INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, date)
)
""")

# 주간/월간 상태
cursor.execute("""
CREATE TABLE IF NOT EXISTS weekly_status (
    user_id TEXT,
    week_start TEXT,
    achieved_exercise INTEGER DEFAULT 0,
    achieved_diet INTEGER DEFAULT 0,
    weight_updated INTEGER DEFAULT 0,
    achieved_weight INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, week_start)
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS monthly_trophy (
    user_id TEXT,
    year_month TEXT,
    won_trophy INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, year_month)
)
""")
conn.commit()

def _register_user(user_id: str, nickname: str):
    cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
    if cursor.fetchone() is None:
        cursor.execute("INSERT INTO users (user_id, nickname) VALUES (?, ?)", (user_id, nickname))
        conn.commit()

def set_weight_goal(user_id, nickname, start_date, end_date, target_weight, current_weight):
    _register_user(user_id, nickname)
    cursor.execute("UPDATE goals SET active = 0 WHERE user_id = ? AND type = 'weight'", (user_id,))
    now = datetime.now().isoformat()
    cursor.execute("""
    INSERT INTO goals (user_id, type, start_date, end_date, target_weight, current_weight, freq_per_week, last_modified)
    VALUES (?, 'weight', ?, ?, ?, ?, NULL, ?)""", (user_id, start_date, end_date, target_weight, current_weight, now))
    conn.commit()

def set_freq_goal(user_id, nickname, goal_type, freq_per_week):
    _register_user(user_id, nickname)
    cursor.execute("UPDATE goals SET active = 0 WHERE user_id = ? AND type = ?", (user_id, goal_type))
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat()
    cursor.execute("""
    INSERT INTO goals (user_id, type, start_date, end_date, target_weight, current_weight, freq_per_week, last_modified)
    VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?)""", (user_id, goal_type, today, freq_per_week, now))
    conn.commit()

def delete_goal(user_id, goal_type):
    cursor.execute("UPDATE goals SET active = 0 WHERE user_id = ? AND type = ?", (user_id, goal_type))
    conn.commit()

def get_active_goals(user_id):
    cursor.execute("""
    SELECT type, start_date, end_date, target_weight, current_weight, freq_per_week, last_modified
      FROM goals WHERE user_id = ? AND active = 1
    """, (user_id,))
    return cursor.fetchall()

def get_goal_last_modified(user_id, goal_type):
    cursor.execute("""
    SELECT last_modified FROM goals WHERE user_id = ? AND type = ? AND active = 1
    """, (user_id, goal_type))
    row = cursor.fetchone()
    return row[0] if row else None

def update_current_weight(user_id, new_weight):
    cursor.execute("""
        UPDATE goals SET current_weight = ?, last_modified = ?
        WHERE user_id = ? AND type = 'weight' AND active = 1
    """, (new_weight, datetime.now().isoformat(), user_id))
    conn.commit()

    def increment_exercise_log(user_id, when_date=None):
        if when_date is None:
            when_date = datetime.now().strftime("%Y-%m-%d")
        cursor.execute("SELECT count FROM exercise_log WHERE user_id = ? AND date = ?", (user_id, when_date))
        row = cursor.fetchone()
        if row:
            if row[0] >= 1:     # ★ 하루 1회만 인정 (포럼+음성 중복 불가)
                return row[0]
            new_cnt = row[0] + 1
            cursor.execute("UPDATE exercise_log SET count = ? WHERE user_id = ? AND date = ?", (new_cnt, user_id, when_date))
        else:
            new_cnt = 1
            cursor.execute("INSERT INTO exercise_log (user_id, date, count) VALUES (?, ?, 1)", (user_id, when_date))
        conn.commit()
        return new_cnt

def increment_diet_log(user_id, when_date=None):
    if when_date is None:
        when_date = datetime.now().strftime("%Y-%m-%d")
    cursor.execute("SELECT count FROM diet_log WHERE user_id = ? AND date = ?", (user_id, when_date))
    row = cursor.fetchone()
    if row:
        new_cnt = row[0] + 1
        cursor.execute("UPDATE diet_log SET count = ? WHERE user_id = ? AND date = ?", (new_cnt, user_id, when_date))
    else:
        new_cnt = 1
        cursor.execute("INSERT INTO diet_log (user_id, date, count) VALUES (?, ?, 1)", (user_id, when_date))
    conn.commit()
    return new_cnt

def get_week_progress(user_id, week_start, week_end):
    result = {"exercise_goal": 0, "exercise_done": 0, "diet_goal": 0, "diet_done": 0, "weight_goal": False, "daily": {}}
    cursor.execute("SELECT freq_per_week FROM goals WHERE user_id = ? AND type = 'freq_exercise' AND active = 1", (user_id,))
    row = cursor.fetchone()
    if row:
        result["exercise_goal"] = row[0]
        cursor.execute("""
            SELECT COALESCE(SUM(count), 0) FROM exercise_log
            WHERE user_id = ? AND date BETWEEN ? AND ?
        """, (user_id, week_start, week_end))
        result["exercise_done"] = cursor.fetchone()[0]
    cursor.execute("SELECT freq_per_week FROM goals WHERE user_id = ? AND type = 'freq_diet' AND active = 1", (user_id,))
    row = cursor.fetchone()
    if row:
        result["diet_goal"] = row[0]
        cursor.execute("""
            SELECT COALESCE(SUM(count), 0) FROM diet_log
            WHERE user_id = ? AND date BETWEEN ? AND ?
        """, (user_id, week_start, week_end))
        result["diet_done"] = cursor.fetchone()[0]
    cursor.execute("SELECT target_weight, current_weight FROM goals WHERE user_id = ? AND type = 'weight' AND active = 1", (user_id,))
    row = cursor.fetchone()
    if row and row[0] is not None and row[1] is not None and row[1] <= row[0]:
        result["weight_goal"] = True
    day_iter = datetime.strptime(week_start, "%Y-%m-%d").date()
    end_dt = datetime.strptime(week_end, "%Y-%m-%d").date()
    while day_iter <= end_dt:
        d_str = day_iter.strftime("%Y-%m-%d")
        cursor.execute("SELECT count FROM exercise_log WHERE user_id = ? AND date = ?", (user_id, d_str))
        ex_count = cursor.fetchone()
        cursor.execute("SELECT count FROM diet_log WHERE user_id = ? AND date = ?", (user_id, d_str))
        dt_count = cursor.fetchone()
        result["daily"][d_str] = {"exercise": ex_count[0] if ex_count else 0, "diet": dt_count[0] if dt_count else 0}
        day_iter += timedelta(days=1)
    return result

def get_muscle_ranking_top5():
    cursor.execute("""
        SELECT nickname, (badge_weekly + badge_bikini + badge_monthly) AS total,
               badge_weekly, badge_bikini, badge_monthly
        FROM users
        ORDER BY total DESC
        LIMIT 5
    """)
    return cursor.fetchall()

def get_exercise_ranking_top5(week_start, week_end):
    cursor.execute("""
        SELECT u.nickname, COALESCE(SUM(el.count), 0) AS total_count
        FROM users u
        LEFT JOIN exercise_log el ON u.user_id = el.user_id AND el.date BETWEEN ? AND ?
        GROUP BY u.user_id
        ORDER BY total_count DESC
        LIMIT 5
    """, (week_start, week_end))
    return cursor.fetchall()

def get_diet_ranking_top5(week_start, week_end):
    cursor.execute("""
        SELECT u.nickname, COALESCE(SUM(dl.count), 0) AS total_count
        FROM users u
        LEFT JOIN diet_log dl ON u.user_id = dl.user_id AND dl.date BETWEEN ? AND ?
        GROUP BY u.user_id
        ORDER BY total_count DESC
        LIMIT 5
    """, (week_start, week_end))
    return cursor.fetchall()

def check_and_award_weekly_badges():
    today = date.today()
    if today.weekday() != 6: return
    week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    week_end = today.strftime("%Y-%m-%d")
    cursor.execute("SELECT user_id, type, freq_per_week FROM goals WHERE active = 1")
    goals = cursor.fetchall()
    user_weeks = {}
    for user_id, gtype, freq in goals:
        user_weeks.setdefault(user_id, {"exercise": False, "diet": False, "weight": False})
    for user_id in list(user_weeks.keys()):
        cursor.execute("SELECT freq_per_week FROM goals WHERE user_id = ? AND type = 'freq_exercise' AND active = 1", (user_id,))
        row = cursor.fetchone()
        if row:
            target_cnt = row[0]
            cursor.execute("SELECT COALESCE(SUM(count), 0) FROM exercise_log WHERE user_id = ? AND date BETWEEN ? AND ?", (user_id, week_start, week_end))
            total_ex = cursor.fetchone()[0]
            if total_ex >= target_cnt: user_weeks[user_id]["exercise"] = True
        cursor.execute("SELECT freq_per_week FROM goals WHERE user_id = ? AND type = 'freq_diet' AND active = 1", (user_id,))
        row = cursor.fetchone()
        if row:
            target_cnt = row[0]
            cursor.execute("SELECT COALESCE(SUM(count), 0) FROM diet_log WHERE user_id = ? AND date BETWEEN ? AND ?", (user_id, week_start, week_end))
            total_diet = cursor.fetchone()[0]
            if total_diet >= target_cnt: user_weeks[user_id]["diet"] = True
        cursor.execute("SELECT target_weight, current_weight FROM goals WHERE user_id = ? AND type = 'weight' AND active = 1", (user_id,))
        row = cursor.fetchone()
        if row and row[0] is not None and row[1] is not None and row[1] <= row[0]:
            user_weeks[user_id]["weight"] = True
    for user_id, status in user_weeks.items():
        achieved_ex = 1 if status["exercise"] else 0
        achieved_dt = 1 if status["diet"] else 0
        achieved_wt = 1 if status["weight"] else 0
        cursor.execute("""
            INSERT OR REPLACE INTO weekly_status (user_id, week_start, achieved_exercise, achieved_diet, weight_updated, achieved_weight)
            VALUES (?, ?, ?, ?, 1, ?)
        """, (user_id, week_start, achieved_ex, achieved_dt, achieved_wt))
        if achieved_ex and achieved_dt:
            cursor.execute("UPDATE users SET badge_weekly = badge_weekly + 1 WHERE user_id = ?", (user_id,))
        if achieved_wt:
            cursor.execute("UPDATE users SET badge_bikini = badge_bikini + 1 WHERE user_id = ?", (user_id,))
    conn.commit()

def check_and_award_monthly_trophy():
    today = date.today()
    if today.day != 1: return
    last_month = (today.replace(day=1) - timedelta(days=1))
    year_month = last_month.strftime("%Y-%m")
    first_day = last_month.replace(day=1)
    last_day = last_month.replace(day=last_month.day)
    week_starts = []
    day_iter = first_day
    while day_iter <= last_day:
        if day_iter.weekday() == 0: week_starts.append(day_iter.strftime("%Y-%m-%d"))
        day_iter += timedelta(days=1)
    cursor.execute("SELECT user_id FROM users")
    all_users = [row[0] for row in cursor.fetchall()]
    for user_id in all_users:
        all_weeks_ok = True
        for wstart in week_starts:
            cursor.execute("""
                SELECT achieved_exercise, achieved_diet FROM weekly_status
                WHERE user_id = ? AND week_start = ?
            """, (user_id, wstart))
            row = cursor.fetchone()
            if row is None or row[0] == 0 or row[1] == 0:
                all_weeks_ok = False
                break
        if all_weeks_ok:
            cursor.execute("SELECT 1 FROM monthly_trophy WHERE user_id = ? AND year_month = ?", (user_id, year_month))
            if cursor.fetchone() is None:
                cursor.execute("""
                    INSERT INTO monthly_trophy (user_id, year_month, won_trophy)
                    VALUES (?, ?, 1)
                """, (user_id, year_month))
                cursor.execute("UPDATE users SET badge_monthly = badge_monthly + 1 WHERE user_id = ?", (user_id,))
    conn.commit()

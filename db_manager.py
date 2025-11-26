import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from cryptography.fernet import Fernet
import os

# ... (KONFIGURASI & HELPER SAMA SEPERTI SEBELUMNYA) ...
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'monegment_final.db')

CIPHER: Optional[Fernet] = None
VIP_USERS: List[int] = []
ADMIN_IDS: List[int] = []

def set_db_config(encryption_key: bytes, vip_users: List[int], admin_ids: List[int] = None):
    global CIPHER, VIP_USERS, ADMIN_IDS
    if encryption_key: CIPHER = Fernet(encryption_key)
    VIP_USERS = vip_users or []
    ADMIN_IDS = admin_ids or []
    init_db()

def encrypt_data(data: Any) -> str:
    if CIPHER is None: return str(data)
    return CIPHER.encrypt(str(data).encode()).decode() if data is not None else ""

def decrypt_data(token: Any) -> str:
    if CIPHER is None: return str(token)
    try: return CIPHER.decrypt(token.encode()).decode() if token else "0"
    except: return "0"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, datetime TEXT, type TEXT, amount TEXT, category TEXT, wallet TEXT, description TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_premium INTEGER DEFAULT 0, expiry_date TEXT, last_order_id TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS feedbacks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, message TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS budgets (user_id INTEGER PRIMARY KEY, limit_amount INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_exports (user_id INTEGER, date TEXT, count INTEGER, PRIMARY KEY(user_id, date))''')
    c.execute('''CREATE TABLE IF NOT EXISTS goals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT, target_amount TEXT, current_amount TEXT, deadline TEXT, priority TEXT, created_at TEXT)''')
    try: c.execute("SELECT username FROM feedbacks LIMIT 1")
    except: c.execute("ALTER TABLE feedbacks ADD COLUMN username TEXT")
    conn.commit(); conn.close()

# ... (FUNGSI GET_DF, GET_BALANCES, DLL TETAP SAMA) ...
def get_df(user_id: int, limit: int = None) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    try:
        lim = f"LIMIT {limit}" if limit else ""
        df = pd.read_sql_query(f"SELECT * FROM transactions WHERE user_id={user_id} ORDER BY id DESC {lim}", conn)
        if not df.empty:
            for c in ['amount', 'category', 'wallet', 'description']: df[c] = df[c].apply(decrypt_data)
            df['amount'] = df['amount'].apply(lambda x: float(x) if x != '0' else 0)
            df['datetime'] = pd.to_datetime(df['datetime'])
    except: df = pd.DataFrame()
    finally: conn.close()
    return df

def get_balances(user_id: int) -> Dict[str, float]:
    df = get_df(user_id)
    if df.empty: return {"total": 0, "Cash": 0, "E-Wallet": 0, "Bank": 0}
    
    total_inc = df[df['type'] == 'IN']['amount'].sum()
    total_out = df[df['type'] == 'OUT']['amount'].sum()
    net_balance = total_inc - total_out

    df['wallet_norm'] = df['wallet'].str.lower().str.strip()
    
    balances = {"total": float(net_balance), "Cash": 0, "E-Wallet": 0, "Bank": 0}
    
    for w_type in ['cash', 'e-wallet', 'bank']:
        df_w = df[df['wallet_norm'] == w_type]
        if not df_w.empty:
            inc = df_w[df_w['type'] == 'IN']['amount'].sum()
            out = df_w[df_w['type'] == 'OUT']['amount'].sum()
            key = 'E-Wallet' if w_type == 'e-wallet' else w_type.title()
            balances[key] = float(inc - out)
            
    return balances

def get_wallet_balance_specific(user_id: int, wallet_name: str) -> float:
    bals = get_balances(user_id)
    w_map = {'cash': 'Cash', 'e-wallet': 'E-Wallet', 'bank': 'Bank'}
    key = w_map.get(wallet_name.lower().strip(), 'Cash')
    return bals.get(key, 0.0)

def get_best_wallet_for_goal(user_id: int) -> Tuple[str, float]:
    bals = get_balances(user_id)
    best_w = "Cash"
    max_bal = 0
    for w in ['Cash', 'E-Wallet', 'Bank']:
        if bals.get(w, 0) > max_bal:
            max_bal = bals.get(w, 0)
            best_w = w
    return best_w, max_bal

def save_transaction(user_id: int, data: Dict):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        amt = abs(int(data.get('amount', 0)))
        cat = data.get('category')
        wal = data.get('wallet', 'Cash')
        desc = data.get('description')
        typ = data.get('type')
        if not typ: typ = 'IN' if cat == 'Pemasukan' else 'OUT'

        c.execute("INSERT INTO transactions (user_id, datetime, type, amount, category, wallet, description) VALUES (?,?,?,?,?,?,?)",
                  (user_id, dt, typ, encrypt_data(amt), encrypt_data(cat), encrypt_data(wal), encrypt_data(desc)))
        conn.commit()
    except Exception as e: print(f"DB Save Error: {e}")
    finally: conn.close()

# === GOAL FUNCTIONS ===
def add_goal(user_id, title, target, deadline, priority):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    dt = datetime.now().strftime("%Y-%m-%d")
    try:
        c.execute("INSERT INTO goals (user_id, title, target_amount, current_amount, deadline, priority, created_at) VALUES (?,?,?,?,?,?,?)",
                  (user_id, title, encrypt_data(target), encrypt_data(0), deadline, priority, dt))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def update_goal(user_id, goal_id, title, target, deadline, priority):
    """Fitur Edit Tujuan (AC 2.2)"""
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute("UPDATE goals SET title=?, target_amount=?, deadline=?, priority=? WHERE id=? AND user_id=?",
                  (title, encrypt_data(target), deadline, priority, goal_id, user_id))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def get_goals(user_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; c = conn.cursor()
    c.execute("SELECT * FROM goals WHERE user_id=? ORDER BY deadline ASC", (user_id,))
    rows = c.fetchall(); goals = []
    for r in rows:
        try:
            goals.append({
                "id": r['id'],
                "title": r['title'],
                "target": float(decrypt_data(r['target_amount'])),
                "current": float(decrypt_data(r['current_amount'])),
                "deadline": r['deadline'],
                "priority": r['priority']
            })
        except: pass
    conn.close(); return goals

def delete_goal(user_id, goal_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("DELETE FROM goals WHERE id=? AND user_id=?", (goal_id, user_id))
    conn.commit(); conn.close()

def process_goal_deposit(user_id: int, goal_id: int, wallet_source: str, amount: int) -> Tuple[bool, str]:
    bal = get_wallet_balance_specific(user_id, wallet_source)
    if bal < amount: return False, f"Saldo {wallet_source} tidak cukup (Sisa: {bal:,.0f})."
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try:
        c.execute("SELECT title, current_amount FROM goals WHERE id=? AND user_id=?", (goal_id, user_id))
        res = c.fetchone()
        if not res: return False, "Tujuan tidak ditemukan."
        goal_title = res[0]; current_saved = float(decrypt_data(res[1])); new_saved = current_saved + amount
        dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); desc = f"Tabungan ke {goal_title}"
        c.execute("INSERT INTO transactions (user_id, datetime, type, amount, category, wallet, description) VALUES (?,?,?,?,?,?,?)",
                  (user_id, dt, 'OUT', encrypt_data(amount), encrypt_data('Tabungan'), encrypt_data(wallet_source), encrypt_data(desc)))
        c.execute("UPDATE goals SET current_amount=? WHERE id=? AND user_id=?", (encrypt_data(new_saved), goal_id, user_id))
        conn.commit(); return True, "Berhasil menabung!"
    except Exception as e: return False, str(e)
    finally: conn.close()

# ... (SISA FUNGSI LAIN TETAP SAMA: check_premium, set_premium, delete_user_data, dll) ...
def check_premium(user_id: int) -> Tuple[bool, Optional[datetime]]:
    if user_id in VIP_USERS or user_id in ADMIN_IDS: return True, datetime(2099, 12, 31)
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT is_premium, expiry_date FROM users WHERE user_id = ?", (user_id,))
    res = c.fetchone(); conn.close()
    if res and res[0] == 1:
        try:
            exp = datetime.strptime(res[1], "%Y-%m-%d %H:%M:%S")
            if datetime.now() < exp: return True, exp
            else: set_premium(user_id, False); return False, None
        except: return False, None
    return False, None

def set_premium(user_id: int, status: bool, days: int = 30, order_id: Optional[str] = None):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    if status:
        is_prem, old_exp = check_premium(user_id)
        start = old_exp if is_prem and old_exp and old_exp > datetime.now() else datetime.now()
        exp = (start + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT OR REPLACE INTO users (user_id, is_premium, expiry_date, last_order_id) VALUES (?, ?, ?, ?)", (user_id, 1, exp, order_id))
    else: c.execute("UPDATE users SET is_premium = 0 WHERE user_id = ?", (user_id,))
    conn.commit(); conn.close()

def delete_user_data(user_id: int):
    conn = sqlite3.connect(DB_PATH); 
    conn.execute("DELETE FROM transactions WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM goals WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

def reset_user_data(user_id: int):
    try: delete_user_data(user_id); return True
    except: return False

def set_user_budget(user_id: int, amount: int):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO budgets (user_id, limit_amount) VALUES (?, ?)", (user_id, amount))
    conn.commit(); conn.close()

def get_user_budget(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT limit_amount FROM budgets WHERE user_id=?", (user_id,)); res = c.fetchone(); conn.close()
    return res[0] if res else 0

def update_transaction_db(user_id, trans_id, amount, category, wallet, description):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try: c.execute("UPDATE transactions SET amount=?, category=?, wallet=?, description=? WHERE id=? AND user_id=?", (encrypt_data(amount), encrypt_data(category), encrypt_data(wallet), encrypt_data(description), trans_id, user_id)); conn.commit(); return True
    except: return False
    finally: conn.close()

def delete_transaction_db(user_id, trans_id):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    try: c.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (trans_id, user_id)); conn.commit(); return True
    except: return False
    finally: conn.close()

def process_transfer(user_id: int, src: str, tgt: str, amount: int):
    bal = get_wallet_balance_specific(user_id, src)
    if bal < amount: return False, "TRANSAKSI GAGAL: Saldo tidak mencukupi. Saldo tidak dapat menjadi negatif."
    conn = sqlite3.connect(DB_PATH); c = conn.cursor(); dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        c.execute("INSERT INTO transactions (user_id, datetime, type, amount, category, wallet, description) VALUES (?,?,?,?,?,?,?)", (user_id, dt, 'OUT', encrypt_data(amount), encrypt_data('Transfer'), encrypt_data(src), encrypt_data(f"Transfer ke {tgt}")))
        c.execute("INSERT INTO transactions (user_id, datetime, type, amount, category, wallet, description) VALUES (?,?,?,?,?,?,?)", (user_id, dt, 'IN', encrypt_data(amount), encrypt_data('Transfer'), encrypt_data(tgt), encrypt_data(f"Terima dari {src}")))
        conn.commit(); return True, "Sukses"
    except Exception as e: return False, str(e)
    finally: conn.close()

def save_feedback(uid, uname, msg):
    conn = sqlite3.connect(DB_PATH); conn.execute("INSERT INTO feedbacks (user_id, username, message, created_at) VALUES (?,?,?,?)", (uid, uname, msg, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))); conn.commit(); conn.close()

def get_df_feedback():
    conn = sqlite3.connect(DB_PATH); df = pd.read_sql_query("SELECT * FROM feedbacks ORDER BY created_at DESC", conn); conn.close(); return df

def check_export_limit(user_id: int, max_limit: int = 3) -> bool:
    conn = sqlite3.connect(DB_PATH); c = conn.cursor(); today = datetime.now().strftime("%Y-%m-%d")
    c.execute("SELECT count FROM daily_exports WHERE user_id=? AND date=?", (user_id, today)); res = c.fetchone()
    if (res and res[0] >= max_limit): conn.close(); return False
    c.execute("INSERT OR REPLACE INTO daily_exports (user_id, date, count) VALUES (?, ?, ?)", (user_id, today, (res[0] if res else 0) + 1)); conn.commit(); conn.close(); return True
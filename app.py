from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
import io
import os
from dotenv import load_dotenv
import google.generativeai as genai
from cryptography.fernet import Fernet
import hmac
import hashlib
import urllib.parse
import json
from datetime import datetime
import pandas as pd
import re
import traceback

# Import DB Manager
from db_manager import (
    set_db_config, save_transaction, get_df, get_balances,
    check_premium, encrypt_data, set_user_budget, get_user_budget, 
    update_transaction_db, delete_transaction_db, check_export_limit,
    save_feedback, get_df_feedback, reset_user_data, process_transfer,
    get_wallet_balance_specific, add_goal, get_goals, delete_goal,
    process_goal_deposit, get_best_wallet_for_goal, update_goal
)

# ==========================================
# KONFIGURASI FLASK & PATH
# ==========================================
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'Front_end')
STATIC_DIR = os.path.join(TEMPLATE_DIR, 'static') 

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

app.secret_key = os.getenv("SECRET_KEY", "super_secret_key_default")
app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='None')

GEMINI_API_KEY = os.getenv("GEMINI_KEY")
TELEGRAM_TOKEN = os.getenv("BOT_TOKEN") 
ENCRYPTION_KEY = os.getenv("SECRET_KEY")
if isinstance(ENCRYPTION_KEY, str):
    ENCRYPTION_KEY = ENCRYPTION_KEY.encode()

VIP_USERS = [int(x) for x in os.getenv("VIP_USERS", "").replace("'","").split(',') if x.strip().isdigit()]
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").replace("'","").split(',') if x.strip().isdigit()]

genai.configure(api_key=GEMINI_API_KEY)
set_db_config(ENCRYPTION_KEY, VIP_USERS, ADMIN_IDS)

# --- HELPER FUNCTIONS ---
def normalize_wallet(w_raw):
    w = str(w_raw).lower().strip()
    if any(x in w for x in ['cash', 'tunai', 'uang']): return 'Cash'
    elif any(x in w for x in ['bank', 'bca', 'mandiri', 'bri', 'bni', 'atm', 'debit']): return 'Bank'
    else: return 'E-Wallet'

def clean_number(value):
    """Membersihkan string input menjadi integer murni"""
    if not value: return 0
    # Hapus semua karakter yang bukan angka
    clean_str = re.sub(r'[^\d]', '', str(value))
    try:
        return int(clean_str)
    except:
        return 0

def parse_gemini_json(response_text, original_text):
    # 1. Bersihkan Markdown Code Blocks
    clean = response_text.replace('```json', '').replace('```', '').strip()
    
    data = []
    try:
        # 2. Coba parsing langsung
        data = json.loads(clean)
    except:
        try:
            # 3. Jika gagal, cari pola List [...] atau Object {...} dengan Regex
            match_list = re.search(r'\[.*\]', clean, re.DOTALL)
            match_obj = re.search(r'\{.*\}', clean, re.DOTALL)
            
            if match_list:
                data = json.loads(match_list.group(0))
            elif match_obj:
                data = [json.loads(match_obj.group(0))]
            else:
                print(f"DEBUG: Gagal regex JSON. Raw text: {clean}")
                return []
        except Exception as e:
            print(f"DEBUG: JSON Error: {e}")
            return []

    if isinstance(data, dict): data = [data]
    if not isinstance(data, list): return []

    final_data = []
    valid_cats = ['Makanan', 'Transport', 'Tagihan', 'Belanja', 'Kesehatan', 'Hiburan', 'Pemasukan', 'Lainnya']
    
    for item in data:
        # Ambil nominal dengan pembersihan
        raw_amt = item.get('amount', 0)
        amount = clean_number(raw_amt)
        
        if amount == 0: continue
        
        cat = str(item.get('category', 'Lainnya')).title()
        
        # Logika Category & Type
        if cat == 'Pemasukan':
            item['type'] = 'IN'
            item['category'] = 'Pemasukan'
        else:
            item['type'] = 'OUT'
            item['category'] = cat if cat in valid_cats else 'Lainnya'

        item['wallet'] = normalize_wallet(item.get('wallet', 'Cash'))
        item['description'] = item.get('description', original_text)
        item['amount'] = amount
        final_data.append(item)
    
    return final_data

def ask_gemini_web(text=None, file_path=None, mode="text", mime_type=None):
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    # PROMPT DIPERBAIKI: Lebih eksplisit meminta JSON murni tanpa basa-basi
    base_prompt = """
    Extract financial transaction details into a JSON Array.
    
    Rules:
    1. "category" MUST be one of: [Makanan, Transport, Tagihan, Belanja, Kesehatan, Hiburan, Pemasukan, Lainnya].
    2. "amount" MUST be an integer number (no Rp, no dots, no commas).
    3. "wallet" hints: gopay/ovo/dana/shopee -> "E-Wallet", tunai/cash -> "Cash", atm/transfer/bca/mandiri -> "Bank". Default: "Cash".
    4. If the text implies income (gajian, dikasih, terima), set category to "Pemasukan".
    
    Output Format Example:
    [{"amount": 50000, "category": "Makanan", "wallet": "Cash", "description": "Nasi Goreng"}]
    
    RETURN ONLY JSON. NO MARKDOWN. NO EXPLANATION.
    """
    
    try:
        if mode in ["image", "voice"] and file_path:
            if not os.path.exists(file_path): return []
            prompt_suf = "\nAnalyze this receipt/audio."
            res = model.generate_content([base_prompt + prompt_suf, genai.upload_file(path=file_path, mime_type=mime_type)])
            orig = "Scan Bon" if mode == "image" else "Voice Note"
        else:
            res = model.generate_content(f"{base_prompt}\nInput Text: \"{text}\"")
            orig = text
            
        return parse_gemini_json(res.text, orig)
    except Exception as e:
        print(f"AI Connection Error: {e}")
        return []

# --- ROUTES ---
def validate_telegram_auth(init_data):
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        if 'hash' not in parsed_data: return None
        received_hash = parsed_data.pop('hash')
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", TELEGRAM_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash == received_hash: return json.loads(parsed_data['user'])['id']
        return None
    except: return None

@app.route('/')
def home(): return render_template('dashboard.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    user_id = validate_telegram_auth(data.get('initData'))
    if user_id:
        session['user_id'] = int(user_id); session.permanent = True
        return jsonify({"status": "success", "user_id": user_id})
    return jsonify({"status": "error", "message": "Invalid signature"}), 403

@app.route('/api/get_data')
def api_get_data():
    if 'user_id' not in session: return jsonify({"status": "error", "message": "Sesi Habis."}), 401
    uid = session['user_id']
    try:
        is_prem, expiry = check_premium(uid)
        is_admin = uid in ADMIN_IDS
        is_vip = uid in VIP_USERS
        
        # Load balances breakdown for Modal AC 3.3
        balances = get_balances(uid)
        df = get_df(uid)
        budget_limit = get_user_budget(uid) 
        
        inc, out = 0, 0
        recents, chart_labels, chart_values = [], [], []
        monthly_labels, monthly_inc, monthly_exp = [], [], []

        if not df.empty:
            df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
            inc = df[df['type']=='IN']['amount'].sum()
            out = df[df['type']=='OUT']['amount'].sum()
            
            df_out = df[df['type'] == 'OUT']
            if not df_out.empty:
                df_chart = df_out.groupby('category')['amount'].sum().reset_index()
                chart_labels, chart_values = df_chart['category'].tolist(), [float(x) for x in df_chart['amount'].tolist()]
            
            df['month_year'] = df['datetime'].dt.to_period('M').astype(str) 
            monthly_groups = df.groupby('month_year')
            for p in sorted(list(monthly_groups.groups.keys()))[-6:]:
                monthly_labels.append(p)
                g = monthly_groups.get_group(p)
                monthly_inc.append(float(g[g['type']=='IN']['amount'].sum()))
                monthly_exp.append(float(g[g['type']=='OUT']['amount'].sum()))

            recents = df.head(50).to_dict(orient='records')

        formatted_recents = []
        for r in recents:
            row = r.copy()
            if isinstance(row['datetime'], (datetime, pd.Timestamp)): row['datetime'] = row['datetime'].strftime('%d %b %H:%M')
            if 'month_year' in row: del row['month_year']
            formatted_recents.append(row)
                
        return jsonify({
            "status": "success",
            "user_id": uid, "is_prem": is_prem, "is_vip": is_vip, "is_admin": is_admin,
            "expiry_date": expiry.strftime('%d %b %Y') if expiry else "",
            "balance": f"{balances['total']:,.0f}", 
            "income": f"{inc:,.0f}", 
            "expense": f"{out:,.0f}",
            # Breakdown for Modal
            "cash_balance": f"{balances['Cash']:,.0f}", 
            "ewallet_balance": f"{balances['E-Wallet']:,.0f}",
            "bank_balance": f"{balances['Bank']:,.0f}", # Added for completeness
            "recents": formatted_recents, "chart_labels": chart_labels, "chart_values": chart_values,
            "budget_limit": budget_limit, "monthly_labels": monthly_labels, "monthly_inc": monthly_inc, "monthly_exp": monthly_exp
        })
    except Exception as e: return jsonify({"status": "error", "message": str(e)})

# === FITUR EDIT GOAL (AC 2) ===
@app.route('/api/edit_goal', methods=['POST'])
def api_edit_goal():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    try:
        data = request.json
        # Validasi sederhana
        if not data.get('title') or not data.get('target'): return jsonify({"status": "error", "message": "Data tidak lengkap"})
        
        success = update_goal(
            session['user_id'], 
            data['id'], 
            data['title'], 
            clean_number(data['target']), 
            data['deadline'], 
            data['priority']
        )
        if success: return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "Gagal update database"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)})

# ... (SISA ROUTE LAINNYA SAMA SEPERTI SEBELUMNYA) ...
@app.route('/add_transaction', methods=['POST'])
def add_transaction():
    if 'user_id' not in session: return jsonify({"status": "error", "message": "Sesi habis"})
    uid = session['user_id']
    is_prem, _ = check_premium(uid)
    
    mode = request.form.get('mode', 'text')
    
    # MANUAL
    if mode == 'manual':
        try:
            amount = clean_number(request.form.get('amount'))
            if amount <= 0: return jsonify({"status": "error", "message": "Nominal tidak valid"})

            data = {
                'amount': amount,
                'category': request.form.get('category'),
                'wallet': request.form.get('wallet'),
                'description': request.form.get('description'),
                'type': request.form.get('type')
            }
            if data['type'] == 'OUT':
                cur = get_wallet_balance_specific(uid, data['wallet'])
                if cur < data['amount']: return jsonify({"status": "error", "message": f"Saldo {data['wallet']} Kurang (Sisa: {cur:,.0f})"})
            
            save_transaction(uid, data)
            return jsonify({"status": "success", "count": 1})
        except Exception as e: return jsonify({"status": "error", "message": f"Input Manual Error: {str(e)}"})

    # AI
    file = request.files.get('media_file')
    text_input = request.form.get('text_input')
    
    if mode in ['image', 'voice'] and not is_prem: return jsonify({"status": "error", "message": "Fitur Pro Terkunci 🔒"})

    temp_filename = None
    try:
        data_list = []
        if file and mode in ['image', 'voice']:
            ext = os.path.splitext(file.filename)[1].lower()
            if 'audio' in file.mimetype or mode == 'voice': ext = '.ogg' 
            elif 'image' in file.mimetype: ext = '.jpg'
            temp_filename = os.path.join(BASE_DIR, f"temp_{uid}_{int(datetime.now().timestamp())}{ext}")
            file.save(temp_filename)
            data_list = ask_gemini_web(file_path=temp_filename, mode=mode, mime_type=file.mimetype)
        elif text_input:
            data_list = ask_gemini_web(text=text_input, mode='text')

        if not data_list: return jsonify({"status": "error", "message": "AI tidak menemukan data transaksi valid."})

        count = 0
        error_msg = None
        for item in data_list:
            if item['type'] == 'OUT':
                cur = get_wallet_balance_specific(uid, item['wallet'])
                if cur < item['amount']: 
                    error_msg = f"Saldo {item['wallet']} tidak cukup untuk '{item['description']}'"
                    continue 
            save_transaction(uid, item)
            count += 1
            
        if count == 0 and error_msg: return jsonify({"status": "error", "message": error_msg})
        elif count == 0: return jsonify({"status": "error", "message": "Gagal menyimpan data."})
        return jsonify({"status": "success", "count": count})
    except Exception as e: return jsonify({"status": "error", "message": f"Server Error: {str(e)}"})
    finally: 
        if temp_filename and os.path.exists(temp_filename): os.remove(temp_filename)

@app.route('/api/goals', methods=['GET', 'POST', 'DELETE'])
def api_goals():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    uid = session['user_id']
    if request.method == 'GET': return jsonify({"status": "success", "data": get_goals(uid)})
    if request.method == 'POST':
        try:
            data = request.json
            add_goal(uid, data['title'], data['target'], data['deadline'], data['priority'])
            return jsonify({"status": "success"})
        except: return jsonify({"status": "error"})
    if request.method == 'DELETE':
        delete_goal(uid, request.args.get('id'))
        return jsonify({"status": "success"})

@app.route('/api/optimize_goals', methods=['GET'])
def api_optimize_goals():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    uid = session['user_id']
    df = get_df(uid)
    now_s = datetime.now().strftime("%Y-%m")
    df_m = df[df['datetime'].dt.strftime('%Y-%m')==now_s] if not df.empty else pd.DataFrame()
    inc = df_m[df_m['type']=='IN']['amount'].sum() if not df_m.empty else 0
    exp = df_m[df_m['type']=='OUT']['amount'].sum() if not df_m.empty else 0
    top_cat = "Lainnya"
    if not df_m.empty:
        cat_group = df_m[df_m['type']=='OUT'].groupby('category')['amount'].sum().sort_values(ascending=False)
        if not cat_group.empty: top_cat = cat_group.index[0]
    free_to_spend = inc - exp
    goals = get_goals(uid)
    advice = []
    recommended_action = None 
    
    if not goals:
        advice.append("⚠️ Anda belum memiliki tujuan keuangan.")
        advice.append("💡 Buat tujuan baru (ex: Laptop) di tab Rencana.")
    elif free_to_spend <= 0:
        advice.append(f"⚠️ Arus kas bulan ini <b>negatif</b> atau pas-pasan.")
        advice.append(f"📉 Pengeluaran terbesar: <b>{top_cat}</b>.")
        advice.append("💡 Fokus kurangi pengeluaran sebelum menabung.")
    else:
        target_goal = None
        for g in goals:
            if g['priority'] == 'P1' and g['current'] < g['target']:
                target_goal = g; break
        if not target_goal:
            goals_sorted = sorted(goals, key=lambda x: x['deadline'])
            for g in goals_sorted:
                if g['current'] < g['target']: target_goal = g; break
        
        if target_goal:
            sisa_butuh = target_goal['target'] - target_goal['current']
            save_amt = min(free_to_spend * 0.8, sisa_butuh)
            save_amt = int(save_amt // 1000 * 1000) 
            
            if save_amt > 10000: 
                advice.append(f"✅ Arus kas Anda positif! (Sisa: Rp {free_to_spend:,.0f})")
                advice.append(f"🚀 Percepat tujuan <b>{target_goal['title']}</b>.")
                best_wallet, _ = get_best_wallet_for_goal(uid)
                recommended_action = { "amount": save_amt, "goal_id": target_goal['id'], "goal_title": target_goal['title'], "wallet": best_wallet }
            else: advice.append("✅ Kondisi keuangan aman, tapi sisa dana tipis.")
        else: advice.append("🎉 Hebat! Semua tujuan keuangan Anda sudah tercapai.")

    return jsonify({"status": "success", "advice": advice, "action": recommended_action})

@app.route('/api/goal_deposit', methods=['POST'])
def api_goal_deposit():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    goal_id = request.form.get('goal_id')
    wallet_source = request.form.get('wallet_source')
    amount = clean_number(request.form.get('amount'))
    if amount <= 0: return jsonify({"status": "error", "message": "Nominal harus > 0"})
    success, msg = process_goal_deposit(session['user_id'], goal_id, wallet_source, amount)
    if success: return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": msg})

@app.route('/api/download_excel')
def api_download_excel():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    uid = session['user_id']; is_prem, _ = check_premium(uid)
    if not is_prem and not check_export_limit(uid, max_limit=3): return jsonify({"status": "error", "message": "LIMIT_REACHED"}), 403
    try:
        df = get_df(uid)
        if df.empty: return jsonify({"status": "error", "message": "Data kosong"}), 400
        df = df.drop(columns=['id', 'user_id', 'month_year'], errors='ignore')
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/octet-stream', as_attachment=True, download_name=f"Laporan_{datetime.now().strftime('%d%m%Y')}.xlsx")
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/transfer_balance', methods=['POST'])
def api_transfer_balance():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    src, tgt, amt = request.form.get('source'), request.form.get('target'), clean_number(request.form.get('amount'))
    if src == tgt: return jsonify({"status": "error", "message": "Dompet sama"})
    success, msg = process_transfer(session['user_id'], src, tgt, amt)
    if success: return jsonify({"status": "success"})
    return jsonify({"status": "error", "message": msg})

@app.route('/api/send_feedback', methods=['POST'])
def api_send_feedback():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    uid = session['user_id']
    if uid in VIP_USERS or uid in ADMIN_IDS: return jsonify({"status": "error", "message": "Access Denied"}), 403
    save_feedback(uid, f"User {uid}", request.form.get('message', ''))
    return jsonify({"status": "success"})

@app.route('/api/download_feedback')
def api_download_feedback():
    if session.get('user_id') not in ADMIN_IDS: return jsonify({"status": "error", "message": "Admin Only"}), 403
    try:
        df = get_df_feedback(); output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False)
        output.seek(0)
        return send_file(output, mimetype='application/octet-stream', as_attachment=True, download_name="Feedbacks.xlsx")
    except: return jsonify({"status": "error"})

@app.route('/api/set_budget', methods=['POST'])
def api_set_budget():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    try: set_user_budget(session['user_id'], clean_number(request.form.get('amount', 0))); return jsonify({"status": "success"})
    except: return jsonify({"status": "error"})

@app.route('/api/edit_transaction', methods=['POST'])
def api_edit_transaction():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    if update_transaction_db(session['user_id'], request.form.get('id'), clean_number(request.form.get('amount')), request.form.get('category'), request.form.get('wallet'), request.form.get('description')): return jsonify({"status": "success"})
    return jsonify({"status": "error"})

@app.route('/api/delete_transaction', methods=['POST'])
def api_delete_transaction():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    if delete_transaction_db(session['user_id'], request.form.get('id')): return jsonify({"status": "success"})
    return jsonify({"status": "error"})

@app.route('/api/reset_data', methods=['POST'])
def api_reset_data():
    if 'user_id' not in session: return jsonify({"status": "error"}), 401
    if reset_user_data(session['user_id']): return jsonify({"status": "success"})
    return jsonify({"status": "error"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
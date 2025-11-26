import telebot
from telebot import types
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import os
from dotenv import load_dotenv
import json
import google.generativeai as genai
from datetime import datetime, timedelta
import re
import time
import sys
from typing import Dict, Any, List, Optional, Tuple
import random

# IMPORT DB MANAGER UNTUK INTEGRASI
import db_manager as db

# ==========================================
# 1. KONFIGURASI
# ==========================================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_KEY")
ENCRYPTION_KEY = os.getenv("SECRET_KEY")
# Load URL dari .env
BASE_URL = os.getenv("WEB_APP_URL", "http://127.0.0.1:5000")

# Validasi Key
if not all([TELEGRAM_TOKEN, GEMINI_API_KEY, ENCRYPTION_KEY]):
    print("❌ ERROR: Key di .env ada yang kurang.")
    sys.exit(1)

# Konversi Key ke Bytes
if isinstance(ENCRYPTION_KEY, str):
    ENCRYPTION_KEY = ENCRYPTION_KEY.encode()

# Inisialisasi
bot = telebot.TeleBot(TELEGRAM_TOKEN)
genai.configure(api_key=GEMINI_API_KEY)

# --- PARSING ID DARI .ENV ---
def parse_id_list(env_key: str) -> List[int]:
    raw = os.getenv(env_key, "")
    raw = raw.replace("'", "").replace('"', "")
    if not raw: return []
    try: return [int(x.strip()) for x in raw.split(',') if x.strip().isdigit()]
    except: return []

VIP_USERS = parse_id_list("VIP_USERS")
ADMIN_IDS = parse_id_list("ADMIN_IDS")

# INIT DATABASE MELALUI DB MANAGER
db.set_db_config(ENCRYPTION_KEY, VIP_USERS)

# --- PREMIUM CONFIG ---
PREMIUM_PACKAGES = {
    "pay_week": (7, "Bronze (1 Minggu)", "prem_7_day", 50),
    "pay_month": (30, "Silver (1 Bulan)", "prem_30_day", 150),
    "pay_gold": (90, "Gold (3 Bulan)", "prem_90_day", 400)
}
PAYLOAD_TO_DAYS = {v[2]: v[0] for v in PREMIUM_PACKAGES.values()}

user_pending_data: Dict[int, List[Dict[str, Any]]] = {}

# --- IKLAN CONFIG ---
AD_COOLDOWN = 21600
ad_tracker: Dict[int, float] = {}

print(f"✅ Bot Berjalan: Menggunakan Gemini 2.0 Flash")

# ==========================================
# 2. HELPER (PREMIUM & ADS)
# ==========================================

def send_upsell_message(chat_id: int, feature_name: str):
    msg = (f"🔒 *Fitur {feature_name} Terkunci*\n\nFitur ini khusus *PREMIUM*.\n"
           "📸 Scan Bon (AI Vision)\n🎙️ Voice Note (Rekam Suara)\n✅ Multi-Input (Banyak Transaksi)\n"
           "Mulai dari: *50 Stars / minggu*")
    mk = types.InlineKeyboardMarkup(); mk.add(types.InlineKeyboardButton("💎 Upgrade Premium", callback_data="upgrade_now"))
    bot.send_message(chat_id, msg, parse_mode='Markdown', reply_markup=mk)

def check_and_send_ad(user_id: int, chat_id: int, is_premium: bool):
    if is_premium: return

    last_ad = ad_tracker.get(user_id, 0)
    now = time.time()

    if now - last_ad > AD_COOLDOWN:
        ads_list = [
            "💡 *Tahukah Kamu?*\nKamu bisa mencatat pengeluaran cuma pakai Suara lho! Gak perlu ngetik lagi.\nUpgrade ke *Premium* sekarang.",
            "📊 *Butuh Laporan Rapi?*\nDapatkan akses Export ke Excel (.xlsx) dengan fitur *Premium*. Rapikan keuanganmu hari ini!",
            "🚀 *Hemat Waktu!*\nFitur *Scan Bon* di Premium bisa membaca struk belanjaanmu otomatis. Cobain deh!",
            "🛑 *Capek Ngetik Satu-satu?*\nUser *Premium* bisa kirim daftar belanja sekaligus dalam satu chat. Upgrade yuk!"
        ]
        selected_ad = random.choice(ads_list)
        mk = types.InlineKeyboardMarkup()
        mk.add(types.InlineKeyboardButton("💎 Lihat Paket Premium", callback_data="upgrade_now"))
        try:
            bot.send_message(chat_id, f"----- 📢 Iklan -----\n\n{selected_ad}", parse_mode='Markdown', reply_markup=mk)
            ad_tracker[user_id] = now
        except: pass

# ==========================================
# 3. AI LOGIC (GEMINI 2.0 FLASH)
# ==========================================

def parse_gemini_response(response_text: str, original_text: str) -> List[Dict[str, Any]]:
    clean = response_text.replace('```json', '').replace('```', '').strip()
    data = []
    try:
        data = json.loads(clean)
    except:
        try:
            match = re.search(r'(\[.*\])', clean, re.DOTALL) 
            if match:
                data = json.loads(match.group(0))
            else:
                match = re.search(r'(\{.*\})', clean, re.DOTALL)
                if match:
                    data = [json.loads(match.group(0))]
        except: pass

    if isinstance(data, dict): data = [data]
    if not isinstance(data, list): data = []

    final = []
    VALID = {'Makanan', 'Transport', 'Tagihan', 'Belanja', 'Kesehatan', 'Hiburan', 'Pemasukan', 'Lainnya'}

    for item in data:
        amt = item.get('amount', 0)
        if amt == 0: continue

        cat = str(item.get('category', 'Lainnya')).title()
        item['category'] = cat if cat in VALID else 'Lainnya'
        item['type'] = 'IN' if item['category'] == 'Pemasukan' else 'OUT'
        item['wallet'] = item.get('wallet', 'Cash')
        item['description'] = item.get('description', original_text)
        item['amount'] = int(amt)
        final.append(item)
    return final

def ask_gemini(text: str = None, file_path: str = None, mode: str = "text"):
    model_name = 'gemini-2.0-flash'

    base = """
    Extract ALL financial transactions.
    Category MUST be one of: Makanan, Transport, Tagihan, Belanja, Kesehatan, Hiburan, Pemasukan, Lainnya.
    Amount MUST be integer (e.g. 50000). Wallet default is "Cash".
    Output JSON List ONLY: [{"amount": int, "category": str, "wallet": str, "description": str}]
    """

    try:
        model = genai.GenerativeModel(model_name)

        if mode == "image":
            res = model.generate_content([base + "\nAnalyze receipt image.", genai.upload_file(path=file_path)])
            orig = "Scan Bon"
        elif mode == "voice":
            res = model.generate_content([base + "\nListen to audio.", genai.upload_file(path=file_path)])
            orig = "Voice Note"
        else:
            res = model.generate_content(f"{base}\nText: \"{text}\"")
            orig = text

        return parse_gemini_response(res.text, orig)

    except Exception as e:
        print(f"AI Error ({mode}): {e}")
        return []

# ==========================================
# 4. HANDLERS & UX
# ==========================================

def notify_admins(msg: str):
    for aid in ADMIN_IDS:
        try: bot.send_message(aid, msg, parse_mode='Markdown')
        except: pass

def show_main_menu(chat_id: int, text: str = "🏠 *Menu Utama*"):
    mk = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    mk.add("📊 Summary", "💰 Cek Saldo")
    mk.add("💡 Saran Cepat", "📂 Export Excel")

    if chat_id in ADMIN_IDS:
        mk.add("💎 Akun Saya", "📢 Broadcast")
        mk.add("📑 Baca Feedback", "🗑️ Reset Feedback")
        mk.add("📂 Export Feedback")
    else:
        mk.add("💎 Akun Saya", "💬 Feedback")

    bot.send_message(chat_id, text, reply_markup=mk, parse_mode='Markdown')

# --- START & MENU ---
@bot.message_handler(commands=['start'])
def send_welcome_start(message: types.Message):
    # Init DB saat start untuk memastikan user siap
    
    # Inline Keyboard dengan tombol Web App
    mk = types.InlineKeyboardMarkup(row_width=1)
    mk.add(
        types.InlineKeyboardButton("🏠 Buka Menu Utama", callback_data="open_main_menu"),
        types.InlineKeyboardButton("📱 Buka Mini App", web_app=types.WebAppInfo(url=BASE_URL)),
        types.InlineKeyboardButton("❓ Bantuan Cara Pakai", callback_data="open_help"),
        types.InlineKeyboardButton("💎 Upgrade Premium", callback_data="upgrade_now"),
        )

    bot.send_message(
        message.chat.id,
        "🤖 *Selamat Datang di Monegment Bot*\n\nAsisten Keuangan AI yang Cerdas & Praktis.\nSilakan pilih opsi di bawah ini:",
        parse_mode='Markdown',
        reply_markup=mk
    )

@bot.message_handler(commands=['menu'])
def command_menu(message: types.Message):
    show_main_menu(message.chat.id)

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=['addprem'])
def admin_add(m: types.Message):
    if m.from_user.id not in ADMIN_IDS: return
    try:
        parts = m.text.split(); uid = int(parts[1]); days = int(parts[2]) if len(parts)>2 else 30000
        # Panggil DB Manager
        db.set_premium(uid, True, days, "ADMIN")
        bot.reply_to(m, f"✅ User {uid} Premium {days} hari.")
    except: bot.reply_to(m, "Format: /addprem <user_id> [hari]")

@bot.message_handler(commands=['panduan'])
def help_cmd(m):
    bot.send_message(m.chat.id, "📝 *Cara Pakai:*\n_'Makan siang 25rb'_\n\n💎 *Premium:*\n📸 Scan Bon\n🎙️ Voice Note\n📂 Excel Report", parse_mode='Markdown')

# --- PAYMENT & UPGRADE ---
@bot.message_handler(commands=['upgrade'])
def handle_upgrade(m: types.Message):
    # Cek Premium via DB Manager
    is_prem, exp = db.check_premium(m.from_user.id)
    if is_prem:
        d_txt = "LIFETIME" if exp.year==2099 else exp.strftime('%d-%m-%Y')
        bot.reply_to(m, f"🌟 *PREMIUM AKTIF*\nExp: {d_txt}", parse_mode='Markdown'); return

    msg = "💎 *UPGRADE PREMIUM*\n\n📸 Scan Bon & 🎙️ Voice Note\n✅ Multi-Input\n👇 *Pilih Paket:*"
    mk = types.InlineKeyboardMarkup(row_width=1)
    for k, (d, t, _, p) in PREMIUM_PACKAGES.items(): mk.add(types.InlineKeyboardButton(f"{t} - {p} ⭐️", callback_data=k))
    bot.send_message(m.chat.id, msg, parse_mode='Markdown', reply_markup=mk)

@bot.callback_query_handler(func=lambda c: c.data in PREMIUM_PACKAGES or c.data == "upgrade_now")
def invoice(c: types.CallbackQuery):
    if c.data == "upgrade_now":
        bot.answer_callback_query(c.id, text="Membuka menu upgrade...")
        handle_upgrade(c.message)
        return

    d, t, pay, p = PREMIUM_PACKAGES[c.data]
    bot.send_invoice(c.message.chat.id, t, f"Premium {d} Hari", pay, "", "XTR", [types.LabeledPrice(t, p)], "upg")

@bot.pre_checkout_query_handler(func=lambda q: True)
def pre(q: types.PreCheckoutQuery):
    bot.answer_pre_checkout_query(q.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def paid(m: types.Message):
    uid = m.from_user.id; days = PAYLOAD_TO_DAYS.get(m.successful_payment.invoice_payload, 30)
    db.set_premium(uid, True, days, m.successful_payment.telegram_payment_charge_id)
    bot.send_message(m.chat.id, f"🎉 *Sukses!*\nPremium aktif {days} hari.", parse_mode='Markdown')

# --- FEATURES (VOICE & PHOTO) ---
@bot.message_handler(content_types=['photo', 'voice'])
def handle_media(m: types.Message):
    uid = m.from_user.id
    is_prem, _ = db.check_premium(uid)
    ft_name = "Scan Bon" if m.content_type == 'photo' else "Voice Note"

    if not is_prem: send_upsell_message(m.chat.id, ft_name); return

    bot.send_chat_action(m.chat.id, 'typing')
    try:
        fid = m.photo[-1].file_id if m.content_type == 'photo' else m.voice.file_id
        f = bot.download_file(bot.get_file(fid).file_path)
        fname = f"temp_{uid}.{'jpg' if m.content_type == 'photo' else 'ogg'}"
        with open(fname, 'wb') as file: file.write(f)

        bot.reply_to(m, f"⏳ AI memproses {ft_name}...", parse_mode='Markdown')
        data = ask_gemini(file_path=fname, mode="image" if m.content_type=='photo' else "voice")
        if os.path.exists(fname): os.remove(fname)
        confirm_data(m, data)
    except Exception as e: bot.reply_to(m, f"❌ Error: {e}")

# --- BROADCAST & FEEDBACK ---
def run_broadcast(message, text_to_send):
    users = db.get_all_user_ids()
    status_msg = bot.reply_to(message, f"⏳ Mengirim ke {len(users)} user (Admin dikecualikan)...")
    ok, fail = 0, 0
    for u in users:
        if u in ADMIN_IDS: continue
        try:
            bot.send_message(u, f"📢 *PENGUMUMAN*\n\n{text_to_send}", parse_mode='Markdown')
            ok+=1
            time.sleep(0.05)
        except: fail+=1
    bot.delete_message(message.chat.id, status_msg.message_id)
    bot.reply_to(message, f"✅ *Broadcast Selesai*\nSukses: `{ok}`\nGagal: `{fail}`", parse_mode='Markdown')
    show_main_menu(message.chat.id)

def process_broadcast_custom(m: types.Message):
    if m.text.lower() == "batal":
        bot.reply_to(m, "❌ Broadcast dibatalkan.")
        show_main_menu(m.chat.id)
        return
    run_broadcast(m, m.text)

def process_feedback(m: types.Message):
    if m.text.lower() == "batal": show_main_menu(m.chat.id, "❌ Batal."); return
    username = m.from_user.username if m.from_user.username else m.from_user.first_name
    db.save_feedback(m.from_user.id, username, m.text)
    notify_admins(f"🔔 *Feedback Masuk*\nDari: **@{username}**\nLihat di menu *'📑 Baca Feedback'*.")
    bot.reply_to(m, "✅ Feedback terkirim! Terima kasih."); show_main_menu(m.chat.id)

def send_feedback_report(m: types.Message):
    fb_list = db.get_all_feedback()
    if not fb_list: bot.reply_to(m, "Belum ada feedback."); return
    msg = "📑 *Laporan Feedback*\n\n"
    for i, fb in enumerate(fb_list[:5], 1):
        msg += f"*{i}. {fb['username']}*: {fb['message']}\n"
    bot.reply_to(m, msg, parse_mode='Markdown')

def send_feedback_export(m: types.Message):
    df = db.get_df_feedback()
    if df.empty: bot.reply_to(m, "Kosong."); return
    df.to_excel('Feedback.xlsx', index=False)
    bot.send_document(m.chat.id, open('Feedback.xlsx','rb'), caption="📂 Feedback Data")
    os.remove('Feedback.xlsx')

# --- TEXT HANDLER UTAMA ---
@bot.message_handler(func=lambda m: True)
def handle_text(m: types.Message):
    uid = m.from_user.id; txt = m.text
    prem, exp = db.check_premium(uid)

    # NAVIGASI
    if txt == "📊 Summary": send_summary(m, prem); return
    elif txt == "💰 Cek Saldo": send_saldo(m); return

    elif txt == "💡 Saran Cepat":
        sug = db.get_suggestions(uid)
        if not sug: bot.reply_to(m, "Data kurang.")
        else:
            mk = types.ReplyKeyboardMarkup(resize_keyboard=True,row_width=1); [mk.add(s) for s in sug]; mk.add("🏠 Menu Utama")
            bot.send_message(m.chat.id, "💡 Sering dipakai:", reply_markup=mk)
        return

    elif txt == "📂 Export Excel":
        send_export(m)
        return

    elif txt == "💎 Akun Saya":
        mk = types.InlineKeyboardMarkup()
        if prem:
            date_txt = "Selamanya" if exp.year==2099 else exp.strftime('%d-%m-%Y')
            msg = f"👤 *Status Akun*\n\n🏅 Level: *PREMIUM*\n📅 Exp: *{date_txt}*"
            mk.add(types.InlineKeyboardButton("🗑️ Reset / Hapus Data", callback_data="reset_confirm_menu"))
        else:
            msg = f"👤 *Status Akun*\n\n🏅 Level: *FREE*\n_Limitasi: Max 1 Item, No Voice/Scan_"
            mk.add(types.InlineKeyboardButton("💎 Upgrade", callback_data="upgrade_now"))
            mk.add(types.InlineKeyboardButton("🗑️ Reset / Hapus Data", callback_data="reset_confirm_menu"))
        bot.send_message(m.chat.id, msg, parse_mode='Markdown', reply_markup=mk)
        return

    # ADMIN TOOLS
    elif txt == "📢 Broadcast" and uid in ADMIN_IDS:
        mk = types.InlineKeyboardMarkup(row_width=1)
        mk.add(
            types.InlineKeyboardButton("✅ Bot Online / Aktif", callback_data="bc_online"),
            types.InlineKeyboardButton("🛠 Sedang Maintenance", callback_data="bc_maint"),
            types.InlineKeyboardButton("✨ Bot Sudah Update", callback_data="bc_update"),
            types.InlineKeyboardButton("⚠️ Bot Offline Sementara", callback_data="bc_off"),
            types.InlineKeyboardButton("✍️ Custom Pesan Sendiri", callback_data="bc_custom"),
            types.InlineKeyboardButton("❌ Batal", callback_data="delete_msg")
        )
        bot.reply_to(m, "📢 *Pilih Template Broadcast:*", parse_mode='Markdown', reply_markup=mk)
        return

    elif txt == "📑 Baca Feedback" and uid in ADMIN_IDS: send_feedback_report(m); return
    elif txt == "📂 Export Feedback" and uid in ADMIN_IDS: send_feedback_export(m); return
    elif txt == "🗑️ Reset Feedback" and uid in ADMIN_IDS:
        mk = types.InlineKeyboardMarkup(); mk.add(types.InlineKeyboardButton("🔥 HAPUS FEEDBACK", callback_data="reset_feedback_confirm"), types.InlineKeyboardButton("❌ Batal", callback_data="delete_msg"))
        bot.send_message(m.chat.id, "⚠️ Hapus semua feedback?", reply_markup=mk)
        return

    # USER TOOLS
    elif txt == "💬 Feedback":
        bot.reply_to(m, "✍️ Tulis masukan Anda (atau Batal):", parse_mode='Markdown'); bot.register_next_step_handler(m, process_feedback)
        return

    elif txt == "🏠 Menu Utama":
        show_main_menu(m.chat.id)
        return

    # INPUT
    else:
        check_and_send_ad(uid, m.chat.id, prem)
        bot.send_chat_action(m.chat.id, 'typing')
        data = ask_gemini(text=txt, mode="text")

        if not data: bot.reply_to(m, "🤔 Gagal baca. Coba format: _'Beli kopi 20rb'_", parse_mode='Markdown'); return

        if not prem and len(data) > 1:
            saved = data[0]; data = [saved]
            mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🚀 Buka Fitur", callback_data="upgrade_now"))
            confirm_data(m, data)
            bot.send_message(m.chat.id, f"🛑 *FITUR TERKUNCI*\nCuma simpan: *{saved['description']}*.\nUpgrade untuk Multi-Input!", parse_mode='Markdown', reply_markup=mk)
            return
        confirm_data(m, data)

def confirm_data(m: types.Message, data: List[Dict[str, Any]]):
    user_pending_data[m.from_user.id] = data
    msg = "🔍 *Konfirmasi*\n\n"; tot = 0
    for i, d in enumerate(data, 1):
        fmt = f"{d['amount']:,.0f}".replace(',', 'X').replace('.', ',').replace('X', '.')
        msg += f"*{i}. {d['description']}*\n   💰 Rp {fmt} | 📂 {d['category']}\n"
        tot += d['amount']
    ftot = f"{tot:,.0f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    msg += f"\n💵 Total: Rp {ftot}\nSimpan?"
    mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("✅ Simpan", callback_data="save_yes"), types.InlineKeyboardButton("❌ Batal", callback_data="save_no"))
    bot.reply_to(m, msg, parse_mode='Markdown', reply_markup=mk)

@bot.callback_query_handler(func=lambda c: True)
def cb(c: types.CallbackQuery):
    uid = c.from_user.id

    # --- HANDLER BROADCAST TEMPLATES ---
    if c.data.startswith("bc_"):
        if c.data == "bc_custom":
            bot.delete_message(c.message.chat.id, c.message.message_id)
            msg = bot.send_message(c.message.chat.id, "📢 *Mode Custom*\nKetik pesan Anda (atau 'Batal'):", parse_mode='Markdown')
            bot.register_next_step_handler(msg, process_broadcast_custom)
        else:
            templates = {
                "bc_online": "✅ *INFO BOT*\n\nBot sudah kembali ONLINE dan siap digunakan! Silakan lanjut mencatat keuangan. 🚀",
                "bc_maint": "🛠 *INFO MAINTENANCE*\n\nBot sedang dalam perbaikan/maintenance sebentar. Mohon bersabar ya, akan segera kembali! ⏳",
                "bc_update": "✨ *UPDATE BARU*\n\nBot baru saja di-update dengan fitur/perbaikan baru! Cek sekarang. 🆕",
                "bc_off": "⚠️ *INFO OFFLINE*\n\nBot akan nonaktif untuk sementara waktu. Kami akan kabari jika sudah aktif kembali. 🔌"
            }
            text = templates.get(c.data)
            if text: run_broadcast(c.message, text)
        return

    # --- HANDLER MENU INLINE ---
    if c.data == "open_main_menu":
        show_main_menu(c.message.chat.id)
        bot.answer_callback_query(c.id)
        return
    elif c.data == "open_help":
        help_cmd(c.message)
        bot.answer_callback_query(c.id)
        return

    if c.data == "save_yes":
        if uid in user_pending_data:
            for d in user_pending_data[uid]: db.save_transaction(uid, d)
            del user_pending_data[uid]; bot.edit_message_text(chat_id=c.message.chat.id, message_id=c.message.message_id, text="✅ Tersimpan!")
        else: bot.edit_message_text(chat_id=c.message.chat.id, message_id=c.message.message_id, text="⏳ Data kadaluarsa.")
    elif c.data == "save_no":
        if uid in user_pending_data: del user_pending_data[uid]
        bot.edit_message_text(chat_id=c.message.chat.id, message_id=c.message.message_id, text="❌ Dibatalkan.")
    elif c.data == "reset_confirm_menu":
        mk = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🔥 YA, HAPUS SEMUA", callback_data="reset_yes_final"),
               types.InlineKeyboardButton("❌ Batal", callback_data="delete_msg"))
        bot.edit_message_text(chat_id=c.message.chat.id, message_id=c.message.message_id,
                              text="⚠️ *ZONA BAHAYA*\n\nAnda yakin ingin menghapus SEMUA data? Tidak bisa kembali.",
                              parse_mode='Markdown', reply_markup=mk)
    elif c.data == "reset_yes_final":
        db.delete_user_data(uid);
        bot.edit_message_text(chat_id=c.message.chat.id, message_id=c.message.message_id, text="🗑️ Data Transaksi Bersih.")
    elif c.data == "reset_feedback_confirm":
        if uid in ADMIN_IDS:
            db.delete_all_feedback()
            bot.edit_message_text(chat_id=c.message.chat.id, message_id=c.message.message_id,
                                  text="🗑️ Semua Feedback telah dihapus.", parse_mode='Markdown')
        else:
            bot.edit_message_text(chat_id=c.message.chat.id, message_id=c.message.message_id, text="❌ Akses ditolak.")
    elif c.data == "delete_msg":
        bot.delete_message(c.message.chat.id, c.message.message_id)
    elif c.data == "upgrade_now":
        handle_upgrade(c.message)
        bot.answer_callback_query(c.id, text="Membuka menu upgrade...")
    else:
        bot.answer_callback_query(c.id)

# --- HELPERS DATA DISPLAY ---
def send_summary(m: types.Message, prem: bool):
    # Gunakan db.get_df
    df = db.get_df(m.chat.id); now = datetime.now(); now_s = now.strftime("%Y-%m")
    if df.empty: bot.reply_to(m, "Data kosong."); return

    df_m = df[df['datetime'].dt.strftime('%Y-%m')==now_s]
    if df_m.empty: bot.reply_to(m, f"Data {now_s} kosong."); return

    inc = df_m[df_m['type']=='IN']['amount'].sum()
    exp = df_m[df_m['type']=='OUT']['amount'].sum()
    f_inc = f"{inc:,.0f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    f_exp = f"{exp:,.0f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    f_cf = f"{inc-exp:,.0f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    txt = f"📊 *Laporan {now_s}*\n➕ Masuk: `Rp {f_inc}`\n➖ Keluar: `Rp {f_exp}`\n💰 Cashflow: `Rp {f_cf}`"

    df_out = df_m[df_m['type']=='OUT']
    if not df_out.empty:
        try:
            cats = df_out.groupby('category')['amount'].sum()
            plt.figure(figsize=(6,6));
            cats.plot.pie(autopct='%1.0f%%', startangle=90)
            plt.ylabel('') 
            plt.title(f"Pengeluaran {now_s}")
            plt.savefig('c.png'); plt.close()
            bot.send_photo(m.chat.id, open('c.png','rb'), caption=txt, parse_mode='Markdown'); os.remove('c.png')
        except Exception as e:
            print(f"Plot error: {e}")
            bot.send_message(m.chat.id, txt, parse_mode='Markdown')
    else: bot.send_message(m.chat.id, txt, parse_mode='Markdown')

def send_saldo(m: types.Message):
    df = db.get_df(m.chat.id); txt = "💰 *Saldo Dompet*\n"
    if df.empty: bot.reply_to(m, "Kosong."); return
    tot = 0
    for w in df['wallet'].unique():
        val = df[(df['wallet']==w) & (df['type']=='IN')]['amount'].sum() - df[(df['wallet']==w) & (df['type']=='OUT')]['amount'].sum()
        if val!=0:
            f_val = f"{val:,.0f}".replace(',', 'X').replace('.', ',').replace('X', '.')
            txt += f"🔹 {w}: `Rp {f_val}`\n"; tot+=val

    f_tot = f"{tot:,.0f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    txt += f"\n💵 Total Aset: `Rp {f_tot}`"
    bot.send_message(m.chat.id, txt, parse_mode='Markdown')

def send_export(m: types.Message):
    df = db.get_df(m.chat.id)
    if df.empty: bot.reply_to(m, "Kosong."); return
    df.insert(0, 'No', range(1, 1 + len(df)))
    df.drop(columns=['id','user_id'], errors='ignore', inplace=True)
    df['datetime'] = pd.to_datetime(df['datetime']).dt.strftime('%d-%m-%Y %H:%M')
    df['amount'] = df['amount'].astype(float)
    df.rename(columns={'datetime': 'Waktu', 'type': 'Tipe', 'amount': 'Nominal', 'category': 'Kategori', 'wallet': 'Dompet', 'description': 'Keterangan'}, inplace=True)
    df.to_excel('Laporan.xlsx', index=False)
    bot.send_document(m.chat.id, open('Laporan.xlsx','rb'), caption="📂 Excel Export"); os.remove('Laporan.xlsx')

if __name__ == "__main__":
    try:
        # DB sudah di-init di atas
        notify_admins("✅ *Bot Online*\n")
        print("Bot Siap...")
        bot.infinity_polling()
    except Exception as e:
        print(f"Error: {e}")
        notify_admins(f"⚠️ *Bot Error:*\n {e}")
        time.sleep(5)
    finally:
        notify_admins("⚠️ *Bot Offline*\n")
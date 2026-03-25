import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType, OpenOrderParams
from py_clob_client.order_builder.constants import BUY, SELL

load_dotenv()

print("=== BOT STARTING ===")
print(f"Time: {datetime.now()}")
print(f"POLY_PRIVATE_KEY present: {'Yes' if os.getenv('POLY_PRIVATE_KEY') else 'NO - MISSING!'}")
print(f"POLY_FUNDER present: {'Yes' if os.getenv('POLY_FUNDER') else 'NO - MISSING!'}")
print(f"POLY_SIG_TYPE: {os.getenv('POLY_SIG_TYPE')}")

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137
SIZE = 10.0

POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY")
POLY_FUNDER = os.getenv("POLY_FUNDER")
POLY_SIG_TYPE = int(os.getenv("POLY_SIG_TYPE", 0))

if not POLY_PRIVATE_KEY or not POLY_FUNDER:
    print("❌ CRITICAL: Missing POLY_PRIVATE_KEY or POLY_FUNDER!")
    raise ValueError("Missing required environment variables")

client = None
try:
    client = ClobClient(
        HOST,
        key=POLY_PRIVATE_KEY,
        chain_id=CHAIN_ID,
        signature_type=POLY_SIG_TYPE,
        funder=POLY_FUNDER,
    )
    print("✅ ClobClient created successfully")
except Exception as e:
    print(f"❌ Failed to create ClobClient: {e}")

# API creds
if os.getenv("POLY_API_KEY") and os.getenv("POLY_API_SECRET") and os.getenv("POLY_API_PASSPHRASE"):
    client.set_api_creds({
        "apiKey": os.getenv("POLY_API_KEY"),
        "secret": os.getenv("POLY_API_SECRET"),
        "passphrase": os.getenv("POLY_API_PASSPHRASE"),
    })
    print("✅ API credentials set")
else:
    print("⚠️ Using derived API creds")

OUTCOME = "Up"

def get_current_btc_5m_event():
    now = int(time.time())
    window_start = (now // 300) * 300
    candidates = [f"btc-updown-5m-{window_start}", f"btc-updown-5m-{window_start - 300}", f"btc-updown-5m-{window_start + 300}"]
    
    for slug in candidates:
        try:
            resp = requests.get(f"https://gamma-api.polymarket.com/events/{slug}", timeout=10)
            if resp.status_code == 200:
                event = resp.json()
                print(f"✅ Found market: {event.get('title', slug)}")
                return event
        except:
            pass
    
    # Fallback search
    try:
        resp = requests.get("https://gamma-api.polymarket.com/events?active=true&closed=false&limit=200", timeout=15)
        data = resp.json()
        events = data if isinstance(data, list) else data.get("data") or data.get("events") or []
        for event in events:
            slug = str(event.get("slug", "")).lower()
            title = str(event.get("title", "")).lower()
            if "btc-updown-5m" in slug or ("bitcoin" in title and "up or down" in title and "5 min" in title):
                print(f"✅ Found via search: {event.get('title')}")
                return event
    except Exception as e:
        print(f"Search failed: {e}")
    
    print(f"[{datetime.now().strftime('%H:%M:%S')}] No market found yet, retrying...")
    raise ValueError("No active BTC 5-min market found yet")

# (rest of the functions and main loop remain the same as previous version - get_token_id, execute_trade, while True loop)

def get_token_id(event, outcome: str):
    if "clobTokenIds" in event and "outcomes" in event:
        try:
            idx = event["outcomes"].index(outcome)
            return event["clobTokenIds"][idx]
        except:
            pass
    if "markets" in event and event["markets"]:
        market = event["markets"][0]
        if "clobTokenIds" in market and "outcomes" in market:
            try:
                idx = market["outcomes"].index(outcome)
                return market["clobTokenIds"][idx]
            except:
                pass
    raise ValueError(f"Could not find token for {outcome}")

def execute_trade(token_id: str, trade_size: float = SIZE):
    mo = MarketOrderArgs(token_id=token_id, amount=trade_size, side=BUY, order_type=OrderType.FOK)
    signed_mo = client.create_market_order(mo)
    buy_resp = client.post_order(signed_mo, OrderType.FOK)
    print(f"[{OUTCOME}] Market buy: {buy_resp}")

    filled_size = trade_size

    stop_args = OrderArgs(token_id=token_id, price=0.45, size=filled_size, side=SELL)
    signed_stop = client.create_order(stop_args)
    stop_resp = client.post_order(signed_stop, OrderType.GTC)
    stop_id = stop_resp.get("id") if isinstance(stop_resp, dict) else None

    tp_args = OrderArgs(token_id=token_id, price=0.99, size=filled_size, side=SELL)
    signed_tp = client.create_order(tp_args)
    tp_resp = client.post_order(signed_tp, OrderType.GTC)
    tp_id = tp_resp.get("id") if isinstance(tp_resp, dict) else None

    print(f"[{OUTCOME}] Stop @0.45 | TP @0.99")
    return stop_id, tp_id, filled_size

# Main loop
print(f"🚀 BTC 5-min {OUTCOME} bot is now running - waiting for next market...")

last_slug = None
current_stop_id = None
current_tp_id = None
current_token_id = None
current_size = 0.0

while True:
    try:
        event = get_current_btc_5m_event()
        slug = event.get("slug") or event.get("id")

        if slug != last_slug:
            print(f"🟢 NEW WINDOW: {slug}")
            if current_stop_id or current_tp_id:
                client.cancel_all()
            last_slug = slug
            token_id = get_token_id(event, OUTCOME)
            current_token_id = token_id
            current_stop_id, current_tp_id, current_size = execute_trade(token_id)

        else:
            if not current_token_id:
                time.sleep(2)
                continue

            open_orders = client.get_orders(OpenOrderParams(token_id=current_token_id))
            open_ids = [o.get("id") for o in (open_orders if isinstance(open_orders, list) else [])]

            stop_open = current_stop_id in open_ids if current_stop_id else False
            tp_open = current_tp_id in open_ids if current_tp_id else False

            if current_stop_id and not stop_open:
                print(f"🔴 Stop-loss hit - re-entering")
                if current_tp_id and tp_open:
                    client.cancel(current_tp_id)
                current_stop_id, current_tp_id, current_size = execute_trade(current_token_id, current_size)

            elif current_tp_id and not tp_open:
                print(f"🟢 Take-profit hit - Win!")
                if current_stop_id and stop_open:
                    client.cancel(current_stop_id)
                current_stop_id = current_tp_id = None

        time.sleep(2)

    except Exception as e:
        if "No active" not in str(e):
            print(f"Error: {e}")
        time.sleep(2)

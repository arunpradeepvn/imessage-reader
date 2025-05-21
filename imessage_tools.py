import time
import datetime
import requests
import sqlite3
import re
import os

SERVER_URL = "http://localhost:3000"

def get_last_fetched_time():
    try:
        response = requests.get(f"{SERVER_URL}/api/lastfetchedtime")
        if response.status_code == 200:
            raw_ts = response.json().get("lastfetchedtime")

            if raw_ts is None:
                return None  # No previous timestamp

            return int(raw_ts)  # Convert string or number to int
        return None  # API responded but no timestamp
    except Exception as e:
        print(f"Error fetching last fetched time: {e}")
        return "_FAIL_"


def update_last_fetched_time(timestamp):
    try:
        url = f"{SERVER_URL}/api/lastfetchedtime"
        payload = {"lastfetchedtime": str(timestamp)}
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("🕒 Timestamp updated successfully.")
        else:
            print(f"❌ Failed to update timestamp: {response.status_code}")
    except Exception as e:
        print(f"🚨 Error updating timestamp: {e}")

def get_current_apple_timestamp():
    apple_epoch = datetime.datetime(2001, 1, 1, tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - apple_epoch
    return int(delta.total_seconds() * 1_000_000_000)  # nanoseconds

def send_to_api(messages, timestamp):
    url = f"{SERVER_URL}/api/message"
    payload = {"messages": messages, "timestamp": str(timestamp)}
    headers = {'Content-Type': 'application/json'}

    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            print(f"✅ {len(messages)} messages successfully sent to the API")
            return True
        else:
            print(f"❌ Failed to send messages to the API: {response.status_code}")
            return False
    except Exception as e:
        print(f"🚨 Error sending messages to the API: {e}")
        return False

def get_chat_mapping(db_location):
    try:
        conn = sqlite3.connect(db_location)
        cursor = conn.cursor()
        cursor.execute("SELECT room_name, display_name FROM chat")
        mapping = {room_name: display_name for room_name, display_name in cursor.fetchall()}
        conn.close()
        return mapping
    except Exception as e:
        print(f"Error getting chat mapping: {e}")
        return {}

def extract_rtf_text(data):
    try:
        if not data:
            return "No content"
        text = data.decode('utf-8', errors='replace')
        text = re.sub(r'(NSString|NSAttributedString|NSValue|NSNumber|NSDictionary|NSObject|streamtype|iI|__kIMMessagePartAttributeName|\*|@|\+|data|file|NSLog|NSRange)', '', text)
        text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
        text = re.sub(r'^[^\w]*d*', '', text)
        text = re.sub(r'\bi\b', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip() or "Unreadable message content"
    except Exception as e:
        print(f"Error extracting text: {e}")
        return "Unreadable message content"
    
def prompt_mac_permission():
    print("""
    🚨 macOS Permission Required 🚨

    This script needs access to the Messages database.

    ➡️ Go to:
    System Settings > Privacy & Security > Full Disk Access

    ✅ Enable Full Disk Access for the Terminal (or the app running this script)

    🔁 Then restart the script
    """)


def read_messages(db_location, last_timestamp):
    try:
        conn = sqlite3.connect(db_location)
        cursor = conn.cursor()
        
        query = """
        SELECT message.ROWID, message.date, message.text, message.attributedBody, 
               handle.id, message.is_from_me, message.cache_roomnames
        FROM message
        LEFT JOIN handle ON message.handle_id = handle.ROWID
        WHERE message.date > ?
        ORDER BY message.date ASC
        """
        
        cursor.execute(query, (last_timestamp,))
        results = cursor.fetchall()
        mapping = get_chat_mapping(db_location)

        messages = []
        for rowid, date, text, attributed_body, handle_id, is_from_me, cache_roomname in results:
            body = text
            if not body and attributed_body:
                body = extract_rtf_text(attributed_body)
            
            phone_number = handle_id or "Me"
            mapped_name = mapping.get(cache_roomname, "Unknown")

            # Convert Apple timestamp to datetime
            mod_date = datetime.datetime(2001, 1, 1)
            timestamp = (mod_date + datetime.timedelta(seconds=date / 1000000000)).strftime("%Y-%m-%d %H:%M:%S")

            messages.append({
                "rowid": rowid,
                "date": timestamp,
                "body": body,
                "phone_number": phone_number,
                "is_from_me": is_from_me,
                "cache_roomname": cache_roomname,
                "group_chat_name": mapped_name
            })

        conn.close()
        return messages
    except Exception as e:
        print(f"Error reading messages: {e}")
        return []

def run_continuously(db_location):
    # Initial delay to ensure server is up
    time.sleep(2)
    
    while True:
        try:
            # Get the last timestamp we processed from the server
            last_processed_timestamp = get_last_fetched_time()

            if last_processed_timestamp == "_FAIL_":
                print("❌ Failed to connect to server to get last fetched timestamp.")
                print("🛑 Exiting process due to connection failure.")
                return  # or exit(1)
            
            if last_processed_timestamp is None:
                print("ℹ️  No previous timestamp found. Fetching all messages...")
                messages = read_messages(db_location, 0)
            else:
                print(f"🔄 Checking for new messages since {last_processed_timestamp}...")
                messages = read_messages(db_location, last_processed_timestamp)
            
            newest_timestamp = get_current_apple_timestamp()

            if messages:
                print(f"📨 Found {len(messages)} new messages")
                if send_to_api(messages, newest_timestamp):
                    print(f"✅ Successfully processed {len(messages)} messages")
                    print(f"🕒 Updated timestamp to: {newest_timestamp}")
                    update_last_fetched_time(newest_timestamp)
                else:
                    print("⚠️ Failed to send messages, will retry next cycle")
            else:
                print("ℹ️  No new messages found")
                print(f"🕒 Still updating last fetched time to: {newest_timestamp}")
                update_last_fetched_time(newest_timestamp)

            # Wait for 1 minute before checking again (adjust as needed)
            print("⏳ Waiting for 60 seconds before next check...")
            time.sleep(60)
            
        except Exception as e:
            print(f"🚨 Error in main loop: {e}")
            print("🔄 Retrying in 30 seconds...")
            time.sleep(30)

def has_permission(db_location):
    return os.access(db_location, os.R_OK)

if __name__ == "__main__":
    db_location = "/Users/achit226/Library/Messages/chat.db"  # Update with your DB path

    if not has_permission(db_location):
        print("❗ Cannot read the database file.")
        prompt_mac_permission()
        exit(1)

    run_continuously(db_location)
"""Publish recruit4 (KINGDOM KHATRE MEIN, 50k) to akhiri_jung. Run from the repo root.
python channels/rabbit-war/recruit4/post.py [--check]   (--check: account + video link only, no post)"""
import os, sys, time, json, pathlib, requests
from dotenv import load_dotenv
load_dotenv(); sys.path.insert(0, '.')
os.environ["IG_ACCESS_TOKEN"] = os.environ["IG_ACCESS_TOKEN_AKHIRI"]; os.environ["IG_USER_ID"] = os.environ["IG_USER_ID_AKHIRI"]
from engine import instagram
D = pathlib.Path('channels/rabbit-war/recruit4'); V = "v23.0"
url = f"https://raw.githubusercontent.com/dtechmonkey/insta-automation/main/channels/rabbit-war/recruit4/reel.mp4"
for i in range(30):
    r = requests.get(url, headers={"Range": "bytes=0-99"}, timeout=30)
    if r.status_code in (200, 206) and str((D / 'reel.mp4').stat().st_size) in (r.headers.get('content-range') or ''): print("video public"); break
    time.sleep(10)
else: sys.exit("video not public yet")
who = instagram._call("GET", os.environ["IG_USER_ID"].strip(), V, fields="username")
print("account:", who.get("username"))
if who.get("username") != "akhiri_jung": sys.exit("wrong account")
if '--check' in sys.argv: sys.exit(0)
cap = (D / 'caption.txt').read_text(encoding='utf-8').strip()
mid = instagram.find_posted(cap, V)
if mid: print("already posted", mid)
else: mid = instagram.publish_reel(url, cap, V, share_to_feed=True, thumb_ms=5000)
link = instagram._call("GET", mid, V, fields="permalink").get("permalink")
print("media id", mid, link)
json.dump({"media_id": mid, "permalink": link, "posted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, open(D / 'posted.json', 'w'))

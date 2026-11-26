"""Publish Day 2 (BUNTY KAUN HAI?) to akhiri_jung. Run from the repo root.
python channels/rabbit-war/day02/post.py [--check] [--at HH:MM]   (--check: account + video link only, no post)"""
import os, sys, time, json, pathlib, requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv(); sys.path.insert(0, '.')
os.environ["IG_ACCESS_TOKEN"] = os.environ["IG_ACCESS_TOKEN_AKHIRI"]; os.environ["IG_USER_ID"] = os.environ["IG_USER_ID_AKHIRI"]
from engine import instagram
D = pathlib.Path('channels/rabbit-war/day02'); V = "v23.0"
url = f"https://raw.githubusercontent.com/dtechmonkey/insta-automation/main/channels/rabbit-war/day02/reel.mp4"
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
else:
    at = None
    if '--at' in sys.argv:   # IST time today (or tomorrow if already past); uploads now, publishes then
        hh, mm = map(int, sys.argv[sys.argv.index('--at') + 1].split(':')); IST = timezone(timedelta(hours=5, minutes=30))
        at = datetime.now(IST).replace(hour=hh, minute=mm, second=0, microsecond=0)
        if at < datetime.now(IST): at += timedelta(days=1)
        print("will publish at", at.isoformat())
    mid = instagram.publish_reel(url, cap, V, share_to_feed=True, cover_url=url.replace('reel.mp4', 'cover.jpg'), at=at)
link = instagram._call("GET", mid, V, fields="permalink").get("permalink")
print("media id", mid, link)
json.dump({"media_id": mid, "permalink": link, "posted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, open(D / 'posted.json', 'w'))

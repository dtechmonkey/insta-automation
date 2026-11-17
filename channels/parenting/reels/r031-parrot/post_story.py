"""Post one video to the Let Me Try Story through the API. Used for the r031 teaser on 18 Nov 2026, 16:00 IST.
python channels/parenting/reels/r031-parrot/post_story.py [--check]   (run from the repo root; --check posts nothing)"""
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
load_dotenv(ROOT / ".env")
for name in ("IG_ACCESS_TOKEN", "IG_USER_ID", "IG_GRAPH_BASE"):
    if os.environ.get(f"{name}_PARENTING"):
        os.environ[name] = os.environ[f"{name}_PARENTING"]
from engine import instagram  # noqa: E402

URL = f"https://raw.githubusercontent.com/{os.environ['GITHUB_REPOSITORY']}/main/channels/parenting/reels/r031-parrot/teaser2.mp4"
VERSION = "v23.0"
log = Path(__file__).with_name("post_story.log")
try:
    if "--check" in sys.argv:
        instagram.check_video(URL, "STORIES", VERSION)
        msg = "check OK (nothing posted)"
    else:
        msg = f"Story published: {instagram.publish_story(URL, VERSION)}"
except Exception as e:  # noqa: BLE001
    msg = f"FAILED: {e}"
line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
print(line)
with log.open("a", encoding="utf8") as f:
    f.write(line + "\n")

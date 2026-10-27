"""Copy finished bank posts (output/bank/<post>/READY) into the committed bank/ folder for GitHub Actions.

  python tools/bank_sync.py      # then commit + push bank/
Only the slides, caption and links travel; prompts and raw images stay on this PC.
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC, DEST = ROOT / "output" / "bank", ROOT / "bank"


def main():
    posted = {p.get("bank_id") for p in json.loads((ROOT / "channels/ai-news/history.json").read_text(encoding="utf-8"))["posts"]}
    added = 0
    for post in sorted(SRC.glob("*")):
        if not (post / "READY").exists() or post.name in posted or (DEST / post.name / "READY").exists():
            continue
        out = DEST / post.name
        out.mkdir(parents=True, exist_ok=True)
        for f in [*post.glob("[0-9][0-9].jpg"), post / "caption.txt", post / "links.txt"]:
            if f.exists():
                shutil.copy2(f, out / f.name)
        (out / "READY").write_text("", encoding="utf-8")
        added += 1
        print(f"+ {post.name}")
    waiting = [d.name for d in DEST.glob("*") if (d / "READY").exists()] if DEST.exists() else []
    print(f"added {added}; {len(waiting)} posts waiting in bank/")


if __name__ == "__main__":
    main()

"""Tiny local receiver: the Gemini page POSTs an image here, we save it as a 1080x1350 slide background.

  python tools/receiver.py        # listens on 127.0.0.1:8765 only (not reachable from other machines)
  POST /save?dest=output/bank/<post>/img/NN.jpg   body = image bytes
"""
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
BANK = (ROOT / "output" / "bank").resolve()


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "https://gemini.google.com")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_POST(self):
        dest = parse_qs(urlparse(self.path).query).get("dest", [""])[0]
        target = (ROOT / dest).resolve()
        if not str(target).startswith(str(BANK)) or target.suffix != ".jpg":
            self.send_response(400); self._cors(); self.end_headers(); return
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if urlparse(self.path).path == "/form":  # form submission: img=data:image/...;base64,....
            import base64
            value = parse_qs(data.decode("ascii", "ignore")).get("img", [""])[0]
            data = base64.b64decode(value.split(",", 1)[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".img") as tmp:
            tmp.write(data)
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", tmp.name,
                        "-vf", "scale=1080:1350:force_original_aspect_ratio=increase,crop=1080:1350,unsharp=5:5:0.6",
                        "-q:v", "2", str(target)], check=True)
        Path(tmp.name).unlink()
        self.send_response(200); self._cors(); self.end_headers()
        self.wfile.write(f"SAVED {len(data)} bytes".encode())

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()

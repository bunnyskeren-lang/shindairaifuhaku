import http.server
import sys
import os

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

class Handler(http.server.BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        filename = self.headers.get("X-Filename", "upload.txt")
        filename = os.path.basename(filename)
        path = os.path.join(OUT_DIR, filename)
        with open(path, "wb") as f:
            f.write(body)
        print(f"saved {len(body)} bytes to {path}", flush=True)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(f"OK {len(body)} bytes -> {filename}".encode("utf-8"))

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"listening on 127.0.0.1:{port}, saving to {OUT_DIR}", flush=True)
    server.serve_forever()

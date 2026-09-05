"""Tableau de bord web minimal (stdlib uniquement, sans dépendance en plus).

Sert deux choses sur le même port :
- "/" : la page HTML listant les sessions enregistrées (sessions.csv).
- une image de calibration (ex "/calibrate.png"), produite par
  calibrate_zone.py, pour lire les coordonnées pixels de la zone de travail
  depuis le téléphone sans passer par SSH.
"""

import csv
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

SESSIONS_FILE = "sessions.csv"
PORT = 8000
IMAGE_FILES = {"/calibrate.png", "/calibrate_day.png", "/frame_test.png"}


def load_sessions():
    if not os.path.isfile(SESSIONS_FILE):
        return []
    with open(SESSIONS_FILE, newline="") as f:
        return list(csv.DictReader(f))


def render_html():
    sessions = load_sessions()
    rows = "".join(
        f"<tr><td>{s['machine']}</td><td>{s['debut']}</td><td>{s['fin']}</td><td>{s['duree_secondes']}</td></tr>"
        for s in reversed(sessions)
    )
    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>Suivi de machine</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{ font-family: sans-serif; padding: 20px; background: #111; color: #eee; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #444; padding: 8px; text-align: left; }}
            th {{ background: #222; }}
        </style>
    </head>
    <body>
        <h1>Suivi de machine</h1>
        <p>{len(sessions)} session(s) enregistree(s). Page actualisee automatiquement toutes les 30s.</p>
        <table>
            <tr><th>Machine</th><th>Debut</th><th>Fin</th><th>Duree (s)</th></tr>
            {rows}
        </table>
    </body>
    </html>
    """


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in IMAGE_FILES:
            filename = self.path.lstrip("/")
            if os.path.isfile(filename):
                self.send_response(200)
                self.send_header("Content-type", "image/png")
                self.end_headers()
                with open(filename, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(render_html().encode("utf-8"))

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Tableau de bord disponible sur le port {PORT}")
    server.serve_forever()

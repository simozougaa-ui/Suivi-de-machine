"""Tableau de bord web minimal (stdlib uniquement, sans dépendance en plus).

Sert plusieurs choses sur le même port :
- "/" : la page HTML listant les sessions enregistrées (sessions.csv).
- une image de calibration (ex "/calibrate.png"), produite par
  calibrate_zone.py, pour lire les coordonnées pixels de la zone de travail
  depuis le téléphone sans passer par SSH.
- "/contact" : les pages de validation produites par contact_sheet.py
  (contact_pages/page_NN.png), une à la fois, avec navigation
  précédent/suivant — lisible sur téléphone.
"""

import csv
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

SESSIONS_FILE = "sessions.csv"
PORT = 8000
IMAGE_FILES = {
    "/calibrate.png",
    "/calibrate_day.png",
    "/frame_test.png",
    "/reference_background.png",
    "/machine_mask_preview.png",
    "/reference_fragments.png",
    "/zones_debug.png",
    "/contact_sheet.png",
}

CONTACT_PAGES_DIR = "contact_pages"
# N'accepte QUE ce motif exact ("page_" + 2 chiffres + ".png") : pas de
# traversee de chemin possible (aucun "/", ".." ou autre caractere permis).
CONTACT_IMAGE_RE = re.compile(r"^/contact_pages/(page_\d{2}\.png)$")


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


def list_contact_pages():
    if not os.path.isdir(CONTACT_PAGES_DIR):
        return []
    names = [f for f in os.listdir(CONTACT_PAGES_DIR) if re.match(r"^page_\d{2}\.png$", f)]
    return sorted(names)


def render_contact(page_num):
    pages = list_contact_pages()
    if not pages:
        return """
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Feuille de contact</title>
            <style>body { font-family: sans-serif; padding: 20px; background: #111; color: #eee; }</style>
        </head>
        <body>
            <h2>Feuille de contact</h2>
            <p>Aucune page disponible pour l'instant. Lancez contact_sheet.py sur le Jetson.</p>
        </body>
        </html>
        """

    total = len(pages)
    page_num = max(1, min(page_num, total))
    prev_link = f'<a href="/contact?page={page_num - 1}">&larr; Precedent</a>' if page_num > 1 else ""
    next_link = f'<a href="/contact?page={page_num + 1}">Suivant &rarr;</a>' if page_num < total else ""

    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Feuille de contact</title>
        <style>
            body {{ font-family: sans-serif; padding: 0; margin: 0; background: #111; color: #eee; text-align: center; }}
            h2 {{ padding: 12px 0 4px; margin: 0; font-size: 1.1em; }}
            img {{ width: 100%; height: auto; display: block; }}
            .nav {{ padding: 10px 0; }}
            .nav a {{ color: #5af; text-decoration: none; margin: 0 16px; font-size: 1.1em; }}
        </style>
    </head>
    <body>
        <h2>Feuille de contact — page {page_num}/{total}</h2>
        <div class="nav">{prev_link} {next_link}</div>
        <img src="/contact_pages/page_{page_num:02d}.png" alt="page {page_num}">
        <div class="nav">{prev_link} {next_link}</div>
    </body>
    </html>
    """


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/contact":
            query = urllib.parse.parse_qs(parsed.query)
            try:
                page_num = int(query.get("page", ["1"])[0])
            except ValueError:
                page_num = 1
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(render_contact(page_num).encode("utf-8"))
            return

        contact_match = CONTACT_IMAGE_RE.match(path)
        if contact_match:
            filename = os.path.join(CONTACT_PAGES_DIR, contact_match.group(1))
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

        if path in IMAGE_FILES:
            filename = path.lstrip("/")
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

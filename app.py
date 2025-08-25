import os, json, importlib, subprocess
from flask import Flask, jsonify, send_file, Response

# Crea credenciales.json desde el secreto (no subas el archivo al repo)
CREDS = os.getenv("GOOGLE_CREDENTIALS_JSON")
if CREDS:
    with open("credenciales.json", "w", encoding="utf-8") as f:
        f.write(CREDS)

PERSIST_DIR = os.getenv("PERSIST_DIR", "/data")
QR_PATH = os.path.join(PERSIST_DIR, "qr.png")

# (Opcional) simple token para proteger /run
RUN_TOKEN = os.getenv("RUN_TOKEN")  # si lo estableces, llama /run?key=TOKEN

app = Flask(__name__)
RUNNING = False

def call_cumple():
    global RUNNING
    if RUNNING:
        return False, "Ya hay una ejecución en curso"
    RUNNING = True
    try:
        mod = importlib.import_module("cumple")  # tu archivo: cumple.py
        if hasattr(mod, "run_job"):
            mod.run_job()
        elif hasattr(mod, "main"):
            mod.main()
        else:
            subprocess.run(["python", "cumple.py"], check=True)
        RUNNING = False
        return True, "OK"
    except Exception as e:
        RUNNING = False
        return False, str(e)

@app.get("/")
def health():
    return "✅ Servicio activo. Usa /run para disparar y /qr para ver el QR."

@app.get("/run")
def run():
    if RUN_TOKEN and (os.getenv("RUN_TOKEN") is not None):
        from flask import request
        if request.args.get("key") != RUN_TOKEN:
            return jsonify({"ok": False, "msg": "Unauthorized"}), 401
    ok, msg = call_cumple()
    return jsonify({"ok": ok, "msg": msg}), (200 if ok else 409)

@app.get("/qr")
def qr():
    if os.path.exists(QR_PATH):
        return send_file(QR_PATH, mimetype="image/png")
    return Response("No hay QR aún. Genera abriendo WhatsApp en tu script y guardando /data/qr.png.", 404)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

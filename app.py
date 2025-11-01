# server_light.py
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import os, re
import requests
from urllib.parse import urlparse, unquote
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

app = Flask(__name__)
CORS(app, supports_credentials=True)

# ===== Allowlist: só Mercado Livre =====
ALLOWED_EXACT = {"api.mercadolibre.com"}
ALLOWED_SUFFIXES = (".mercadolivre.com.br", ".mercadolibre.com")

# ===== Timeouts & Pool =====
CONNECT_TO = float(os.getenv("LR_CONNECT_TIMEOUT", "3.5"))
READ_TO    = float(os.getenv("LR_READ_TIMEOUT", "10.0"))
POOL_CONN  = int(os.getenv("LR_POOL_CONNECTIONS", "50"))
POOL_MAX   = int(os.getenv("LR_POOL_MAXSIZE", "100"))

# ===== Proxy estático (opcional) =====
USE_PROXY = os.getenv("LR_USE_PROXY", "0").strip().lower() in ("1","true","on","yes")
PROXY_URL = os.getenv("LR_PROXY_URL", "").strip()  # ex: http://user:pass@host:port
PROXIES   = {"http": PROXY_URL, "https": PROXY_URL} if (USE_PROXY and PROXY_URL) else None

# ===== Headers básicos =====
DEFAULT_OUT_HEADERS = {
    "User-Agent": os.getenv("LR_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                                             "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}
FORWARD_INBOUND = (
    "Authorization","Accept","Accept-Language","User-Agent",
    "Range","If-None-Match","If-Modified-Since","Cache-Control","Pragma",
    "Content-Type"  # (relevante se você habilitar POST depois)
)

HOP_BY_HOP = {
    "connection","keep-alive","proxy-authenticate","proxy-authorization",
    "te","trailers","transfer-encoding","upgrade"
}

session = requests.Session()
adapter = HTTPAdapter(
    pool_connections=POOL_CONN,
    pool_maxsize=POOL_MAX,
    max_retries=Retry(total=0)  # sem retries no light
)
session.mount("https://", adapter)
session.mount("http://", adapter)
session.trust_env = False  # ignora proxies do sistema

def is_allowed(target: str) -> bool:
    try:
        u = urlparse(target)
        if u.scheme not in ("http","https"): return False
        host = (u.hostname or "").lower()
        if host in ALLOWED_EXACT: return True
        return any(host.endswith(suf) for suf in ALLOWED_SUFFIXES)
    except Exception:
        return False

def add_cors(resp: Response):
    origin = request.headers.get("Origin")
    resp.headers["Access-Control-Allow-Origin"] = origin or "*"
    # Só anuncie credenciais se for ecoar a Origin (não com "*")
    resp.headers["Access-Control-Allow-Credentials"] = "true" if origin else "false"
    req_method  = request.headers.get("Access-Control-Request-Method")
    req_headers = request.headers.get("Access-Control-Request-Headers")
    resp.headers["Access-Control-Allow-Methods"] = req_method or "GET,HEAD,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = req_headers or (
        "Content-Type, Authorization, If-None-Match, If-Modified-Since, Range, Cache-Control, Pragma"
    )
    resp.headers["Access-Control-Expose-Headers"] = (
        "Content-Type, ETag, Cache-Control, Last-Modified, Location, Content-Range, "
        "Content-Length, X-Proxy-Final-Url, X-Proxy-Static"
    )
    resp.headers["Access-Control-Max-Age"] = "86400"
    resp.headers["Vary"] = "Origin, Access-Control-Request-Headers, Access-Control-Request-Method"
    return resp

def _mask_proxy(url: str) -> str:
    try:
        u = urlparse(url)
        host = u.hostname or ""
        port = f":{u.port}" if u.port else ""
        return f"{u.scheme}://{host}{port}"
    except Exception:
        return ""

@app.route("/_health", methods=["GET"])
def _health():
    return "ok", 200

# Preflight
@app.route("/", defaults={"raw": ""}, methods=["OPTIONS"])
@app.route("/<path:raw>", methods=["OPTIONS"])
def _opts(raw):
    return add_cors(Response(status=204))

@app.route("/", defaults={"raw": ""}, methods=["GET","HEAD"])
@app.route("/<path:raw>", methods=["GET","HEAD"])
def light_proxy(raw: str):
    """
    Uso:
      /https://api.mercadolibre.com/items/MLB123
      /?u=https://api.mercadolibre.com/sites/MLB/search?q=...
    """
    # 1) Resolve target a partir de path ou ?u=
    if raw:
        target = unquote(raw)
    else:
        target = request.args.get("u","").strip()

    if not target:
        return add_cors(Response("OK - use /https://<url> ou ?u=<url>", status=200))

    # 2) Valida host
    if not is_allowed(target):
        return add_cors(Response("Host não permitido", status=400))

    # 3) Monta headers de saída
    out_headers = dict(DEFAULT_OUT_HEADERS)
    for k in FORWARD_INBOUND:
        v = request.headers.get(k)
        if v: out_headers[k] = v

    # 4) Dispara upstream (sem cookies, sem rotação, sem retries)
    try:
        r = session.request(
            method=request.method,
            url=target,
            headers=out_headers,
            allow_redirects=True,
            timeout=(CONNECT_TO, READ_TO),
            proxies=PROXIES if PROXIES else None,
            stream=False,  # baixa completo; simples
        )
    except Exception as e:
        body = jsonify({"error":"upstream_error","detail":str(e)})
        resp = Response(body.get_data(as_text=True), status=502, mimetype="application/json")
        if PROXIES:
            resp.headers["X-Proxy-Static"] = _mask_proxy(PROXY_URL)
        return add_cors(resp)

    # 5) Monta resposta ao cliente (copia headers seguros)
    resp = Response(
        r.content if request.method == "GET" else b"",
        status=r.status_code
    )
    for k, v in r.headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP: 
            continue
        # passe apenas os cabeçalhos úteis/seguros
        if lk in ("content-type","cache-control","etag","last-modified",
                  "content-range","accept-ranges","location","vary","content-length"):
            resp.headers[k] = v

    # Info de debug
    resp.headers["X-Proxy-Final-Url"] = getattr(r, "url", target)
    if PROXIES:
        resp.headers["X-Proxy-Static"] = _mask_proxy(PROXY_URL)

    return add_cors(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8081")))

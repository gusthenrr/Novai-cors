# server_light.py
from flask import Flask, request, Response, jsonify
from flask_cors import CORS
import os
import requests
from urllib.parse import urlparse, unquote, parse_qs
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import http.cookiejar as cookielib

app = Flask(__name__)
CORS(app, supports_credentials=True)

# ===================== Config =====================
ALLOWED_EXACT = {"api.mercadolibre.com"}
ALLOWED_SUFFIXES = (".mercadolivre.com.br", ".mercadolibre.com")

CONNECT_TO = float(os.getenv("LR_CONNECT_TIMEOUT", "3.5"))
READ_TO    = float(os.getenv("LR_READ_TIMEOUT", "10.0"))
POOL_CONN  = int(os.getenv("LR_POOL_CONNECTIONS", "50"))
POOL_MAX   = int(os.getenv("LR_POOL_MAXSIZE", "100"))

# Proxy estático (opcional)
USE_PROXY = os.getenv("LR_USE_PROXY", "0").strip().lower() in ("1","true","on","yes")
PROXY_URL = os.getenv("LR_PROXY_URL", "").strip()
PROXIES   = {"http": PROXY_URL, "https": PROXY_URL} if (USE_PROXY and PROXY_URL) else None

# Flags
FORWARD_AUTH = os.getenv("LR_FORWARD_AUTH", "0").strip().lower() in ("1","true","on","yes")  # OFF por padrão
LOG_REQUESTS = os.getenv("LR_LOG", "0").strip().lower() in ("1","true","on","yes")

# Headers base
DEFAULT_OUT_HEADERS = {
    "User-Agent": os.getenv("LR_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                                             "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
}
# Authorization só se LR_FORWARD_AUTH=1
FORWARD_INBOUND = (
    "Accept","Accept-Language","User-Agent",
    "Range","If-None-Match","If-Modified-Since","Cache-Control","Pragma",
    "Content-Type"
)

HOP_BY_HOP = {
    "connection","keep-alive","proxy-authenticate","proxy-authorization",
    "te","trailers","transfer-encoding","upgrade"
}

# ===================== Session =====================
session = requests.Session()
adapter = HTTPAdapter(pool_connections=POOL_CONN,
                      pool_maxsize=POOL_MAX,
                      max_retries=Retry(total=0))
session.mount("https://", adapter)
session.mount("http://", adapter)
session.trust_env = False  # ignora proxies do sistema

class _NoCookiesPolicy(cookielib.CookiePolicy):
    rfc2965 = False; netscape = False; hide_cookie2 = True
    def set_ok(self, cookie, request): return False
    def return_ok(self, cookie, request): return False
    def domain_return_ok(self, domain, request): return False
    def path_return_ok(self, path, request): return False
session.cookies = cookielib.CookieJar()
session.cookies.set_policy(_NoCookiesPolicy())

# ===================== Helpers =====================
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
    # CORS amplo (igual ao mfy)
    origin = request.headers.get("Origin")
    resp.headers["Access-Control-Allow-Origin"] = origin or "*"
    # credenciais só se ecoar origin, nunca com '*'
    resp.headers["Access-Control-Allow-Credentials"] = "true" if origin else "false"

    # Métodos iguais ao mfy
    resp.headers["Access-Control-Allow-Methods"] = "PUT, GET, POST, DELETE, OPTIONS"

    # Headers amplos (inclui os que seu front costuma enviar)
    resp.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, If-None-Match, If-Modified-Since, "
        "Range, Cache-Control, Pragma"
    )

    # Expose headers úteis + debug
    resp.headers["Access-Control-Expose-Headers"] = (
        "content-type,content-length,connection,date,access-control-max-age,"
        "x-api-server-segment,x-content-type-options,x-request-id,strict-transport-security,"
        "x-frame-options,x-xss-protection,access-control-allow-origin,access-control-allow-headers,"
        "access-control-allow-methods,x-cache,via,x-amz-cf-pop,x-amz-cf-id,"
        "X-Proxy-Final-Url,X-Proxy-Fwd-Query,X-Proxy-Static,X-Proxy-Auth"
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

# ===================== Health & Ping =====================
@app.get("/_health")
def _health():
    return "ok", 200

@app.get("/ping")
def _ping():
    # ajuda a testar CORS/Network do front rapidamente
    j = {
        "ok": True,
        "origin": request.headers.get("Origin"),
        "path": request.path,
        "qs": request.query_string.decode("utf-8"),
    }
    resp = jsonify(j); resp.status_code = 200
    return add_cors(resp)

# ===================== Preflight =====================
@app.route("/", defaults={"raw": ""}, methods=["OPTIONS"])
@app.route("/<path:raw>", methods=["OPTIONS"])
def _opts(raw):
    # responde sempre 204 com CORS amplo
    return add_cors(Response("", status=204))

# ===================== Proxy =====================
@app.route("/", defaults={"raw": ""}, methods=["GET","HEAD"])
@app.route("/<path:raw>", methods=["GET","HEAD"])
def light_proxy(raw: str):
    """
    Uso:
      /https://api.mercadolibre.com/items/MLB123
      /?u=https://api.mercadolibre.com/sites/MLB/search?q=...
    """
    # 1) Resolve target
    qs = ""
    if raw:
        target = unquote(raw)
        # (FIX) reanexa query quando usar path-style
        qs = request.query_string.decode("utf-8")
        if qs:
            target = f"{target}{'&' if '?' in target else '?'}{qs}"
    else:
        target = request.args.get("u","").strip()

    if not target:
        return add_cors(Response("OK - use /https://<url> ou ?u=<url>", status=200))

    # 2) Allowlist
    if not is_allowed(target):
        return add_cors(Response("Host não permitido", status=400))

    # logs opcionais
    if LOG_REQUESTS:
        app.logger.info("[LIGHT] %s %s | origin=%s", request.method, target, request.headers.get("Origin","-"))

    # 3) Headers de saída
    out_headers = dict(DEFAULT_OUT_HEADERS)
    for k in FORWARD_INBOUND:
        v = request.headers.get(k)
        if v: out_headers[k] = v
    if FORWARD_AUTH:
        v = request.headers.get("Authorization")
        if v: 
            out_headers["Authorization"] = v
            auth_fwd = True

    # 4) Upstream
    try:
        r = session.request(
            method=request.method,
            url=target,
            headers=out_headers,
            allow_redirects=True,
            timeout=(CONNECT_TO, READ_TO),
            proxies=PROXIES if PROXIES else None,
            stream=False,
        )
    except Exception as e:
        body = jsonify({"error":"upstream_error","detail":str(e)})
        resp = Response(body.get_data(as_text=True), status=502, mimetype="application/json")
        if PROXIES:
            resp.headers["X-Proxy-Static"] = _mask_proxy(PROXY_URL)
        resp.headers["X-Proxy-Auth"] = "1" if auth_fwd else "0"
        return add_cors(resp)

    # 5) Resposta
    resp = Response(
        r.content if request.method == "GET" else b"",
        status=r.status_code
    )
    for k, v in r.headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP: 
            continue
        if lk in ("content-type","cache-control","etag","last-modified",
                  "content-range","accept-ranges","location","vary","content-length"):
            resp.headers[k] = v

    # Debug headers
    resp.headers["X-Proxy-Final-Url"] = getattr(r, "url", target)
    if raw:
        resp.headers["X-Proxy-Fwd-Query"] = qs
    if PROXIES:
        resp.headers["X-Proxy-Static"] = _mask_proxy(PROXY_URL)

    return add_cors(resp)

# ===================== Main =====================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8081")))



"""2.3: 2-step sign-in, approved devices, app passwords (Subsonic + Jellyfin), security headers, streaming plan."""
import base64, hashlib, hmac, http.cookiejar, json, re, struct, sys, time, urllib.error, urllib.parse, urllib.request
PORT, S = int(sys.argv[1]), sys.argv[2]
BASE = f"http://localhost:{PORT}"
NET = {"X-Forwarded-For": "8.8.4.4"}            # a request from the internet (trusted from localhost, like Cloudflare)
ok = 0
def check(c, what):
    global ok
    if not c:
        print("FAIL:", what); sys.exit(1)
    ok += 1
def op():
    j = http.cookiejar.CookieJar(); o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(j)); o.jar = j; return o
def web(path, body=None, o=None, h=None, raw=False):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **(h or {})}, method="POST" if body is not None else "GET")
    try:
        r = (o or op()).open(req, timeout=60); d = r.read(); return r.status, (r if raw else json.loads(d or b"{}"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
def totp(secret, off=0):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8)); c = int(time.time() // 30) + off
    hm = hmac.new(key, struct.pack(">Q", c), hashlib.sha1).digest(); o = hm[-1] & 15
    return "%06d" % ((struct.unpack(">I", hm[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)
def sub(user, pw, h=None, token=False, ep="ping"):
    q = {"u": user, "v": "1.16.1", "c": "t", "f": "json"}
    if token:
        q.update(t=hashlib.md5((pw + "zz9").encode()).hexdigest(), s="zz9")
    else:
        q["p"] = pw
    req = urllib.request.Request(f"{BASE}/rest/{ep}?" + urllib.parse.urlencode(q), headers=h or {})
    return json.loads(urllib.request.urlopen(req).read())["subsonic-response"]

A = op()
web("/api/setup", {"username": "boss", "password": "bosspass", "name": "Boss"}, A)
c, r = web("/api/users", {"mode": "together", "name": "Jess", "username": "jess", "password": "jesspw1", "preset": "ask"}, A); jid = r["id"]
# ---- headers
c, resp = web("/", None, op(), raw=True)
hd = dict(resp.headers)
check("frame-ancestors 'none'" in hd.get("Content-Security-Policy", "") and hd.get("X-Frame-Options") == "DENY" and hd.get("X-Content-Type-Options") == "nosniff", "security headers")
c, resp = web("/", None, op(), h={"X-Forwarded-Proto": "https"}, raw=True); check("max-age" in resp.headers.get("Strict-Transport-Security", ""), "HSTS over https")
# ---- admin from the internet needs 2-step
c, r = web("/api/login", {"username": "boss", "password": "bosspass"}, op(), NET)
check(c == 401 and "2-step" in r["error"], f"admin from the internet without 2-step refused {r}")
# ---- turn on 2-step
r = web("/api/me/2fa/start", {}, A)[1]; secret = re.search(r"secret=([A-Z2-7]+)", r["uri"]).group(1)
check(r["uri"].startswith("otpauth://totp/Umculo:boss") and len(r["secret"]) > 20, "2-step: key and link")
check("<svg" in r["qr"], "2-step: QR code")
c, _ = web("/api/me/2fa/confirm", {"code": "000000"}, A); check(c == 400, "wrong first code refused")
c, r = web("/api/me/2fa/confirm", {"code": totp(secret)}, A); check(c == 200 and len(r["recovery"]) == 8, "2-step on, 8 recovery codes")
rec = r["recovery"]
check(web("/api/me", None, A)[1]["user"]["two_step"], "shows as on")
# ---- signing in with it
B = op()
c, r = web("/api/login", {"username": "boss", "password": "bosspass"}, B); check(c == 401 and r.get("need_code"), "asks for the code")
c, r = web("/api/login", {"username": "boss", "password": "bosspass", "code": "123456"}, B); check(c == 401 and r.get("need_code"), "wrong code refused")
time.sleep(31 - time.time() % 30 if time.time() % 30 > 22 else 0)
code = totp(secret)
c, r = web("/api/login", {"username": "boss", "password": "bosspass", "code": code, "remember": True}, B); check(c == 200, f"right code signs in {r}")
c, r = web("/api/login", {"username": "boss", "password": "bosspass", "code": code}, op()); check(c == 401, "a code works only once")
c, r = web("/api/login", {"username": "boss", "password": "bosspass"}, B); check(c == 200, "remembered device: no code for 30 days")
c, r = web("/api/login", {"username": "boss", "password": "bosspass", "code": rec[0]}, op()); check(c == 200, "recovery code works")
c, r = web("/api/login", {"username": "boss", "password": "bosspass", "code": rec[0]}, op()); check(c == 401, "recovery code only once")
c, r = web("/api/login", {"username": "boss", "password": "bosspass", "code": totp(secret, 1)}, op(), NET); check(c == 200, f"admin from the internet with 2-step {r}")
# ---- approved devices
c, r = web("/api/security", {"devices": "approved"}, A); check(r["devices"] == "approved", "approved devices on")
c, _ = web("/api/security", {"devices": "open"}, (lambda o: (web("/api/login", {"username": "jess", "password": "jesspw1"}, o), o)[1])(op())); check(c == 403, "only the admin switches it")
J = op()
c, r = web("/api/login", {"username": "jess", "password": "jesspw1"}, J, NET); check(c == 401 and r.get("waiting"), f"new device from the internet waits {r}")
check(any(ck.name == "umc_d" for ck in J.jar), "device remembered in a cookie")
c, r = web("/api/login", {"username": "jess", "password": "jesspw1"}, J, NET); check(c == 401 and r.get("waiting"), "still waiting")
sp = web("/api/security/people", None, A)[1]; jp = next(p for p in sp["people"] if p["id"] == jid)
wd = [d for d in jp["devices"] if d["waiting"]]; check(len(wd) == 1, "you see one device waiting")
web("/api/security/approve", {"user": jid, "id": wd[0]["id"]}, A)
c, r = web("/api/login", {"username": "jess", "password": "jesspw1"}, J, NET); check(c == 200, "approved: she signs in")
c, r = web("/api/me", None, J, NET); check(r["user"]["username"] == "jess", "her session works from the internet")
c, r = web("/api/login", {"username": "jess", "password": "jesspw1"}, op()); check(c == 200, "at home: no approval needed")
web("/api/security/remove", {"user": jid, "id": wd[0]["id"]}, A)
c, r = web("/api/me", None, J, NET); check(r.get("user") is None, f"removed device: signed out on the internet {r}")
# ---- music apps
check(sub("jess", "jesspw1")["status"] == "ok", "app at home: normal password")
r = sub("jess", "jesspw1", NET); check(r["status"] == "failed" and "app password" in r["error"]["message"], f"app on the internet: normal password refused {r}")
c, ap = web("/api/security/apppass", {"user": jid, "name": "Jess iPhone"}, A); apw = ap["password"]
check(sub("jess", apw, NET)["status"] == "ok", "app password from the internet")
check(sub("jess", apw, NET, token=True)["status"] == "ok", "app password, token sign-in")
check(sub("jess", apw)["status"] == "ok", "app password at home too")
c, mine = web("/api/me/apppass", {"name": "Boss phone"}, A); check(sub("boss", mine["password"], NET)["status"] == "ok", "your own app password")
# Jellyfin
AUTH = 'MediaBrowser Client="Finamp", Device="iPhone", DeviceId="t1", Version="1"'
c, r = web("/Users/AuthenticateByName", {"Username": "jess", "Pw": "jesspw1"}, op(), {"X-Emby-Authorization": AUTH}); tok_main = r["AccessToken"]
H = lambda t, net=False: {"X-Emby-Authorization": AUTH + f', Token="{t}"', **(NET if net else {})}
check(web("/Users/Me", None, op(), H(tok_main))[0] == 200, "Finamp signed in at home")
check(web("/Users/Me", None, op(), H(tok_main, True))[0] == 401, "that sign-in doesn't work from the internet")
c, r = web("/Users/AuthenticateByName", {"Username": "jess", "Pw": apw}, op(), {"X-Emby-Authorization": AUTH, **NET}); check(c == 200, "Finamp with the app password, from the internet")
tok_app = r["AccessToken"]; check(web("/Users/Me", None, op(), H(tok_app, True))[0] == 200, "and keeps working")
web("/api/security/remove", {"user": jid, "id": ap["id"]}, A)
check(web("/Users/Me", None, op(), H(tok_app, True))[0] == 401, "removing the app password signs Finamp out")
check(sub("jess", apw, NET)["status"] == "failed", "and the Subsonic app")
# ---- turning off, and lockout still counts
c, r = web("/api/me/2fa/off", {"code": "000000"}, A); check(c == 400, "turning off needs a code")
web("/api/security/2fa-off", {"user": jid}, A)
c, r = web("/api/security", {"devices": "open"}, A); check(r["devices"] == "open", "back to any device")
check(sub("jess", "jesspw1", NET)["status"] == "ok", "open again: normal password from the internet")
print("V23 OK", ok)

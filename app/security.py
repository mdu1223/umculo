"""Security for the public address: 2-step sign-in codes, approved devices and app passwords.

- 2-step sign-in (TOTP, the codes Authenticator apps show): asked at every web sign-in once switched on,
  except on a device you said to remember for 30 days. Recovery codes for a lost phone.
- Approved devices (switch on in People): from the internet, only devices you approved can sign in.
  A new device waits for your OK. Your own devices are approved when your 2-step code is right.
- App passwords: one per phone or app, shown once, removable one at a time. With approved devices on,
  music apps on the internet must use one; your normal password keeps working at home and on Tailscale.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time

core = None
SETTINGS = {"devices": "open", "admin_2fa_internet": True}     # devices: open | approved
REMEMBER_DAYS = 30
ISSUER = "Umculo"


class Step(PermissionError):
    """Sign-in needs one more thing (a code, or the admin's OK). Carries what the page should show."""
    def __init__(self, msg, **data):
        super().__init__(msg)
        self.data = data


def U():
    return core.users


def now():
    return time.time()


def from_internet():
    ip = getattr(U().LOCAL, "ip", "") or ""
    return bool(ip) and not U().private_ip(ip)


def save():
    U().save()


# --------------------------------------------------------------------------- 2-step codes (RFC 6238)
def totp_at(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


def check_code(u, code):
    """A 6-digit code (this 30 s, or the one either side), or one of the recovery codes (each works once)."""
    t = u.get("totp") or {}
    code = "".join(ch for ch in str(code or "") if ch.isalnum()).lower()
    if not t or not code:
        return False
    if len(code) == 6 and code.isdigit():
        secret = U().unkeep(t["secret"])
        c = int(now() // 30)
        for k in (c - 1, c, c + 1):
            if k > t.get("last", 0) and hmac.compare_digest(totp_at(secret, k), code):
                t["last"] = k                        # a code can't be used twice
                save()
                return True
        return False
    h = hashlib.sha256(code.encode()).hexdigest()
    if h in t.get("recovery", []):
        t["recovery"].remove(h)
        save()
        U().log(f"{u['username']} used a recovery code ({len(t['recovery'])} left)")
        return True
    return False


def start_2fa(u):
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    u["totp_pending"] = U().keep(secret)
    save()
    label = f"{ISSUER}:{u['username']}"
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={ISSUER}&algorithm=SHA1&digits=6&period=30"
    svg = ""
    try:
        import segno                                  # the QR code to scan; without it, the link and key still work
        svg = segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff")
    except Exception:  # noqa: BLE001
        pass
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": uri, "qr": svg}


def confirm_2fa(u, code):
    pend = U().unkeep(u.get("totp_pending") or "")
    if not pend:
        raise ValueError("Start again: tap Turn on")
    c = int(now() // 30)
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not any(hmac.compare_digest(totp_at(pend, k), code) for k in (c - 1, c, c + 1)):
        raise ValueError("That code doesn't match. Check the time on your phone and try the newest code.")
    rec = [secrets.token_hex(5) for _ in range(8)]
    u["totp"] = {"secret": U().keep(pend), "last": c - 2, "since": now(),
                 "recovery": [hashlib.sha256(r.encode()).hexdigest() for r in rec]}
    u.pop("totp_pending", None)
    save()
    U().log(f"{u['username']} turned on 2-step sign-in")
    return {"on": True, "recovery": [f"{r[:5]}-{r[5:]}" for r in rec]}


def stop_2fa(u, code, by_admin=False):
    if not by_admin and not check_code(u, code):
        raise ValueError("Type a current code (or a recovery code) to turn it off")
    u.pop("totp", None)
    u.pop("totp_pending", None)
    for d in (u.get("devices") or {}).values():
        d.pop("trust_until", None)
    save()
    U().log(f"2-step sign-in turned off for {u['username']}" + (" by the admin" if by_admin else ""))
    return {"on": False}


# --------------------------------------------------------------------------- devices
def devices(u):
    return u.setdefault("devices", {})


def device_by_token(tok):
    """-> (user, device id, device) for a browser's device cookie."""
    if not tok:
        return None, None, None
    h = hashlib.sha256(tok.encode()).hexdigest()
    with U().LOCK:
        for u in U().USERS.values():
            for did, d in (u.get("devices") or {}).items():
                if d.get("kind") == "web" and d.get("token") == h:
                    return u, did, d
    return None, None, None


def device_name(ua):
    ua = ua or ""
    os_ = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"),
                                ("Mac OS", "Mac"), ("Linux", "Linux")) if k in ua), "Browser")
    br = next((n for k, n in (("Edg/", "Edge"), ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("Firefox/", "Firefox"),
                              ("Safari/", "Safari")) if k in ua), "")
    return f"{os_} {br}".strip()


def web_device(u, tok, ua):
    """The browser this sign-in comes from: known (by its cookie) or new. -> (device id, device, new cookie or None)"""
    owner, did, d = device_by_token(tok)
    if d and owner is u:
        d.update(last=now(), ip=getattr(U().LOCAL, "ip", ""))
        return did, d, None
    prune(u)
    tok = secrets.token_urlsafe(24)
    did = secrets.token_hex(5)
    d = {"kind": "web", "name": device_name(ua), "token": hashlib.sha256(tok.encode()).hexdigest(), "at": now(),
         "last": now(), "ip": getattr(U().LOCAL, "ip", ""), "approved": not from_internet() or SETTINGS["devices"] != "approved",
         "ua": (ua or "")[:160]}
    devices(u)[did] = d
    save()
    return did, d, tok


def prune(u):
    """Forget browsers unused for 60 days, and requests nobody approved within a week (app passwords stay until removed)."""
    t = now()
    for did, d in list(devices(u).items()):
        if d.get("kind") != "web":
            continue
        idle = t - (d.get("last") or d.get("at") or 0)
        if idle > 60 * 86400 or (not d.get("approved") and idle > 7 * 86400):
            devices(u).pop(did, None)


def sign_in_checks(u, code, remember, tok, ua):
    """After the password: 2-step code and approved devices. -> (device id, new device cookie or None)."""
    did, d, newtok = web_device(u, tok, ua)
    admin = U().is_admin(u)
    if admin and from_internet() and SETTINGS["admin_2fa_internet"] and not u.get("totp"):
        raise Step("Admin sign-in from the internet needs 2-step sign-in. Turn it on at home first (your account → 2-step sign-in).",
                   cookie=newtok)
    if u.get("totp") and (d.get("trust_until") or 0) < now():
        if not code:
            raise Step("Type the 6-digit code from your authenticator app", need_code=True, cookie=newtok)
        if not check_code(u, code):
            U().failed_login(u["username"])
            raise Step("That code isn't right", need_code=True, cookie=newtok)
        if remember:
            d["trust_until"] = now() + REMEMBER_DAYS * 86400
    if from_internet() and SETTINGS["devices"] == "approved" and not d.get("approved"):
        if admin and u.get("totp"):
            d["approved"] = True                      # your own device: your 2-step code proves it's you
        else:
            if not d.get("asked"):
                d["asked"] = now()
                try:
                    core.notify(f"Umculo: {u.get('name') or u['username']} wants to sign in on a new device ({d['name']}). "
                                f"Approve it in People.")
                except Exception:  # noqa: BLE001
                    pass
                U().log(f"{u['username']}: new device waiting for approval ({d['name']}, {d['ip']})")
            save()
            raise Step(f"This device needs the admin's OK first. Ask them to approve \"{d['name']}\" in People, then sign in again.",
                       waiting=True, cookie=newtok)
    save()
    return did, newtok


def session_ok(u, sess):
    """A signed-in browser keeps working only while its device is approved (for the public address)."""
    if not from_internet() or SETTINGS["devices"] != "approved":
        return True
    d = (u.get("devices") or {}).get(sess.get("dev") or "")
    return bool(d and d.get("approved"))


# --------------------------------------------------------------------------- app passwords
def new_app_password(u, name):
    pw = "-".join(secrets.token_hex(2) + secrets.token_hex(1)[:1] for _ in range(4))
    did = secrets.token_hex(5)
    devices(u)[did] = {"kind": "app", "name": (name or "Music app").strip()[:40], "pw": U().keep(pw), "at": now(),
                       "last": None, "approved": True}
    save()
    U().log(f"app password made for {u['username']}: {devices(u)[did]['name']}")
    return {"id": did, "password": pw}


def app_secrets(u):
    return [(did, U().unkeep(d["pw"])) for did, d in (u.get("devices") or {}).items()
            if d.get("kind") == "app" and d.get("approved") and d.get("pw")]


def main_password_allowed():
    """Music apps may use the normal password, except from the internet when only approved devices may."""
    return not (from_internet() and SETTINGS["devices"] == "approved")


def used(u, did):
    d = (u.get("devices") or {}).get(did)
    if d and now() - (d.get("last") or 0) > 300:
        d["last"] = now()
        d["ip"] = getattr(U().LOCAL, "ip", "")


# --------------------------------------------------------------------------- views and changes
def view_devices(u):
    out = []
    for did, d in sorted((u.get("devices") or {}).items(), key=lambda kv: -(kv[1].get("last") or kv[1].get("at") or 0)):
        out.append({"id": did, "kind": d.get("kind"), "name": d.get("name"), "approved": bool(d.get("approved")),
                    "at": d.get("at"), "last": d.get("last"), "ip": d.get("ip", ""),
                    "remembered": (d.get("trust_until") or 0) > now(), "waiting": not d.get("approved")})
    return out


def view(u):
    t = u.get("totp") or {}
    return {"on": bool(t), "since": t.get("since"), "recovery_left": len(t.get("recovery", [])), "devices": view_devices(u),
            "settings": dict(SETTINGS)}


def handle_post(path, b):
    me = U().current()
    if not me or me.get("open"):
        raise PermissionError("Sign in first")
    if path == "/api/me/2fa/start":
        return start_2fa(me)
    if path == "/api/me/2fa/confirm":
        return confirm_2fa(me, b.get("code"))
    if path == "/api/me/2fa/off":
        return stop_2fa(me, b.get("code"))
    if path == "/api/me/apppass":
        return new_app_password(me, b.get("name"))
    if path == "/api/me/devices/remove":
        devices(me).pop(str(b.get("id")), None)
        save()
        return {"ok": True}
    if path.startswith("/api/security"):
        if not U().is_admin(me):
            raise PermissionError("Only the admin can do that")
        if path == "/api/security":
            if b.get("devices") in ("open", "approved"):
                SETTINGS["devices"] = b["devices"]
                if b["devices"] == "approved":           # what already works keeps working: approve the devices in use
                    with U().LOCK:
                        for p in U().USERS.values():
                            for d in (p.get("devices") or {}).values():
                                if d.get("kind") == "web" and d.get("last") and not d.get("asked"):
                                    d["approved"] = True
            save()
            U().log(f"public address: {'only approved devices' if SETTINGS['devices'] == 'approved' else 'any device with a password'}")
            return dict(SETTINGS)
        p = U().USERS.get(str(b.get("user")))
        if not p:
            raise LookupError("No such person")
        if path == "/api/security/approve":
            d = devices(p).get(str(b.get("id")))
            if not d:
                raise LookupError("No such device")
            d["approved"] = True
            save()
            U().log(f"approved {p['username']}'s device {d.get('name')}")
            return {"ok": True}
        if path == "/api/security/remove":
            devices(p).pop(str(b.get("id")), None)
            save()
            return {"ok": True}
        if path == "/api/security/apppass":
            return new_app_password(p, b.get("name"))
        if path == "/api/security/2fa-off":
            return stop_2fa(p, None, by_admin=True)
    return None


def handle_get(path, qs):
    me = U().current()
    if path == "/api/me/security":
        if not me or me.get("open"):
            raise PermissionError("Sign in first")
        return view(me)
    if path == "/api/security/people":
        if not U().is_admin(me):
            raise PermissionError("Only the admin can see this")
        with U().LOCK:
            people = list(U().USERS.values())
        return {"settings": dict(SETTINGS), "people": [{"id": p["id"], "name": p.get("name") or p["username"],
                                                        "two_step": bool(p.get("totp")), "devices": view_devices(p)} for p in people]}
    return None


def init(umculo):
    global core
    core = umculo

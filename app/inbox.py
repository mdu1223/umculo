"""The inbox: drop music you bought (Traxsource, Beatport, Bandcamp, Juno...) into one folder and it's filed.

  Music/_inbox/Banomona.wav               -> Music/Umculo/LebtoniQ/... with the library's tags and cover
  Music/_inbox/Some EP (Bandcamp).zip     -> unpacked, every song filed, the zip set aside
Files are taken once they've stopped growing (a download still in progress is left alone).
Anything that can't be filed goes to _inbox/.problems with a note why. Nothing is ever deleted.

GET /api/inbox   -> {"folder", "waiting", "filed": [...last ones], "problems": [...]}
"""
import os
import shutil
import threading
import time
import uuid
import zipfile

core = None
AUDIO = {"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "aiff", "aif", "alac", "wma"}
STATE = {"filed": [], "problems": [], "sizes": {}}
LOCK = threading.Lock()


def folder():
    return os.environ.get("INBOX_DIR") or os.path.join(core.CONFIG["music"].rstrip("/"), "_inbox")


def aside(path, sub, why=""):
    d = os.path.join(folder(), sub)
    os.makedirs(d, exist_ok=True)
    dest = core.mixtidy.free(os.path.join(d, os.path.basename(path)))
    shutil.move(path, dest)
    if why:
        with open(dest + ".txt", "w") as fh:
            fh.write(why + "\n")
    return dest


def settled(path):
    """True once a file has kept the same size for a whole pass (and is a minute old)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    was = STATE["sizes"].get(path)
    STATE["sizes"][path] = st.st_size
    return was == st.st_size and time.time() - st.st_mtime > 30


def unzip(path):
    out = os.path.join(folder(), os.path.splitext(os.path.basename(path))[0])
    out = core.mixtidy.free(out)
    with zipfile.ZipFile(path) as z:
        for m in z.infolist():
            name = os.path.normpath(m.filename)
            if name.startswith("..") or os.path.isabs(name) or m.is_dir():
                continue
            if name.rsplit(".", 1)[-1].lower() not in AUDIO | {"jpg", "jpeg", "png"}:
                continue
            dest = os.path.join(out, name)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(m) as src, open(dest, "wb") as fh:
                shutil.copyfileobj(src, fh)
    aside(path, ".unpacked")
    return out


def file_one(path):
    req = {"id": "i" + uuid.uuid4().hex[:11], "target": core.make_target("", "", "", 0, "", ""),
           "candidate": {"source": "inbox", "name": os.path.basename(path)}, "user": None, "stop": threading.Event()}
    final = core.file_into_library(path, req)
    t = req["target"]
    with LOCK:
        STATE["filed"] = (STATE["filed"] + [{"artist": t.get("artist"), "title": t.get("title"), "at": time.time(), "file": final}])[-50:]
    return final


def tick():
    root = folder()
    if not os.path.isdir(root):
        os.makedirs(root, exist_ok=True)
        return 0
    done = 0
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for n in names:
            p = os.path.join(d, n)
            ext = n.rsplit(".", 1)[-1].lower()
            if n.startswith(".") or not settled(p):
                continue
            try:
                if ext == "zip":
                    unzip(p)
                elif ext in AUDIO:
                    file_one(p)
                    done += 1
                STATE["sizes"].pop(p, None)
            except Exception as ex:  # noqa: BLE001
                core.log("inbox: couldn't file", n, ex)
                with LOCK:
                    STATE["problems"] = (STATE["problems"] + [{"file": n, "why": str(ex)[:200], "at": time.time()}])[-50:]
                try:
                    aside(p, ".problems", str(ex))
                except OSError:
                    pass
    for d, _dirs, _names in sorted(os.walk(root), key=lambda x: -len(x[0])):      # empty folders left by zips go
        if d != root and not os.path.basename(d).startswith(".") and not os.listdir(d):
            os.rmdir(d)
    if done:
        core.log(f"inbox: filed {done} song{'s' if done != 1 else ''}")
        core.request_scan()
    return done


def loop():
    time.sleep(20)
    while True:
        try:
            tick()
        except Exception as ex:  # noqa: BLE001
            core.log("inbox:", ex)
        time.sleep(int(os.environ.get("INBOX_EVERY") or 30))


def handle_get(path, _qs):
    if path != "/api/inbox":
        return None
    root = folder()
    waiting = 0
    for d, dirs, names in os.walk(root) if os.path.isdir(root) else []:
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        waiting += sum(1 for n in names if n.rsplit(".", 1)[-1].lower() in AUDIO | {"zip"})
    return {"folder": root, "waiting": waiting, "filed": STATE["filed"][-20:][::-1], "problems": STATE["problems"][-10:][::-1]}


def init(umculo):
    global core
    core = umculo
    threading.Thread(target=loop, daemon=True).start()

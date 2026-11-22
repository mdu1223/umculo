"""The web page's script: no function or top-level name defined twice (the later one silently wins)."""
import re, sys
s = open(sys.argv[1] if len(sys.argv) > 1 else "app/static/index.html").read()
names = re.findall(r"^(?:async )?function ([A-Za-z0-9_$]+)", s, re.M) + re.findall(r"^(?:const|let) ([A-Za-z0-9_$]+)", s, re.M)
dup = sorted({n for n in names if names.count(n) > 1})
print("PAGE OK" if not dup else "PAGE DUPLICATES: " + ", ".join(dup))
sys.exit(1 if dup else 0)

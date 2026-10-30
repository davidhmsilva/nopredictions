import subprocess, sys, concurrent.futures as cf, os
D=os.getcwd()
ws=[l.split()[0] for l in open("f2.txt") if l.strip()]
def run(w):
    out=f"{D}/an_{w}.json"
    if os.path.exists(out): return w,0
    p=subprocess.run(["../ingest/.venv/bin/python","wallet_analyzer.py",w,"--json",out],cwd=os.path.expanduser("~/agente/agent"),capture_output=True,text=True,timeout=1500)
    open(f"{D}/an_{w}.log","w").write(p.stdout+p.stderr); return w,p.returncode
with cf.ThreadPoolExecutor(4) as ex:
    for w,rc in ex.map(run,ws): print(w,rc,flush=True)

import requests, json, time, concurrent.futures as cf, warnings
warnings.filterwarnings("ignore")
S=requests.Session()
def get(params):
    for i in range(5):
        try:
            r=S.get("https://data-api.polymarket.com/positions",params=params,timeout=40)
            if r.status_code==200: return r.json()
            if r.status_code==429: time.sleep(2+2*i); continue
        except Exception: pass
        time.sleep(1+i)
    return None
def pull(w):
    out=[]; ok=True
    for pg in range(20):
        b=get(dict(user=w,limit=500,offset=pg*500,sizeThreshold=0))
        if b is None: ok=False; break
        out+=b
        if len(b)<500: break
    else: ok=False
    return w,out,ok
ws=[o["w"] for o in json.load(open("scored.json"))]
res={}
with cf.ThreadPoolExecutor(8) as ex:
    for w,rows,ok in ex.map(pull,ws): res[w]={"rows":rows,"complete":ok}
json.dump(res,open("open.json","w"))
print(len(res), sum(len(v["rows"]) for v in res.values()), sum(not v["complete"] for v in res.values()))

import requests, json, time, random, concurrent.futures as cf, warnings
warnings.filterwarnings("ignore")
S=requests.Session()
def get(params):
    for i in range(5):
        try:
            r=S.get("https://data-api.polymarket.com/closed-positions",params=params,timeout=30)
            if r.status_code==200: return r.json()
            if r.status_code==429: time.sleep(2+i*2); continue
        except Exception: pass
        time.sleep(1+i)
    return None
def pull(w, maxpages=12):
    out=[]
    for pg in range(maxpages):
        b=get(dict(user=w,limit=50,offset=pg*50,sortBy="TIMESTAMP",sortDirection="DESC"))
        if not b: break
        out+=b
        if len(b)<50: break
    return w,out
c=json.load(open("stage1.json"))
res={}
with cf.ThreadPoolExecutor(8) as ex:
    for w,rows in ex.map(pull,[x[0] for x in c]):
        res[w]=rows
json.dump(res,open("closed.json","w"))
print(len(res), sum(len(v) for v in res.values()))

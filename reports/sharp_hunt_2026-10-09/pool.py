import requests, json, time, concurrent.futures as cf
S=requests.Session()
U="https://data-api.polymarket.com/v1/leaderboard"
def page(tp,ob,off):
    for i in range(4):
        try:
            r=S.get(U,params=dict(category="SPORTS",timePeriod=tp,orderBy=ob,limit=50,offset=off),timeout=30)
            if r.status_code==200: return r.json()
        except Exception: pass
        time.sleep(1+i)
    return []
pool={}
jobs=[(tp,"PNL",off) for tp,n in (("ALL",3000),("MONTH",1500),("WEEK",500)) for off in range(0,n,50)]
with cf.ThreadPoolExecutor(6) as ex:
    for (tp,ob,off),rows in zip(jobs,ex.map(lambda j:page(*j),jobs)):
        for r in rows or []:
            w=r["proxyWallet"].lower(); d=pool.setdefault(w,{"name":r.get("userName"),})
            d[tp]={"pnl":r["pnl"],"vol":r["vol"],"rank":int(r["rank"])}
json.dump(pool,open("pool.json","w"))
print(len(pool), {tp:sum(tp in d for d in pool.values()) for tp in ("ALL","MONTH","WEEK")})

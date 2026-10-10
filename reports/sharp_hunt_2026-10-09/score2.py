import json, random, collections
c={x[0]:x for x in json.load(open("stage1.json"))}
CL=json.load(open("closed.json")); OP=json.load(open("open.json"))
def boot(vals,f,n=2000,seed=7):
    r=random.Random(seed);k=len(vals);xs=sorted(f([vals[r.randrange(k)] for _ in range(k)]) for _ in range(n))
    return xs[int(.025*n)],xs[int(.975*n)]
out=[]
for w,o in OP.items():
    cl=CL.get(w) or []
    capped=len(cl)>=600
    start=min((p.get("endDate") or "9") for p in cl) if capped else ""
    pos=[]
    for p in cl:
        pos.append((p.get("eventSlug") or p["conditionId"],p["realizedPnl"],(p["totalBought"] or 0)*(p["avgPrice"] or 0),p["totalBought"] or 0,p.get("slug",""),p["avgPrice"] or 0,p.get("endDate") or ""))
    n_open_res=0;unres=0
    for p in o["rows"]:
        cp=p.get("curPrice")
        if not p.get("redeemable") or cp not in (0,1):
            unres+=1; continue
        if (p.get("endDate") or "") < start: continue
        n_open_res+=1
        pnl=(p.get("cashPnl") or 0)+(p.get("realizedPnl") or 0)
        pos.append((p.get("eventSlug") or p["conditionId"],pnl,(p["totalBought"] or 0)*(p["avgPrice"] or 0),p["totalBought"] or 0,p.get("slug",""),p["avgPrice"] or 0,p.get("endDate") or ""))
    ev=collections.defaultdict(lambda:[0.,0.,0.]); sp=collections.Counter()
    for e,pnl,cost,sh,slug,ap,ed in pos:
        if sh<=0 or cost<=0: continue
        v=ev[e]; v[0]+=pnl; v[1]+=cost; v[2]+=sh; sp[slug.split("-")[0]]+=cost
    E=list(ev.values())
    if len(E)<100: continue
    mean=lambda s:sum(s)/len(s); roi=lambda s:sum(v[0] for v in s)/sum(v[1] for v in s)
    pps=[v[0]/v[2] for v in E]
    lo,hi=boot(pps,mean); rlo,rhi=boot(E,roi)
    tot=sum(v[0] for v in E); top5=sum(sorted((v[0] for v in E),reverse=True)[:5]); totc=sum(v[1] for v in E)
    x=c[w]
    eds=sorted(p[6] for p in pos if p[6])
    out.append(dict(w=w,name=x[1],lb_pnl=x[2],lb_vol=x[3],m_pnl=x[5],events=len(E),closed=len(cl),open_res=n_open_res,unres=unres,complete=o["complete"],
      edge_pp=mean(pps)*100,edge_ci=(lo*100,hi*100),roi=roi(E)*100,roi_ci=(rlo*100,rhi*100),pnl=tot,cost=totc,
      top5_share=(top5/tot if tot>0 else None),window=(eds[0] if eds else None, eds[-1] if eds else None),
      sports=[(k,round(v/totc*100)) for k,v in sp.most_common(4)]))
json.dump(out,open("scored2.json","w"))
P=[o for o in out if o["edge_ci"][0]>0 and o["roi_ci"][0]>0]
print("scored",len(out),"both CIs clear",len(P))
P.sort(key=lambda o:-(o["roi_ci"][0]))
for o in P[:45]:
    print(f'{o["name"][:20]:20} ev={o["events"]:4} cl={o["closed"]} op={o["open_res"]} edge={o["edge_pp"]:+.1f}pp[{o["edge_ci"][0]:+.1f},{o["edge_ci"][1]:+.1f}] roi={o["roi"]:+.1f}%[{o["roi_ci"][0]:+.1f},{o["roi_ci"][1]:+.1f}] pnl=${o["pnl"]/1e3:.0f}k cost=${o["cost"]/1e3:.0f}k top5={o["top5_share"] and round(o["top5_share"],2)} {o["window"][0]}..{o["window"][1]} {o["sports"]}')

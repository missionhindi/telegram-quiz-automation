# Repair mode for the current Kriya topic:
# Q52-Q75 have already been sent once.
# This script sends ONLY Q1-Q51 and then marks the topic complete.
import csv, json, os, time, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

BASE=Path(__file__).resolve().parent
CSV=BASE/"kriya_questions.csv"
STATE=BASE/"progress.json"
IST=ZoneInfo("Asia/Kolkata")

def load(p,d):
    if not p.exists(): return d
    with p.open(encoding="utf-8") as f: return json.load(f)

def save(p,d):
    t=p.with_suffix(".tmp")
    with t.open("w",encoding="utf-8") as f: json.dump(d,f,ensure_ascii=False,indent=2)
    t.replace(p)

def exp(s,limit=200):
    s=" ".join(str(s or "").split())
    if len(s)<=limit:return s
    cur=""
    for x in s.replace("।","।|").split("|"):
        x=x.strip()
        if x and len((cur+" "+x).strip())<=limit:cur=(cur+" "+x).strip()
        elif x:break
    return cur or s[:limit-1]+"…"

def api(token,method,data):
    r=requests.post(f"https://api.telegram.org/bot{token}/{method}",data=data,timeout=(10,30))
    return r.json()

def send(token,chat,q):
    opts=[f"(1) {q['Option 1']}",f"(2) {q['Option 2']}",f"(3) {q['Option 3']}",f"(4) {q['Option 4']}"]
    data={"chat_id":chat,"question":f"Q. {q['Question']}","options":json.dumps([{"text":x} for x in opts],ensure_ascii=False),"is_anonymous":True,"type":"quiz","allows_multiple_answers":False,"correct_option_id":int(q["Correct"])-1,"explanation":exp(q["Explanation"])}
    tries=0
    while True:
        r=api(token,"sendPoll",data)
        if r.get("ok"): return
        if r.get("error_code")==429:
            tries+=1
            if tries>8: raise RuntimeError("Too many Telegram rate-limit retries")
            wait=int(r.get("parameters",{}).get("retry_after",5))+1
            print(f"Rate limit: waiting {wait}s...")
            time.sleep(wait)
        else: raise RuntimeError(r.get("description",r))

def main():
    token=os.environ["BOT_TOKEN"]; chat=os.environ["CHAT_ID"]
    with CSV.open(encoding="utf-8-sig",newline="") as f: qs=list(csv.DictReader(f))
    st=load(STATE,{"next_index":0,"batch_no":0,"last_sent_at":None,"skipped_questions":[]})
    i=int(st.get("next_index",0))
    if i>=51:
        st["next_index"]=75
        save(STATE,st)
        print("Q1-Q51 already complete. Q52-Q75 will NOT be sent.")
        return
    api(token,"getMe",{})
    print("REPAIR MODE: ONLY Q1-Q51 will be sent. Q52-Q75 are already sent.")
    while i<51:
        q=qs[i]
        # Telegram limits: skip the whole question if it cannot fit.
        if len(q["Question"])>300 or any(len(f"({n}) {q[f'Option {n}']}")>100 for n in range(1,5)):
            st.setdefault("skipped_questions",[])
            if q["No"] not in st["skipped_questions"]: st["skipped_questions"].append(q["No"])
            print(f"SKIP Q{q['No']} - Telegram limit")
            i+=1; st["next_index"]=i; save(STATE,st); continue
        print(f"Sending Q{q['No']}...")
        send(token,chat,q)
        print(f"Q{q['No']} sent")
        i+=1
        st["next_index"]=i
        st["last_sent_at"]=dt.datetime.now(IST).isoformat()
        save(STATE,st)
        time.sleep(3)
    st["next_index"]=75
    save(STATE,st)
    print("Q1-Q51 complete. Q52-Q75 will NOT be sent again.")

if __name__=="__main__":
    main()

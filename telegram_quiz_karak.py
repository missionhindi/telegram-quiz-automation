import csv, json, os, time, requests
BOT_TOKEN=os.environ['BOT_TOKEN']; CHAT_ID=os.environ['CHAT_ID']
CSV_FILE='karak_abhyas_prashn.csv'; PROGRESS_FILE='progress_karak.json'
QUESTION_LIMIT=300; OPTION_LIMIT=100; EXPLANATION_LIMIT=200; POLL_DELAY=3

def save_progress(i,total):
    tmp=PROGRESS_FILE+'.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump({'next_index':i,'topic':'कारक','total':total},f,ensure_ascii=False,indent=2)
    os.replace(tmp,PROGRESS_FILE)

def load_progress(total):
    try:
        with open(PROGRESS_FILE,encoding='utf-8') as f: i=int(json.load(f).get('next_index',0))
        return max(0,min(i,total))
    except Exception: return 0

def tg(method,payload):
    url=f'https://api.telegram.org/bot{BOT_TOKEN}/{method}'
    while True:
        try: data=requests.post(url,data=payload,timeout=60).json()
        except Exception as e: print('Network error:',e); time.sleep(5); continue
        if data.get('ok'): return data['result']
        retry=(data.get('parameters') or {}).get('retry_after')
        if retry:
            wait=int(retry)+2; print(f'Telegram rate limit. Waiting {wait}s...'); time.sleep(wait); continue
        raise RuntimeError(data)

def clean_option(s):
    s=str(s).strip()
    for m in ('(अ)','(ब)','(स)','(द)','(1)','(2)','(3)','(4)'):
        if s.startswith(m): return s[len(m):].strip()
    return s

def clean_embedded(s):
    return s.replace('(अ)','(1)').replace('(ब)','(2)').replace('(स)','(3)').replace('(द)','(4)')

def main():
    with open(CSV_FILE,encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    total=len(rows); start=load_progress(total)
    print('Topic: कारक'); print('Total questions in CSV:',total); print('Starting from question index:',start)
    sent=skipped=0
    for idx in range(start,total):
        r=rows[idx]; no=r['Q_No'].strip(); q=r['Question'].strip()
        opts=[clean_embedded(clean_option(r[f'Option_{i}'])) for i in range(1,5)]
        if len(q)>QUESTION_LIMIT:
            print(f'SKIPPED Q{no}: question too long ({len(q)} chars)'); skipped+=1; save_progress(idx+1,total); continue
        if any(len(x)>OPTION_LIMIT for x in opts):
            print(f'SKIPPED Q{no}: option too long {[len(x) for x in opts]}'); skipped+=1; save_progress(idx+1,total); continue
        exp=r.get('Explanation','').strip()[:EXPLANATION_LIMIT]
        correct=int(r['Correct_Option'].strip())-1
        final_opts=[f'({i}) {x}' for i,x in enumerate(opts,1)]
        tg('sendPoll',{'chat_id':CHAT_ID,'question':f'Q. {q}','options':json.dumps(final_opts,ensure_ascii=False),'type':'quiz','correct_option_id':correct,'is_anonymous':'true','explanation':exp,'allows_multiple_answers':'false'})
        sent+=1; print(f'Q{no} sent'); save_progress(idx+1,total)
        if idx<total-1: time.sleep(POLL_DELAY)
    print('Questions sent:',sent); print('Questions skipped:',skipped); print('Next question index:',total); print('TOPIC COMPLETE')
if __name__=='__main__': main()

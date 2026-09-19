import csv
import json
import os
import time
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
CSV_FILE = BASE_DIR / "sarvnaam_questions.csv"
STATE_FILE = BASE_DIR / "progress.json"
IST = ZoneInfo("Asia/Kolkata")
TELEGRAM_TIMEOUT = (5, 10)

def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def load_questions():
    questions = []
    if not CSV_FILE.exists():
        raise RuntimeError(f"CSV file नहीं मिली: {CSV_FILE}")
    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            questions.append(row)
    if not questions:
        raise RuntimeError("CSV में कोई question नहीं मिला।")
    required = {"No","Question","Option 1","Option 2","Option 3","Option 4","Correct","Explanation"}
    missing = required - set(questions[0].keys())
    if missing:
        raise RuntimeError("CSV में columns missing हैं: " + ", ".join(sorted(missing)))
    for q in questions:
        if q["Correct"] not in {"1","2","3","4"}:
            raise ValueError(f"Q{q['No']}: Correct answer 1-4 होना चाहिए।")
    return questions

def compact_explanation(text, max_chars=200):
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    parts = []
    current = ""
    for part in text.replace("।", "।|").split("|"):
        part = part.strip()
        if not part:
            continue
        candidate = (current + " " + part).strip()
        if len(candidate) <= max_chars:
            current = candidate
            parts.append(part)
        else:
            break
    if current:
        return current
    return text[:max_chars-1].rstrip() + "…"

def telegram_request(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        response = requests.post(url, data=payload or {}, timeout=TELEGRAM_TIMEOUT)
    except requests.Timeout as e:
        raise RuntimeError(f"Telegram API timeout: {method}") from e
    except requests.RequestException as e:
        raise RuntimeError(f"Telegram API connection error: {e}") from e
    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(f"Telegram API ने valid JSON नहीं दिया। HTTP {response.status_code}") from e
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data.get('description', data)}")
    return data

def check_bot(token):
    data = telegram_request(token, "getMe")
    username = data["result"].get("username", "unknown")
    print(f"Telegram bot connected: @{username}")

def validate_question(q):
    question = str(q["Question"]).strip()
    rendered_options = [
        f"(1) {str(q['Option 1']).strip()}",
        f"(2) {str(q['Option 2']).strip()}",
        f"(3) {str(q['Option 3']).strip()}",
        f"(4) {str(q['Option 4']).strip()}",
    ]
    # Telegram limits: question <= 300, each poll option <= 100.
    if len(question) > 300:
        return False, f"question {len(question)} chars है (limit 300)"
    for i, option in enumerate(rendered_options, 1):
        if len(option) > 100:
            return False, f"option {i} {len(option)} chars है (limit 100)"
    return True, ""

def send_poll(token, chat_id, question, options, correct_index, explanation):
    payload = {
        "chat_id": chat_id,
        "question": question,
        "options": json.dumps([{"text": x} for x in options], ensure_ascii=False),
        "is_anonymous": True,
        "type": "quiz",
        "allows_multiple_answers": False,
        "correct_option_id": correct_index,
        "explanation": compact_explanation(explanation),
        "protect_content": False,
    }
    telegram_request(token, "sendPoll", payload)

def send_all_questions(config, questions, state):
    token = os.environ.get("BOT_TOKEN") or config.get("bot_token")
    chat_id = os.environ.get("CHAT_ID") or config.get("chat_id")
    if not token:
        raise RuntimeError("BOT_TOKEN नहीं मिला।")
    if not chat_id:
        raise RuntimeError("CHAT_ID नहीं मिला।")

    check_bot(token)
    print(f"Total questions: {len(questions)}")
    print(f"Starting index: {state['next_index']}")

    i = state["next_index"]
    sent = 0
    skipped = 0

    while i < len(questions):
        q = questions[i]
        valid, reason = validate_question(q)

        if not valid:
            print(f"⏭️ Skipping Q{q['No']} - {reason}")
            state.setdefault("skipped_questions", [])
            if q["No"] not in state["skipped_questions"]:
                state["skipped_questions"].append(q["No"])
            i += 1
            state["next_index"] = i
            save_json(STATE_FILE, state)
            skipped += 1
            continue

        poll_question = f"Q. {q['Question']}"
        options = [
            f"(1) {q['Option 1']}",
            f"(2) {q['Option 2']}",
            f"(3) {q['Option 3']}",
            f"(4) {q['Option 4']}",
        ]
        correct_index = int(q["Correct"]) - 1

        print(f"Sending Q{q['No']} ...")
        send_poll(
            token=token,
            chat_id=chat_id,
            question=poll_question,
            options=options,
            correct_index=correct_index,
            explanation=q["Explanation"],
        )
        print(f"  ✓ Q{q['No']} sent")

        sent += 1
        i += 1

        # Save immediately so a later failure never repeats sent questions.
        state["next_index"] = i
        state["last_sent_at"] = dt.datetime.now(IST).isoformat()
        save_json(STATE_FILE, state)
        time.sleep(0.5)

    state["batch_no"] = state.get("batch_no", 0) + 1
    save_json(STATE_FILE, state)

    print("================================")
    print("ALL QUESTIONS COMPLETE")
    print(f"Questions sent: {sent}")
    print(f"Questions skipped: {skipped}")
    print(f"Next question index: {state['next_index']}")
    print("Progress saved.")
    print("================================")

def seconds_until_9pm():
    now = dt.datetime.now(IST)
    target = now.replace(hour=21, minute=0, second=0, microsecond=0)
    if now >= target:
        target += dt.timedelta(days=1)
    return max(0, int((target - now).total_seconds()))

def main():
    print("Telegram Quiz Scheduler - सर्वनाम")
    config = load_json(CONFIG_FILE, {})
    questions = load_questions()
    state = load_json(
        STATE_FILE,
        {"next_index": 0, "batch_no": 0, "last_sent_at": None, "skipped_questions": []}
    )

    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("Running inside GitHub Actions.")
        if state["next_index"] >= len(questions):
            print("सभी questions पहले ही post हो चुके हैं।")
            return
        send_all_questions(config, questions, state)
        return

    print("Daily schedule: 9:00 PM IST")
    if config.get("run_now", False):
        send_all_questions(config, questions, state)
        return

    while True:
        if state["next_index"] >= len(questions):
            print("सभी questions post हो चुके हैं।")
            break
        wait = seconds_until_9pm()
        print(f"Next run in {wait // 3600}h {(wait % 3600) // 60}m.")
        time.sleep(wait)
        send_all_questions(config, questions, state)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram stopped.")
    except Exception as e:
        print("ERROR")
        print(str(e))
        raise

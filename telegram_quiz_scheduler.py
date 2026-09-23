import csv
import json
import os
import time
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = BASE_DIR / "visheshan_questions.csv"
STATE_FILE = BASE_DIR / "progress.json"
CONFIG_FILE = BASE_DIR / "config.json"

IST = ZoneInfo("Asia/Kolkata")

TELEGRAM_TIMEOUT = (10, 30)
DELAY_BETWEEN_POLLS = 3.0
MAX_RETRIES = 12


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
    if not CSV_FILE.exists():
        raise RuntimeError(f"CSV file नहीं मिली: {CSV_FILE}")

    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        questions = list(csv.DictReader(f))

    if not questions:
        raise RuntimeError("CSV में कोई question नहीं मिला।")

    required = {
        "No", "Question", "Option 1", "Option 2",
        "Option 3", "Option 4", "Correct", "Explanation"
    }

    missing = required - set(questions[0].keys())
    if missing:
        raise RuntimeError(
            "CSV में columns missing हैं: " + ", ".join(sorted(missing))
        )

    for q in questions:
        if q["Correct"] not in {"1", "2", "3", "4"}:
            raise ValueError(f"Q{q['No']}: Correct answer invalid है।")

    return questions


def validate_question(q):
    question = str(q["Question"]).strip()
    options = [
        str(q["Option 1"]).strip(),
        str(q["Option 2"]).strip(),
        str(q["Option 3"]).strip(),
        str(q["Option 4"]).strip(),
    ]

    if len(question) > 300:
        return False, "question 300 characters से ज्यादा है"

    rendered = [
        f"(1) {options[0]}",
        f"(2) {options[1]}",
        f"(3) {options[2]}",
        f"(4) {options[3]}",
    ]

    for n, option in enumerate(rendered, start=1):
        if len(option) > 100:
            return False, f"option {n} 100 characters से ज्यादा है"

    explanation = str(q["Explanation"]).strip()
    if len(explanation) > 200:
        # Explanation को छोटा नहीं करेंगे; question valid रहेगा,
        # लेकिन Telegram limit के लिए explanation को sentence boundary
        # पर trim किया जाएगा।
        return True, "explanation लंबा है"

    return True, ""


def telegram_request(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                url,
                data=payload or {},
                timeout=TELEGRAM_TIMEOUT,
            )
        except requests.Timeout:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Telegram API timeout: {method}")
            wait = min(10 * attempt, 60)
            print(f"Timeout. {wait}s बाद retry...")
            time.sleep(wait)
            continue
        except requests.RequestException as e:
            if attempt == MAX_RETRIES:
                raise RuntimeError(f"Telegram connection error: {e}")
            wait = min(10 * attempt, 60)
            print(f"Connection error. {wait}s बाद retry...")
            time.sleep(wait)
            continue

        try:
            data = response.json()
        except ValueError as e:
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"Telegram API ने valid JSON नहीं दिया। HTTP {response.status_code}"
                ) from e
            time.sleep(min(10 * attempt, 60))
            continue

        if data.get("ok"):
            return data

        description = str(data.get("description", data))

        if response.status_code == 429 or "Too Many Requests" in description:
            retry_after = 30
            try:
                retry_after = int(data.get("parameters", {}).get("retry_after", 30))
            except (TypeError, ValueError):
                pass

            wait = max(retry_after + 2, 3)
            print(
                f"Telegram rate limit मिला. "
                f"{wait} सेकंड wait करके उसी request को retry करेंगे..."
            )
            time.sleep(wait)
            continue

        raise RuntimeError(f"Telegram API error: {description}")

    raise RuntimeError(f"Telegram API retry limit exceeded: {method}")


def compact_explanation(text, max_chars=200):
    text = " ".join(str(text).split())

    if len(text) <= max_chars:
        return text

    # Explanation को sentence boundary पर जितना संभव हो उतना रखें।
    pieces = text.replace("।", "।|").split("|")
    current = ""

    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue

        candidate = (current + " " + piece).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            break

    if current:
        return current

    return text[: max_chars - 1].rstrip() + "…"


def send_poll(token, chat_id, q):
    options = [
        f"(1) {q['Option 1'].strip()}",
        f"(2) {q['Option 2'].strip()}",
        f"(3) {q['Option 3'].strip()}",
        f"(4) {q['Option 4'].strip()}",
    ]

    payload = {
        "chat_id": chat_id,
        "question": f"Q. {q['Question'].strip()}",
        "options": json.dumps(
            [{"text": x} for x in options],
            ensure_ascii=False,
        ),
        "is_anonymous": True,
        "type": "quiz",
        "allows_multiple_answers": False,
        "correct_option_id": int(q["Correct"]) - 1,
        "explanation": compact_explanation(q["Explanation"]),
        "protect_content": False,
    }

    telegram_request(token, "sendPoll", payload)


def check_bot(token):
    data = telegram_request(token, "getMe")
    username = data["result"].get("username", "unknown")
    print(f"Telegram bot connected: @{username}")


def send_all_questions(config, questions, state):
    token = os.environ.get("BOT_TOKEN") or config.get("bot_token")
    chat_id = os.environ.get("CHAT_ID") or config.get("chat_id")

    if not token:
        raise RuntimeError("BOT_TOKEN नहीं मिला।")

    if not chat_id:
        raise RuntimeError("CHAT_ID नहीं मिला।")

    check_bot(token)

    if state["next_index"] >= len(questions):
        print("सभी questions पहले ही post हो चुके हैं।")
        return

    skipped_this_run = 0
    sent_this_run = 0
    i = state["next_index"]

    print(f"Total questions in CSV: {len(questions)}")
    print(f"Starting from question index: {i}")

    while i < len(questions):
        q = questions[i]
        valid, reason = validate_question(q)

        if not valid:
            print(f"SKIP Q{q['No']} - {reason}")

            if q["No"] not in state["skipped_questions"]:
                state["skipped_questions"].append(q["No"])

            skipped_this_run += 1
            i += 1
            state["next_index"] = i
            save_json(STATE_FILE, state)
            continue

        print(f"Sending Q{q['No']} ...")

        # 429 आने पर telegram_request खुद retry करेगा।
        send_poll(token, chat_id, q)

        sent_this_run += 1
        i += 1

        state["next_index"] = i
        state["last_sent_at"] = dt.datetime.now(IST).isoformat()
        save_json(STATE_FILE, state)

        print(f"✓ Q{q['No']} sent")
        print(f"Progress: {i}/{len(questions)}")

        if i < len(questions):
            time.sleep(DELAY_BETWEEN_POLLS)

    state["batch_no"] += 1
    state["next_index"] = len(questions)
    save_json(STATE_FILE, state)

    print("================================")
    print("TOPIC COMPLETE")
    print(f"Questions sent: {sent_this_run}")
    print(f"Questions skipped: {skipped_this_run}")
    print(f"Total skipped so far: {len(state['skipped_questions'])}")
    print("All valid questions for this topic are complete.")
    print("Progress saved.")
    print("================================")


def main():
    print("Telegram Quiz Scheduler - विशेषण")

    config = load_json(CONFIG_FILE, {})

    questions = load_questions()

    state = load_json(
        STATE_FILE,
        {
            "next_index": 0,
            "batch_no": 0,
            "last_sent_at": None,
            "skipped_questions": [],
        },
    )

    # GitHub Actions में एक run = पूरा topic।
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

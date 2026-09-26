import csv
import json
import os
import time
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = BASE_DIR / "kriya_questions.csv"
STATE_FILE = BASE_DIR / "progress.json"
IST = ZoneInfo("Asia/Kolkata")

TELEGRAM_TIMEOUT = (10, 30)
POLL_DELAY = 3.0
MAX_RETRIES_429 = 8


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

    return questions


def compact_explanation(text, max_chars=200):
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text

    current = ""
    for part in text.replace("।", "।|").split("|"):
        part = part.strip()
        if not part:
            continue
        candidate = (current + " " + part).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            break

    if current:
        return current

    return text[:max_chars - 1].rstrip() + "…"


def validate_question(q):
    question = str(q["Question"]).strip()
    options = [
        str(q["Option 1"]).strip(),
        str(q["Option 2"]).strip(),
        str(q["Option 3"]).strip(),
        str(q["Option 4"]).strip(),
    ]

    # Telegram sendPoll limits.
    if len(question) > 300:
        return False, "question 300 characters से ज्यादा है"

    rendered = [
        f"(1) {options[0]}",
        f"(2) {options[1]}",
        f"(3) {options[2]}",
        f"(4) {options[3]}",
    ]

    for i, option in enumerate(rendered, 1):
        if len(option) > 100:
            return False, f"option {i} 100 characters से ज्यादा है"

    if q["Correct"] not in {"1", "2", "3", "4"}:
        return False, "correct answer 1-4 में नहीं है"

    return True, ""


def telegram_request(token, method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"

    response = requests.post(
        url,
        data=payload,
        timeout=TELEGRAM_TIMEOUT
    )

    try:
        data = response.json()
    except ValueError as e:
        raise RuntimeError(
            f"Telegram API ने valid JSON नहीं दिया। HTTP {response.status_code}"
        ) from e

    return data


def check_bot(token):
    data = telegram_request(token, "getMe", {})
    if not data.get("ok"):
        raise RuntimeError(
            "Telegram bot connection error: "
            + str(data.get("description", data))
        )

    username = data["result"].get("username", "unknown")
    print(f"Telegram bot connected: @{username}")


def send_poll_with_retry(token, chat_id, q):
    options = [
        f"(1) {q['Option 1']}",
        f"(2) {q['Option 2']}",
        f"(3) {q['Option 3']}",
        f"(4) {q['Option 4']}",
    ]

    payload = {
        "chat_id": chat_id,
        "question": f"Q. {q['Question']}",
        "options": json.dumps(
            [{"text": x} for x in options],
            ensure_ascii=False
        ),
        "is_anonymous": True,
        "type": "quiz",
        "allows_multiple_answers": False,
        "correct_option_id": int(q["Correct"]) - 1,
        "explanation": compact_explanation(q["Explanation"]),
        "protect_content": False,
    }

    retry_count = 0

    while True:
        data = telegram_request(token, "sendPoll", payload)

        if data.get("ok"):
            return

        description = str(data.get("description", ""))

        # Telegram explicitly tells us how long to wait for 429.
        if data.get("error_code") == 429:
            retry_after = (
                data.get("parameters", {}).get("retry_after")
            )

            if retry_after is None:
                retry_after = 5

            retry_count += 1

            if retry_count > MAX_RETRIES_429:
                raise RuntimeError(
                    "Telegram rate-limit बार-बार आया; "
                    "maximum automatic retries समाप्त हो गए।"
                )

            wait_seconds = int(retry_after) + 1

            print(
                f"⚠️ Telegram rate limit. "
                f"{wait_seconds} sec wait करके उसी question को retry करेंगे..."
            )

            time.sleep(wait_seconds)
            continue

        raise RuntimeError(
            f"Telegram API error: {description}"
        )


def send_all_questions(config, questions, state):
    token = os.environ.get("BOT_TOKEN") or config.get("bot_token")
    chat_id = os.environ.get("CHAT_ID") or config.get("chat_id")

    if not token:
        raise RuntimeError("BOT_TOKEN नहीं मिला।")

    if not chat_id:
        raise RuntimeError("CHAT_ID नहीं मिला।")

    check_bot(token)

    total = len(questions)
    i = int(state.get("next_index", 0))

    state["batch_no"] = int(state.get("batch_no", 0)) + 1
    batch_no = state["batch_no"]
    save_json(STATE_FILE, state)

    print(f"Total questions in CSV: {total}")
    print(f"Starting from question index: {i}")
    print(f"Run batch: {batch_no}")

    sent = 0
    skipped = 0

    while i < total:
        q = questions[i]

        valid, reason = validate_question(q)

        if not valid:
            print(f"⏭️ Q{q['No']} SKIPPED — {reason}")

            state.setdefault("skipped_questions", [])
            if q["No"] not in state["skipped_questions"]:
                state["skipped_questions"].append(q["No"])

            i += 1
            state["next_index"] = i
            state["last_sent_at"] = dt.datetime.now(IST).isoformat()
            save_json(STATE_FILE, state)

            skipped += 1
            continue

        print(f"Sending Q{q['No']}...")

        # A 429 is handled internally with retry_after.
        send_poll_with_retry(token, chat_id, q)

        sent += 1
        i += 1

        # Save immediately after every successful post.
        state["next_index"] = i
        state["last_sent_at"] = dt.datetime.now(IST).isoformat()
        save_json(STATE_FILE, state)

        print(f"✓ Q{q['No']} sent")
        print(f"Progress: {i}/{total}")

        time.sleep(POLL_DELAY)

    print("================================")
    print("TOPIC COMPLETE")
    print(f"Questions sent: {sent}")
    print(f"Questions skipped: {skipped}")
    print(f"Next question index: {state['next_index']}")
    print("Progress saved.")
    print("================================")


def main():
    config_path = BASE_DIR / "config.json"
    config = load_json(config_path, {})

    questions = load_questions()

    state = load_json(
        STATE_FILE,
        {
            "next_index": 0,
            "batch_no": 0,
            "last_sent_at": None,
            "skipped_questions": []
        }
    )

    # GitHub Actions: send the entire topic in one run.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        send_all_questions(config, questions, state)
        return

    # Local/manual run.
    if config.get("run_now", True):
        send_all_questions(config, questions, state)
        return

    print("Local scheduler mode disabled; set run_now=true to run.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Program stopped.")
    except Exception as e:
        print("ERROR")
        print(str(e))
        raise

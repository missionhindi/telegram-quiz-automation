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
CSV_FILE = BASE_DIR / "sangya_70_questions.csv"
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

    required_columns = {
        "No",
        "Question",
        "Option 1",
        "Option 2",
        "Option 3",
        "Option 4",
        "Correct",
        "Explanation",
    }

    missing = required_columns - set(questions[0].keys())

    if missing:
        raise RuntimeError(
            f"CSV में ये columns missing हैं: {', '.join(sorted(missing))}"
        )

    for q in questions:
        if q["Correct"] not in {"1", "2", "3", "4"}:
            raise ValueError(
                f"Q{q['No']}: Correct answer 1, 2, 3 या 4 होना चाहिए।"
            )

        if len(q["Question"]) > 300:
            raise ValueError(
                f"Q{q['No']} का question 300 characters से बड़ा है।"
            )

    return questions


def compact_explanation(text, max_chars=200):
    text = " ".join(text.split())

    if len(text) <= max_chars:
        return text

    sentences = []
    current = ""

    for part in text.replace("।", "।|").split("|"):
        part = part.strip()

        if not part:
            continue

        candidate = (current + " " + part).strip()

        if len(candidate) <= max_chars:
            current = candidate
            sentences.append(part)
        else:
            break

    if current:
        return current

    return text[: max_chars - 1].rstrip() + "…"


def get_batch_size(remaining):
    if remaining >= 20:
        return 20

    if remaining >= 15:
        return 15

    if remaining >= 10:
        return 10

    if remaining >= 5:
        return 5

    return remaining


def telegram_request(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"

    try:
        response = requests.post(
            url,
            data=payload or {},
            timeout=TELEGRAM_TIMEOUT,
        )

    except requests.Timeout as e:
        raise RuntimeError(
            f"Telegram API timeout: {method}"
        ) from e

    except requests.RequestException as e:
        raise RuntimeError(
            f"Telegram API connection error: {e}"
        ) from e

    try:
        data = response.json()

    except ValueError as e:
        raise RuntimeError(
            f"Telegram API ने valid JSON नहीं दिया। HTTP {response.status_code}"
        ) from e

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {data.get('description', data)}"
        )

    return data


def check_bot(token):
    print("Telegram bot connection check...")

    data = telegram_request(
        token,
        "getMe",
    )

    username = data["result"].get("username", "unknown")

    print(f"Telegram bot connected: @{username}")

    return True


def send_poll(
    token,
    chat_id,
    question,
    options,
    correct_index,
    explanation,
):
    payload = {
        "chat_id": chat_id,
        "question": question,
        "options": json.dumps(
            [{"text": x} for x in options],
            ensure_ascii=False,
        ),
        "is_anonymous": True,
        "type": "quiz",
        "allows_multiple_answers": False,
        "correct_option_id": correct_index,
        "explanation": compact_explanation(explanation),
        "protect_content": False,
    }

    telegram_request(
        token,
        "sendPoll",
        payload,
    )


def send_daily_batch(config, questions, state):
    token = os.environ.get("BOT_TOKEN") or config.get("bot_token")
    chat_id = os.environ.get("CHAT_ID") or config.get("chat_id")

    if not token:
        raise RuntimeError(
            "BOT_TOKEN नहीं मिला। GitHub Settings → Secrets and variables "
            "→ Actions में BOT_TOKEN secret check करें।"
        )

    if not chat_id:
        raise RuntimeError(
            "CHAT_ID नहीं मिला। GitHub Settings → Secrets and variables "
            "→ Actions में CHAT_ID secret check करें।"
        )

    check_bot(token)

    print(f"CHAT_ID configured: {str(chat_id)[:3]}***")

    remaining = len(questions) - state["next_index"]

    if remaining <= 0:
        print("सभी questions complete हो चुके हैं।")
        return

    batch_no = state["batch_no"] + 1

    batch_size = get_batch_size(remaining)

    start = state["next_index"]
    end = start + batch_size

    batch = questions[start:end]

    print(
        f"Batch {batch_no}: "
        f"{len(batch)} questions भेजे जा रहे हैं..."
    )

    for i, q in enumerate(batch, start=1):

        total_in_batch = len(batch)

        poll_question = (
            f"Q. [{i}/{total_in_batch}] {q['Question']}"
        )

        options = [
            f"(1) {q['Option 1']}",
            f"(2) {q['Option 2']}",
            f"(3) {q['Option 3']}",
            f"(4) {q['Option 4']}",
        ]

        correct_index = int(q["Correct"]) - 1

        print(
            f"Sending Q{q['No']} "
            f"[{i}/{total_in_batch}]..."
        )

        send_poll(
            token=token,
            chat_id=chat_id,
            question=poll_question,
            options=options,
            correct_index=correct_index,
            explanation=q["Explanation"],
        )

        print(
            f"  ✓ Q{q['No']} sent"
        )

        # Telegram को लगातार requests से overload न करें।
        time.sleep(0.5)

    state["next_index"] = end
    state["batch_no"] = batch_no
    state["last_sent_at"] = dt.datetime.now(IST).isoformat()

    skipped = len(questions) - state["next_index"]

    if skipped and skipped < 5:
        state["skipped_questions"] = list(
            range(
                state["next_index"] + 1,
                len(questions) + 1,
            )
        )

        state["next_index"] = len(questions)

        print(
            f"अंत में {skipped} leftover questions skip किए गए।"
        )

    save_json(
        STATE_FILE,
        state,
    )

    print("================================")
    print("BATCH COMPLETE")
    print(f"Questions sent: {len(batch)}")
    print(f"Next question index: {state['next_index']}")
    print("Progress saved.")
    print("================================")


def seconds_until_9pm():
    now = dt.datetime.now(IST)

    target = now.replace(
        hour=21,
        minute=0,
        second=0,
        microsecond=0,
    )

    if now >= target:
        target += dt.timedelta(days=1)

    return max(
        0,
        int((target - now).total_seconds()),
    )


def main():
    print("================================")
    print("Telegram Quiz Scheduler")
    print("================================")

    config = load_json(
        CONFIG_FILE,
        {},
    )

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

    print(f"Total questions: {len(questions)}")
    print(f"Current question index: {state['next_index']}")
    print(f"Completed batches: {state['batch_no']}")

    # GitHub Actions में केवल एक batch भेजना है।
    # यहाँ 24 घंटे wait नहीं करना है।
    if os.environ.get("GITHUB_ACTIONS") == "true":

        print("Running inside GitHub Actions.")

        if state["next_index"] >= len(questions):
            print("सभी questions post हो चुके हैं।")
            return

        send_daily_batch(
            config,
            questions,
            state,
        )

        return

    # Local PC scheduler
    print("Daily schedule: 9:00 PM IST")

    if config.get("run_now", False):

        print("RUN_NOW enabled.")

        send_daily_batch(
            config,
            questions,
            state,
        )

        return

    while True:

        if state["next_index"] >= len(questions):
            print(
                "सभी questions post हो चुके हैं। "
                "Program बंद हो रहा है।"
            )
            break

        wait = seconds_until_9pm()

        print(
            f"Next run in "
            f"{wait // 3600}h "
            f"{(wait % 3600) // 60}m."
        )

        time.sleep(wait)

        if state["next_index"] < len(questions):

            send_daily_batch(
                config,
                questions,
                state,
            )


if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        print("\nProgram stopped.")

    except Exception as e:
        print("\n================================")
        print("ERROR")
        print("================================")
        print(str(e))
        raise

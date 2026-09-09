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
    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            questions.append(row)

    if not questions:
        raise RuntimeError("CSV में कोई question नहीं मिला।")

    for q in questions:
        if q["Correct"] not in {"1", "2", "3", "4"}:
            raise ValueError(f"Q{q['No']}: Correct 1-4 होना चाहिए।")
        if len(q["Question"]) > 300:
            raise ValueError(
                f"Q{q['No']} का Question Telegram की 300-character limit से बड़ा है। "
                "Question को source के अनुसार manually छोटा करना होगा; script उसे बदलता नहीं है।"
            )
    return questions


def compact_explanation(text, max_chars=200):
    """Telegram quiz explanation की 200-character limit के अंदर explanation रखता है."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text

    # पहले पूरे वाक्यों को बचाने की कोशिश
    sentences = []
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

    return text[: max_chars - 1].rstrip() + "…"


def get_batch_size(remaining):
    """20 चाहिए; आखिरी छोटा batch 5/10/15 में नीचे round होता है."""
    if remaining >= 20:
        return 20
    if remaining >= 15:
        return 15
    if remaining >= 10:
        return 10
    if remaining >= 5:
        return 5
    return remaining


def send_poll(token, chat_id, question, options, correct_index, explanation):
    url = f"https://api.telegram.org/bot{token}/sendPoll"
    payload = {
        "chat_id": chat_id,
        "question": question,
        "options": json.dumps([{"text": x} for x in options], ensure_ascii=False),
        "is_anonymous": True,
        "type": "quiz",
        "allows_multiple_answers": False,
        "correct_option_ids": json.dumps([correct_index]),
        "explanation": compact_explanation(explanation),
        "protect_content": False,
    }
    response = requests.post(url, data=payload, timeout=30)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data.get('description', data)}")
    return data["result"]


def send_daily_batch(config, questions, state):
    token = config["bot_token"]
    chat_id = config["chat_id"]
    batch_no = state["batch_no"] + 1

    remaining = len(questions) - state["next_index"]
    if remaining <= 0:
        print("सभी questions complete हो चुके हैं।")
        return True

    batch_size = get_batch_size(remaining)
    start = state["next_index"]
    end = start + batch_size
    batch = questions[start:end]

    print(f"Batch {batch_no}: {len(batch)} questions भेजे जा रहे हैं...")

    for i, q in enumerate(batch, start=1):
        total_in_batch = len(batch)
        poll_question = f"Q. [{i}/{total_in_batch}] {q['Question']}"
        options = [
            f"(1) {q['Option 1']}",
            f"(2) {q['Option 2']}",
            f"(3) {q['Option 3']}",
            f"(4) {q['Option 4']}",
        ]
        correct_index = int(q["Correct"]) - 1

        send_poll(
            token,
            chat_id,
            poll_question,
            options,
            correct_index,
            q["Explanation"],
        )

        print(f"  Sent Q{q['No']} -> [{i}/{total_in_batch}]")
        time.sleep(float(config.get("delay_between_polls", 1.0)))

    state["next_index"] = end
    state["batch_no"] = batch_no
    state["last_sent_at"] = dt.datetime.now(IST).isoformat()

    # अगर आखिरी batch 5/10/15 नहीं बन सकता था, get_batch_size ने नीचे round किया है।
    skipped = len(questions) - state["next_index"]
    if skipped and skipped < 5:
        state["skipped_questions"] = list(
            range(state["next_index"] + 1, len(questions) + 1)
        )
        state["next_index"] = len(questions)

    save_json(STATE_FILE, state)
    print("Batch complete.")
    return True


def seconds_until_9pm():
    now = dt.datetime.now(IST)
    target = now.replace(hour=21, minute=0, second=0, microsecond=0)
    if now >= target:
        target += dt.timedelta(days=1)
    return max(0, int((target - now).total_seconds()))


def main():
    config = load_json(CONFIG_FILE, {})
    if not config.get("bot_token"):
        raise RuntimeError("config.json में bot_token भरें।")
    if not config.get("chat_id"):
        raise RuntimeError("config.json में chat_id भरें।")

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
    print("Daily schedule: 9:00 PM IST")

    # अगर config में RUN_NOW=true है तो अभी एक batch भेज देगा।
    if config.get("run_now", False):
        send_daily_batch(config, questions, state)
        return

    while True:
        if state["next_index"] >= len(questions):
            print("सभी questions post हो चुके हैं। Program बंद हो रहा है।")
            break

        wait = seconds_until_9pm()
        print(f"Next run in {wait // 3600}h {(wait % 3600) // 60}m.")
        time.sleep(wait)

        if state["next_index"] < len(questions):
            send_daily_batch(config, questions, state)


if __name__ == "__main__":
    main()

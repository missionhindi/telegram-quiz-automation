import csv
import json
import os
import time
import requests

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

CSV_FILE = "karak_abhyas_prashn.csv"
PROGRESS_FILE = "progress_karak.json"

QUESTION_LIMIT = 300
OPTION_LIMIT = 100
EXPLANATION_LIMIT = 200
POLL_DELAY = 3


def save_progress(next_index, total):
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {"next_index": next_index, "topic": "कारक", "total": total},
            f, ensure_ascii=False, indent=2
        )
    os.replace(tmp, PROGRESS_FILE)


def load_progress(total):
    try:
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            index = int(json.load(f).get("next_index", 0))
        return max(0, min(index, total))
    except Exception:
        return 0


def telegram(method, payload):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    while True:
        try:
            response = requests.post(url, data=payload, timeout=60)
            data = response.json()
        except Exception as exc:
            print("Network error:", exc)
            time.sleep(5)
            continue

        if data.get("ok"):
            return data["result"]

        retry_after = (data.get("parameters") or {}).get("retry_after")
        if retry_after:
            wait = int(retry_after) + 2
            print(f"Telegram rate limit. Waiting {wait}s...")
            time.sleep(wait)
            continue

        raise RuntimeError(data)


def clean_option(value):
    text = str(value).strip()
    for marker in ("(अ)", "(ब)", "(स)", "(द)", "(1)", "(2)", "(3)", "(4)"):
        if text.startswith(marker):
            text = text[len(marker):].strip()
            break
    return text


def clean_embedded_labels(text):
    return (
        text.replace("(अ)", "(1)")
            .replace("(ब)", "(2)")
            .replace("(स)", "(3)")
            .replace("(द)", "(4)")
    )


def send_question(row):
    q_no = row["Q_No"].strip()
    question = row["Question"].strip()

    option_texts = [
        clean_embedded_labels(clean_option(row[f"Option_{i}"]))
        for i in range(1, 5)
    ]
    final_options = [
        f"({i}) {text}" for i, text in enumerate(option_texts, 1)
    ]
    final_question = f"Q. {question}"

    # Telegram limits apply to the complete displayed question/options,
    # including our Q. and (1)-(4) prefixes.
    if len(final_question) > QUESTION_LIMIT:
        return False, f"SKIPPED Q{q_no}: question too long ({len(final_question)} chars)"

    option_lengths = [len(option) for option in final_options]
    if any(length > OPTION_LIMIT for length in option_lengths):
        return False, f"SKIPPED Q{q_no}: option too long {option_lengths}"

    explanation = row.get("Explanation", "").strip()
    if len(explanation) > EXPLANATION_LIMIT:
        explanation = explanation[:EXPLANATION_LIMIT].rstrip()

    try:
        correct = int(row["Correct_Option"].strip()) - 1
    except ValueError:
        return False, f"SKIPPED Q{q_no}: invalid correct option"

    if correct not in range(4):
        return False, f"SKIPPED Q{q_no}: correct option must be 1-4"

    telegram("sendPoll", {
        "chat_id": CHAT_ID,
        "question": final_question,
        "options": json.dumps(final_options, ensure_ascii=False),
        "type": "quiz",
        "correct_option_id": correct,
        "is_anonymous": "true",
        "explanation": explanation,
        "allows_multiple_answers": "false",
    })
    return True, f"Q{q_no} sent"


def main():
    with open(CSV_FILE, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    total = len(rows)
    start = load_progress(total)

    print("Topic: कारक")
    print("Total questions in CSV:", total)
    print("Starting from question index:", start)

    sent = 0
    skipped = 0

    for index in range(start, total):
        ok, message = send_question(rows[index])
        print(message)

        # Both successful sends and deliberately skipped over-limit questions
        # are marked as processed. A failed Telegram request is NOT advanced.
        if not ok:
            skipped += 1
            save_progress(index + 1, total)
            continue

        sent += 1
        save_progress(index + 1, total)

        if index < total - 1:
            time.sleep(POLL_DELAY)

    print("Questions sent:", sent)
    print("Questions skipped:", skipped)
    print("Next question index:", total)
    print("TOPIC COMPLETE")


if __name__ == "__main__":
    main()

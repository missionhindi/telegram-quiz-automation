import csv
import json
import os
import time
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
import requests


# =========================================================
# TELEGRAM QUIZ SCHEDULER
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
CSV_FILE = BASE_DIR / "sangya_70_questions.csv"
STATE_FILE = BASE_DIR / "progress.json"

IST = ZoneInfo("Asia/Kolkata")

TELEGRAM_TIMEOUT = (5, 10)


# =========================================================
# JSON FUNCTIONS
# =========================================================

def load_json(path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp = path.with_suffix(".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    tmp.replace(path)


# =========================================================
# LOAD QUESTIONS
# =========================================================

def load_questions():

    questions = []

    if not CSV_FILE.exists():
        raise RuntimeError(
            f"CSV file नहीं मिली: {CSV_FILE}"
        )

    with CSV_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        for row in csv.DictReader(f):
            questions.append(row)

    if not questions:
        raise RuntimeError(
            "CSV में कोई question नहीं मिला।"
        )

    required_columns = {
        "No",
        "Question",
        "Option 1",
        "Option 2",
        "Option 3",
        "Option 4",
        "Correct",
        "Explanation"
    }

    missing = required_columns - set(questions[0].keys())

    if missing:
        raise RuntimeError(
            "CSV में ये columns missing हैं: "
            + ", ".join(sorted(missing))
        )

    for q in questions:

        if q["Correct"] not in {"1", "2", "3", "4"}:
            raise ValueError(
                f"Q{q['No']}: Correct answer "
                "1, 2, 3 या 4 होना चाहिए।"
            )

    return questions


# =========================================================
# EXPLANATION COMPACTER
# =========================================================

def compact_explanation(text, max_chars=200):

    if not text:
        return ""

    text = " ".join(str(text).split())

    if len(text) <= max_chars:
        return text

    sentences = []

    current = ""

    for part in text.replace("।", "।|").split("|"):

        part = part.strip()

        if not part:
            continue

        candidate = (
            current + " " + part
        ).strip()

        if len(candidate) <= max_chars:

            current = candidate
            sentences.append(part)

        else:
            break

    if current:
        return current

    return (
        text[:max_chars - 1].rstrip()
        + "…"
    )


# =========================================================
# DAILY BATCH SIZE
# =========================================================

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


# =========================================================
# TELEGRAM API REQUEST
# =========================================================

def telegram_request(
    token,
    method,
    payload=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/{method}"
    )

    try:

        response = requests.post(
            url,
            data=payload or {},
            timeout=TELEGRAM_TIMEOUT
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
            "Telegram API ने valid JSON नहीं दिया। "
            f"HTTP {response.status_code}"
        ) from e

    if not data.get("ok"):

        raise RuntimeError(
            "Telegram API error: "
            + str(
                data.get(
                    "description",
                    data
                )
            )
        )

    return data


# =========================================================
# CHECK BOT
# =========================================================

def check_bot(token):

    print(
        "Telegram bot connection check..."
    )

    data = telegram_request(
        token,
        "getMe"
    )

    username = (
        data["result"].get(
            "username",
            "unknown"
        )
    )

    print(
        f"Telegram bot connected: @{username}"
    )

    return True


# =========================================================
# SEND POLL
# =========================================================

def send_poll(
    token,
    chat_id,
    question,
    options,
    correct_index,
    explanation
):

    payload = {

        "chat_id": chat_id,

        "question": question,

        "options": json.dumps(
            [
                {"text": x}
                for x in options
            ],
            ensure_ascii=False
        ),

        "is_anonymous": True,

        "type": "quiz",

        "allows_multiple_answers": False,

        "correct_option_id": correct_index,

        "explanation": compact_explanation(
            explanation
        ),

        "protect_content": False,
    }

    telegram_request(
        token,
        "sendPoll",
        payload
    )


# =========================================================
# CHECK WHETHER QUESTION CAN BE SENT
# =========================================================

def validate_question(q):

    question_text = str(
        q["Question"]
    ).strip()

    options = [
        str(q["Option 1"]).strip(),
        str(q["Option 2"]).strip(),
        str(q["Option 3"]).strip(),
        str(q["Option 4"]).strip(),
    ]

    # -----------------------------------------------------
    # Telegram question limit
    # -----------------------------------------------------

    if len(question_text) > 300:

        return False, (
            "question 300 characters से ज्यादा है"
        )

    # -----------------------------------------------------
    # Telegram option limit
    # IMPORTANT:
    # (1) / (2) / (3) / (4) भी option text में जाते हैं
    # -----------------------------------------------------

    rendered_options = [

        f"(1) {options[0]}",
        f"(2) {options[1]}",
        f"(3) {options[2]}",
        f"(4) {options[3]}",
    ]

    for index, option in enumerate(
        rendered_options,
        start=1
    ):

        if len(option) > 100:

            return False, (
                f"option {index} "
                "100 characters से ज्यादा है"
            )

    return True, ""


# =========================================================
# SEND DAILY BATCH
# =========================================================

def send_daily_batch(
    config,
    questions,
    state
):

    token = (
        os.environ.get("BOT_TOKEN")
        or config.get("bot_token")
    )

    chat_id = (
        os.environ.get("CHAT_ID")
        or config.get("chat_id")
    )

    if not token:

        raise RuntimeError(
            "BOT_TOKEN नहीं मिला। "
            "GitHub Settings → Secrets and variables "
            "→ Actions में BOT_TOKEN secret check करें।"
        )

    if not chat_id:

        raise RuntimeError(
            "CHAT_ID नहीं मिला। "
            "GitHub Settings → Secrets and variables "
            "→ Actions में CHAT_ID secret check करें।"
        )

    check_bot(token)

    print(
        f"CHAT_ID configured: "
        f"{str(chat_id)[:3]}***"
    )

    start = state["next_index"]

    remaining = (
        len(questions) - start
    )

    if remaining <= 0:

        print(
            "सभी questions complete हो चुके हैं।"
        )

        return

    batch_no = (
        state["batch_no"] + 1
    )

    target_count = get_batch_size(
        remaining
    )

    print(
        "================================"
    )

    print(
        f"Batch {batch_no}: "
        f"{target_count} valid questions भेजे जाएंगे..."
    )

    print(
        "================================"
    )

    sent_count = 0

    skipped_count = 0

    i = start

    # -----------------------------------------------------
    # आगे चलते रहेंगे जब तक target valid questions
    # पूरे नहीं हो जाते
    # -----------------------------------------------------

    while (
        i < len(questions)
        and sent_count < target_count
    ):

        q = questions[i]

        valid, reason = validate_question(q)

        # -------------------------------------------------
        # LONG / INVALID QUESTION SKIP
        # -------------------------------------------------

        if not valid:

            print(
                f"⏭️ Skipping Q{q['No']} - {reason}"
            )

            skipped_count += 1

            # Skip को permanently record करें
            if q["No"] not in state.get(
                "skipped_questions",
                []
            ):

                state.setdefault(
                    "skipped_questions",
                    []
                ).append(q["No"])

            i += 1

            # Progress तुरंत save
            state["next_index"] = i

            save_json(
                STATE_FILE,
                state
            )

            continue

        # -------------------------------------------------
        # QUESTION FORMAT
        # केवल Q. रहेगा
        # [1/20] नहीं
        # -------------------------------------------------

        poll_question = (
            f"Q. {q['Question']}"
        )

        options = [

            f"(1) {q['Option 1']}",

            f"(2) {q['Option 2']}",

            f"(3) {q['Option 3']}",

            f"(4) {q['Option 4']}",
        ]

        correct_index = (
            int(q["Correct"]) - 1
        )

        print(
            f"Sending Q{q['No']} "
            f"[{sent_count + 1}/{target_count}]..."
        )

        # -------------------------------------------------
        # SEND
        # -------------------------------------------------

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

        sent_count += 1

        i += 1

        # -------------------------------------------------
        # VERY IMPORTANT:
        # हर successful question के बाद progress save
        # -------------------------------------------------

        state["next_index"] = i

        state["batch_no"] = batch_no

        state["last_sent_at"] = (
            dt.datetime.now(
                IST
            ).isoformat()
        )

        save_json(
            STATE_FILE,
            state
        )

        time.sleep(0.5)

    # =====================================================
    # FINAL LEFTOVER LOGIC
    # =====================================================

    remaining_after = (
        len(questions)
        - state["next_index"]
    )

    if (
        remaining_after > 0
        and remaining_after < 5
    ):

        leftover_questions = questions[
            state["next_index"] :
        ]

        for q in leftover_questions:

            if q["No"] not in state.get(
                "skipped_questions",
                []
            ):

                state.setdefault(
                    "skipped_questions",
                    []
                ).append(q["No"])

        print(
            f"अंत में {remaining_after} "
            "leftover questions skip किए गए।"
        )

        state["next_index"] = (
            len(questions)
        )

        save_json(
            STATE_FILE,
            state
        )

    # =====================================================
    # BATCH COMPLETE
    # =====================================================

    print(
        "================================"
    )

    print(
        "BATCH COMPLETE"
    )

    print(
        f"Questions sent: {sent_count}"
    )

    print(
        f"Questions skipped: {skipped_count}"
    )

    print(
        f"Next question index: "
        f"{state['next_index']}"
    )

    print(
        "Progress saved."
    )

    print(
        "================================"
    )


# =========================================================
# TIME UNTIL 9 PM
# =========================================================

def seconds_until_9pm():

    now = dt.datetime.now(IST)

    target = now.replace(

        hour=21,

        minute=0,

        second=0,

        microsecond=0
    )

    if now >= target:

        target += dt.timedelta(
            days=1
        )

    return max(
        0,
        int(
            (
                target - now
            ).total_seconds()
        )
    )


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "Telegram Quiz Scheduler"
    )

    config = load_json(
        CONFIG_FILE,
        {}
    )

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

    print(
        f"Total questions: "
        f"{len(questions)}"
    )

    print(
        f"Current question index: "
        f"{state['next_index']}"
    )

    print(
        f"Completed batches: "
        f"{state['batch_no']}"
    )

    # =====================================================
    # GITHUB ACTIONS
    # =====================================================

    if os.environ.get(
        "GITHUB_ACTIONS"
    ) == "true":

        print(
            "Running inside GitHub Actions."
        )

        if (
            state["next_index"]
            >= len(questions)
        ):

            print(
                "सभी questions post हो चुके हैं।"
            )

            return

        send_daily_batch(
            config,
            questions,
            state
        )

        return

    # =====================================================
    # LOCAL PC MODE
    # =====================================================

    print(
        "Daily schedule: 9:00 PM IST"
    )

    # -----------------------------------------------------
    # RUN_NOW
    # -----------------------------------------------------

    if config.get(
        "run_now",
        False
    ):

        print(
            "RUN_NOW enabled."
        )

        send_daily_batch(
            config,
            questions,
            state
        )

        return

    # -----------------------------------------------------
    # NORMAL 9 PM LOOP
    # -----------------------------------------------------

    while True:

        if (
            state["next_index"]
            >= len(questions)
        ):

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

        if (
            state["next_index"]
            < len(questions)
        ):

            send_daily_batch(
                config,
                questions,
                state
            )


# =========================================================
# PROGRAM START
# =========================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\nProgram stopped."
        )

    except Exception as e:

        print(
            "ERROR"
        )

        print(
            str(e)
        )

        raise

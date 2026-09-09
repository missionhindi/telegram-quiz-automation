TELEGRAM DAILY QUIZ SETUP

Files:
1. sangya_70_questions.csv  -> इस PDF से तैयार 70 questions
2. telegram_quiz_scheduler.py -> रोज़ 9:00 PM IST पर quiz भेजने वाला script
3. config.json -> Bot Token और channel username यहाँ भरना है
4. requirements.txt -> Python dependency

Format:
Q. [1/20] Question...
Options:
(1) ...
(2) ...
(3) ...
(4) ...

Telegram quiz में correct answer set होगा और explanation answer के बाद दिखाई जाएगी.
Telegram quiz explanation की limit 200 characters है; script जरूरत पड़ने पर explanation को concise करता है.

Daily:
Day 1 = 20
Day 2 = 20
Day 3 = 20
Day 4 = 10

Important:
- Bot को channel का admin बनाकर poll/message permission देना जरूरी है.
- config.json में bot_token और chat_id भरें.
- पहले test के लिए run_now=true करके एक batch भेज सकते हैं.
- बाद में run_now=false करके रोज़ 9 PM automation चलाएँ.
- progress.json script खुद बनाएगा, इसलिए restart के बाद वही position continue होगी.

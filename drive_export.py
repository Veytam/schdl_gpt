"""Export the academic details needed by ChatGPT, without exporting the chat."""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOTTEXT = "Учебное расписание и задания.txt"
ROOT = Path("/opt/study-schedule")
REMOTE = "study-drive:"
RCLONE = [
    "/usr/bin/docker", "run", "--rm", "-i",
    "--mount", f"type=bind,src={ROOT / 'drive-config'},dst=/config/rclone",
    "study-schedule-drive:1.76.0", "--config", "/config/rclone/rclone.conf",
]
LINK = re.compile(r"https?://\S+|t\.me/\S+|@[\w.]+|[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.I)
PHONE = re.compile(r"(?:\+7|8)[ (\-]*\d{3}[ )\-]*\d{3}[- ]*\d{2}[- ]*\d{2}")
TIMES = re.compile(r"^\s*\d{1,2}[.:]\d{2}\s*[–—-]\s*\d{1,2}[.:]\d{2}\b")
SCHEDULE_DATE = re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b")
NO_CLASS = re.compile(r"^\s*(?:пар нет|пар не будет|пары отменены|выходной|без пар)[.!]?\s*$", re.I)
ASSIGNMENT = re.compile(
    r"\b(?:дедлайн\w*|прикрепить|сдать|подготовить|выполнить|загрузить|"
    r"домашн\w+ задан\w+|итогов\w+ задан\w+|"
    r"задача\s+(?:изучить|сделать)|пришлите|с\.[ ]*\d+.*задан\w*)\b|\bзадание\s*:", re.I,
)
GREETING = re.compile(r"^(?:привет|добрый день|здравствуйте)[!. ]*", re.I)
TASK_DATE = re.compile(r"\b(?:срок|дедлайн|до\s+\d|до конца недели|к следующему занятию)\b", re.I)


def clean(line):
    line = PHONE.sub("", LINK.sub("", line))
    line = line.replace("\\", "").strip(" •-\t")
    return re.sub(r"\s+", " ", line)


def load_archive():
    sql = """
    BEGIN READ ONLY;
    SET LOCAL statement_timeout = '8s';
    SELECT json_build_object(
        'refreshed', (SELECT synced_at AT TIME ZONE 'Europe/Moscow' FROM sync_state),
        'messages', COALESCE(json_agg(item ORDER BY posted DESC, id DESC), '[]')
    )
    FROM (
        SELECT m.id, t.title AS topic, m.body,
               m.sent_at AT TIME ZONE 'Europe/Moscow' AS posted
        FROM messages m JOIN topics t ON t.id = m.topic_id
        WHERE m.sent_at >= now() - interval '60 days'
    ) AS item;
    COMMIT;
    """
    argv = ["/usr/bin/docker", "exec", "-i", "study-schedule-db",
            "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "schedule_reader", "-d", "schedule"]
    result = subprocess.run(argv, input=sql, text=True, stdout=subprocess.PIPE, timeout=20, check=True)
    archive = json.loads(result.stdout)
    if not archive["refreshed"]:
        raise RuntimeError("Telegram archive has not synced")
    return archive


def render(archive):
    updated = datetime.fromisoformat(archive["refreshed"]).strftime("%d.%m.%Y %H:%M")
    output = [
        "Учебное расписание и задания",
        f"Архив Telegram обновлён {updated} по Москве.",
        "Время занятий и публикаций указано по Москве. Даты занятий берутся из объявлений.",
        "Задания отбираются по текстовым признакам. Если даты нет или записи расходятся, сверяйся с темой группы.",
    ]
    schedule = []
    assignments = []
    unique_tasks = set()
    for msg in archive["messages"]:
        posted = datetime.fromisoformat(msg["posted"])
        lines = [clean(line) for line in msg["body"].splitlines()]
        lines = [line for line in lines if line]
        title = msg["topic"]
        if title.casefold().startswith("расписание"):
            course_lines = [line[:220] for line in lines if TIMES.match(line) or NO_CLASS.fullmatch(line)]
            if not course_lines or len(schedule) >= 18:
                continue
            heading = next((line[:110] for line in lines[:3] if line.casefold().startswith("расписание на")), "")
            date_line = next((line[:80] for line in lines[:3] if SCHEDULE_DATE.search(line) and not TIMES.match(line)), "")
            metadata = " ".join(dict.fromkeys(filter(None, [heading, date_line])))
            schedule.append([f"Опубликовано {posted:%d.%m.%Y %H:%M}" + (f" · {metadata}" if metadata else ""), *course_lines])
        elif len(assignments) < 50 and not re.search(r"справк|отсутств", title, re.I):
            task_lines = [line for line in lines if ASSIGNMENT.search(line)]
            if not task_lines:
                continue
            deadline_lines = [line for line in lines if TASK_DATE.search(line)]
            chosen = []
            for line in dict.fromkeys(task_lines[:5] + deadline_lines[:4]):
                # Keep the assignment line itself, not greetings or adjacent participant lists.
                line = GREETING.sub("", line)
                if line and not line.endswith(":") and len(line) <= 330:
                    chosen.append(line)
            if chosen:
                subject = re.sub(r"\s*\([^()]*\)\s*$", "", title)
                key = subject, tuple(chosen)
                if key not in unique_tasks:
                    unique_tasks.add(key)
                    assignments.append([f"{posted:%d.%m.%Y} · {subject}", *chosen])
    for heading, groups in [("Расписание", schedule), ("Задания и сроки", assignments)]:
        output.extend(["", heading])
        if not groups:
            output.append("Записей пока нет.")
        for group in groups:
            output.extend(["", group[0], *("• " + line for line in group[1:])])
    return "\n".join(output) + "\n"


if __name__ == "__main__":
    content = render(load_archive())
    if sys.argv[1:] == ["--preview"]:
        print(content, end="")
    elif not sys.argv[1:]:
        subprocess.run(
            [*RCLONE, "rcat", REMOTE + ROOTTEXT, "--drive-scope", "drive.file"],
            input=content.encode(), timeout=100, check=True,
        )
    else:
        raise SystemExit("Unknown arguments")

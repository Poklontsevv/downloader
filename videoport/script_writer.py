"""Generate shootable scenes from a chat brief and reject incomplete drafts."""

import json
import re

WRITER = (
    "https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
    "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597", 2497281120,
)
EMPTY_BRIEF = re.compile(r"^(?:напиши(?:те)? сценарий|написать сценарий|начни|сгенерируй(?: сценарий)?)[.! ]*$", re.I)
REFUSAL = re.compile(
    r"вы не указали (?:тему|бриф|содержание)|уточните тему|не (?:указана|предоставлена) тема|нет (?:темы|брифа)|"
    r"пожалуйста,? (?:укажите|предоставьте) тему|проверяем факт|извините,? но", re.I,
)
INSTRUCTIONS = (
    "Ты сценарист русскоязычных видео. Поле brief — полноценный авторский бриф, который уже содержит тему. "
    "Напиши реплики, которые исполнитель произносит в кадре, и зримые действия, которые он делает в это время. "
    "Используй персонажей, место, камеру, настроение и жанр из брифа. Чтобы развить идею, придумай подходящие "
    "детали реквизита и действий. Для ASMR важны мягкий темп, различимые звуки, паузы и обращение героя напрямую "
    "к зрителю. В расслабляющей сцене оставляй зрителю выбор, предлагай варианты и избегай давления. "
    "В ролевом видео не выходи из роли: персонаж обращается к зрителю как к участнику ситуации. "
    "Если указан зелёный фон, это условие съёмки; в мире истории видна заявленная локация. "
    "Герои не обсуждают фон, камеру, микрофон или съёмку. Если камера статична, сохраняй один ракурс. "
    "Не открывай ответ обсуждением брифа. Пиши на русском."
)


def brief_for(project, instruction):
    prior = [message["text"].strip() for message in project["messages"]
             if message["role"] == "user" and message.get("mode") not in {"discussion", "board"}
             and message["text"].strip() and not EMPTY_BRIEF.fullmatch(message["text"].strip())]
    title = project["title"].strip()
    latest = instruction.strip()
    items = list(dict.fromkeys([
        project.get("brief", title), *prior[-5:], latest if not EMPTY_BRIEF.fullmatch(latest) else "",
    ]))
    return "\n".join(item for item in items if item)[-4700:]


def script_preview(text):
    candidates = re.findall(r"^Реплика(?: \[[^\n]+\])?\s*[:·]\s*[«„\"]?(.+)$", text, re.M)
    excerpt = " ".join(candidates[:3]) if candidates else text.strip().replace("\n", " ")
    sentences = re.split(r"(?<=[.!?…])\s+", re.sub(r"\s+", " ", excerpt.strip()))
    return " ".join(sentences[:3])[:330].rstrip(" «:;—")


def normalize_line(value):
    return " ".join(re.findall(r"\w+", value.casefold()))


def has_loop(value):
    words = normalize_line(value).split()
    groups = [tuple(words[i:i+6]) for i in range(max(0, len(words)-5))]
    return len(groups) >= 12 and len(set(groups)) / len(groups) < .72


def text_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for entry in value.values():
            yield from text_values(entry)
    elif isinstance(value, list):
        for entry in value:
            yield from text_values(entry)


def generate_json(complete, system, data, schema, read, tokens):
    for attempt in range(3):
        output = complete({
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object", "schema": schema},
            "max_tokens": tokens, "temperature": .48 + attempt * .14, "top_p": .9,
            "repeat_penalty": 1.12, "repeat_last_n": 150, "presence_penalty": .16,
        })
        try:
            parsed = json.loads(output)
            if any(REFUSAL.search(text) or has_loop(text) for text in text_values(parsed)):
                continue
            return read(parsed)
        except (TypeError, KeyError, ValueError):
            continue
    raise ValueError("Qwen не закончил связный ответ. Бриф сохранён. Отправьте его ещё раз или уточните действие в сцене.")


def array(items, minimum, maximum):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


def required(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def validate_plan(data, count, reject_stage_notes=False):
    plan = [item.strip() for item in data["plan"]]
    if (
        len(plan) != count or min(map(len, plan)) < 12 or len({normalize_line(item) for item in plan}) != count
        or (reject_stage_notes and any(re.search(r"зел[её]н.{0,20}фон|хромакей|длительность ролика", item, re.I) for item in plan))
    ):
        raise ValueError("Incomplete plan")
    return plan


def validate_beats(data, count, seen, dialogue=True):
    beats = data["beats"]
    if len(beats) != count:
        raise ValueError("Incomplete scene")
    unique_actions, unique_lines = set(), set()
    for beat in beats:
        action = normalize_line(beat["camera_action"])
        line = normalize_line(beat["spoken_line"])
        if len(action) < 8 or action in unique_actions or action in seen:
            raise ValueError("Repeated action")
        if line:
            if line in unique_lines or line in seen:
                raise ValueError("Repeated dialogue")
            unique_lines.add(line)
        unique_actions.add(action)
    if dialogue and len(unique_lines) < 2:
        raise ValueError("Missing dialogue")
    seen.update(unique_actions | unique_lines)
    return beats


def scene_clock(seconds):
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02}"


def excerpt(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit * 2 // 3] + "\n[…]\n" + text[-limit // 3:]


def draft(complete, project, brief, notes, previous, progress):
    duration = project["seconds"]
    count = 1 if duration <= 80 else min(12, (duration + 99) // 100)
    common = dict(brief=brief, title=project["title"], audience=project["audience"] or "Зритель ролика",
                  duration_seconds=duration, author_notes=notes[:1400])
    quiet = bool(re.search(r"асмр|asmr|медитац", brief, re.I))
    dialogue = not re.search(r"без (?:диалога|реплик|озвучки|голоса)", brief, re.I)
    if quiet and re.search(r"ресепш|отел|гостиниц", brief, re.I):
        common["context"] = (
            "Зритель — уставший гость. Действия происходят за стойкой отеля. У администратора две задачи: "
            "помочь гостю устроиться и дать ему время успокоиться. Используй действия у стойки и естественные реплики из роли."
        )
    if previous:
        common["prior_version"] = excerpt(previous, 2000)
    if count == 1:
        plan = ["Начало, развитие действия и завершение эпизода"]
    else:
        progress("Qwen планирует сцены", 0)
        schema = required({"plan": array({"type": "string", "maxLength": 110}, count, count)})
        plan = generate_json(complete, INSTRUCTIONS + " Сначала составь план. Каждая строка — новое конкретное действие "
                             "или этап истории. На сцену одна короткая фраза из 5–10 слов. Число сцен задано в запросе.",
                             dict(common, scene_count=count), schema, lambda data: validate_plan(data, count, quiet), 700)
    result, seen = [], set()
    for index, scene in enumerate(plan):
        progress(f"Qwen пишет сцену {index + 1} из {count}", round(100 * index / count))
        start, finish = (round(duration * position / count) for position in (index, index + 1))
        amount = min(5, max(3, round((finish - start) / (30 if quiet else 20))))
        limit = 115 if quiet else 500
        beat = required({
            "camera_action": {"type": "string", "maxLength": 150},
            "spoken_line": {"type": "string", "maxLength": limit},
            "audio_pause": {"type": "string", "maxLength": 70},
        })
        request = dict(common, scene=scene, scene_number=index+1, total_scenes=count, start=start, end=finish,
                       beats=amount, previous_scene=plan[index-1] if index else "")
        beats = generate_json(
            complete,
            INSTRUCTIONS + " Напиши только текущую сцену. Каждый блок продолжает действие. camera_action — действие "
            "в кадре, краткое законченное предложение до 13 слов; spoken_line — дословная реплика персонажа; "
            "audio_pause — два–шесть слов о слышимом звуке или тихой паузе. "
            "Для спокойного видео реплики короткие; остальное время занимают физические действия и тишина. "
            "Реплики внутри сцены различаются. В последней сцене заверши ситуацию по брифу."
            + ("" if dialogue else " В этом сценарии нет разговоров. Все поля spoken_line оставь пустыми."),
            request, required({"beats": array(beat, amount, amount)}),
            lambda value: validate_beats(value, amount, seen, dialogue), min(2800, amount * (185 if limit == 115 else 340)),
        )
        paragraphs = []
        for position, part in enumerate(beats):
            at = round(start + position * (finish - start) / len(beats))
            lines = [f"[{scene_clock(at)}] Действие · {part['camera_action'].strip()}"]
            if part["spoken_line"].strip():
                lines.append(f"Реплика · «{part['spoken_line'].strip().strip('«»')}»")
            if part["audio_pause"].strip():
                lines.append("Звук и пауза · " + part["audio_pause"].strip())
            paragraphs.append("\n".join(lines))
        heading = f"{scene_clock(start)}–{scene_clock(finish)} · {scene}"
        result.append(heading + "\n\n" + "\n\n".join(paragraphs))
    return "\n\n".join(result)

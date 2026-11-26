"""Select and validate timed excerpts from the source transcript."""

from bisect import bisect_left, bisect_right
import re


MAX_SEGMENTS = 12
FILLER = re.compile(r"(?:[мэа]+|эм+|хм+|угу|ага|ну|ээ+|um+|uh+|erm+|hmm+)", re.I)
COMMON_WORDS = set(
    "когда потом сейчас потому поэтому просто вроде значит вообще очень только который которая которые "
    "можно нужно будет может было были стали также такой такую тогда этого здесь чтобы если себя совсем "
    "видео дальше сегодня именно говорю сказал делать "
    "about after again around actually because before being could going really should still there these "
    "thing those through today where which while would gonna".split()
)


def speech_blocks(transcript, motion, cuts, duration):
    """Form short spoken passages and give nearby on-screen actions room to finish."""
    blocks = []
    motion_times = [time for time, value in motion if value >= 10]
    for row in transcript:
        words = re.findall(r"[^\W\d_]+", row["text"])
        if not words or all(FILLER.fullmatch(word) for word in words):
            continue
        start, end = max(0, row["start"]), min(duration, row["end"])
        if end <= start:
            continue
        if blocks:
            previous = blocks[-1]
            gap = start - previous["end"]
            action = bisect_left(motion_times, previous["end"])
            action_in_gap = action < len(motion_times) and motion_times[action] < start
            sentence_ended = previous["text"].endswith((".", "?", "!")) and previous["end"] - previous["start"] >= 4
            if (end - previous["start"] <= 16
                    and (gap <= 0 or gap <= 0.75 and not sentence_ended or gap <= 2.5 and action_in_gap)):
                previous["end"] = max(previous["end"], end)
                previous["text"] += " " + row["text"]
                continue
        blocks.append(dict(start=start, end=end, text=row["text"]))

    for index, block in enumerate(blocks):
        left = blocks[index - 1]["end"] if index else 0
        right = blocks[index + 1]["start"] if index + 1 < len(blocks) else duration
        nearby_cut = bisect_left(cuts, block["start"])
        before = cuts[nearby_cut - 1] if nearby_cut else 0
        finish_cut = bisect_left(cuts, block["end"])
        after = cuts[finish_cut] if finish_cut < len(cuts) else duration
        start_margin = min(1.1, block["start"] - before) if block["start"] - before < 1.1 else 0.3
        end_margin = min(1.1, after - block["end"]) if after - block["end"] < 1.1 else 0.45
        lead_action = bisect_left(motion_times, max(before, block["start"] - 1.1))
        if lead_action < len(motion_times) and motion_times[lead_action] < block["start"]:
            start_margin = max(start_margin, block["start"] - motion_times[lead_action])
        tail_action = bisect_left(motion_times, block["end"])
        if tail_action < len(motion_times) and motion_times[tail_action] < min(after, block["end"] + 1.25):
            end_margin = min(after - block["end"], 1.25)
        start = max(left, block["start"] - start_margin)
        end = min(right, max(block["end"] + end_margin, start + 1))
        if end - start >= 1 - 1e-6:
            action = bisect_left(motion_times, start)
            block.update(clip_start=round(start, 3), clip_end=round(end, 3),
                         action=action < len(motion_times) and motion_times[action] < end)
    return [block for block in blocks if "clip_start" in block]


def intersecting_blocks(candidate, blocks):
    return [index for index, block in enumerate(blocks)
            if block["start"] < candidate["end"] and block["end"] > candidate["start"]]


def joined_segments(windows):
    segments = []
    for start, end in sorted(windows):
        if end - start < 1 - 1e-6:
            continue
        if segments and start <= segments[-1]["end"] + 0.35:
            segments[-1]["end"] = max(segments[-1]["end"], end)
        else:
            segments.append(dict(start=start, end=end))
    return segments


def trim_candidate_pauses(candidates, blocks, motion):
    """Cut long gaps when the video also has no marked action during the gap."""
    action_times = [time for time, value in motion if value >= 10]
    for candidate in candidates:
        if not candidate["text"]:
            continue
        windows = [(max(candidate["start"], blocks[index]["clip_start"]),
                    min(candidate["end"], blocks[index]["clip_end"]))
                   for index in intersecting_blocks(candidate, blocks)]
        segments = joined_segments(windows)
        if not 2 <= len(segments) <= MAX_SEGMENTS:
            continue
        for left, right in zip(segments, segments[1:]):
            action = bisect_left(action_times, left["end"])
            if action < len(action_times) and action_times[action] < right["start"]:
                break
        else:
            candidate["segments"] = segments
            candidate["reason"] += " · Паузы вырезаны"


def montage_actions(candidates, motion, cuts):
    """Keep separate bursts of movement within the same shot for clips without speech."""
    if not motion:
        return
    values = sorted(value for _, value in motion)
    threshold = max(6, values[int((len(values) - 1) * 0.8)])
    peaks = [(time, value) for time, value in motion if value >= threshold]
    times = [time for time, _ in peaks]
    for candidate in candidates:
        if candidate["text"]:
            continue
        start, end = candidate["start"], candidate["end"]
        nearby = peaks[bisect_left(times, start):bisect_left(times, end)]
        if not nearby:
            continue
        peak, _ = max(nearby, key=lambda entry: entry[1])
        shot = bisect_right(cuts, peak)
        left = max(start, cuts[shot - 1]) if shot else start
        right = min(end, cuts[shot]) if shot < len(cuts) else end
        windows = [(max(left, time - 0.9), min(right, time + 1.25))
                   for time in times[bisect_left(times, left):bisect_left(times, right)]]
        segments = joined_segments(windows)
        total = sum(segment["end"] - segment["start"] for segment in segments)
        if 2 <= len(segments) <= MAX_SEGMENTS and 5 <= total <= (end - start) * 0.9:
            candidate["segments"] = segments
            candidate["reason"] = "Действия в одной сцене · Паузы вырезаны"


def topic_terms(text):
    return {word[:5] for word in re.findall(r"[^\W\d_]{5,}", text.lower())
            if word not in COMMON_WORDS}


def montage_material(candidates, blocks):
    """Give Qwen nearby passages and related passages from elsewhere in the source."""
    terms = [topic_terms(block["text"]) for block in blocks]
    items, local_by_id = [], {}
    for index, candidate in enumerate(candidates):
        if not candidate["text"]:
            continue
        local = intersecting_blocks(candidate, blocks)
        if not local:
            continue
        topics = set().union(*(terms[entry] for entry in local))
        nearby = set(local)
        ranked = []
        for entry, words in enumerate(terms):
            shared = len(topics & words)
            if entry in nearby or not shared:
                continue
            gap = min(abs(blocks[entry]["start"] - candidate["end"]),
                      abs(candidate["start"] - blocks[entry]["end"]))
            if gap <= 100 or shared >= 2:
                ranked.append((shared, -gap, entry))
        related = [entry for _, _, entry in sorted(ranked, reverse=True)[:3]]
        if len(local) > 4:
            local = local[:3] + local[-1:]
        pool = local + related
        title_terms = topic_terms(candidate["title"])
        anchor = max(local, key=lambda entry: (
            sum(len(terms[entry] & terms[other]) for other in pool if other != entry),
            len(terms[entry] & title_terms),
        ))
        local_by_id[index] = set(local)
        items.append(dict(id=index, anchor=anchor, excerpts=[
            dict(id=entry, at=round(blocks[entry]["start"], 1), speech=blocks[entry]["text"][:165],
                 seconds=round(blocks[entry]["clip_end"] - blocks[entry]["clip_start"], 1),
                 motion=blocks[entry]["action"])
            for entry in sorted(pool)
        ]))
        if len(items) >= 8:
            break
    return items, local_by_id


def apply_montage(candidate, blocks, allowed, local, anchor, keep, length):
    if not isinstance(keep, list) or not keep or any(type(entry) is not int or entry not in allowed for entry in keep):
        return False
    selected = set(keep)
    central = selected & local
    if not central:
        return False
    anchor = anchor if anchor in central else min(central)
    terms = topic_terms(blocks[anchor]["text"])
    related = {entry for entry in selected - {anchor} if terms & topic_terms(blocks[entry]["text"])}
    if related:
        core = related | {anchor}
        # A neighboring setup or reaction can complete the thought even without repeated terms.
        selected = core | {
            entry for entry in selected - core
            if any(max(blocks[entry]["start"] - blocks[other]["end"],
                       blocks[other]["start"] - blocks[entry]["end"]) <= 1.25 for other in core)
        }
    segments = joined_segments((blocks[index]["clip_start"], blocks[index]["clip_end"]) for index in selected)
    total = sum(segment["end"] - segment["start"] for segment in segments)
    if 5 <= total <= length + 8 and 1 <= len(segments) <= MAX_SEGMENTS:
        candidate["segments"] = segments
        return True
    return False

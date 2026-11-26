import { clock } from "./clip-editor.js";

const DEFAULT_STYLE = {
  font: "blocks", mode: "3", size: 78, color: "#ffffff", stroke: "#171215",
  outline: 5, shadow: 2, position: 78, background: false,
};
const round = (value) => Math.round(value * 100) / 100;

function wordsIn(cue) {
  if (cue.words?.length) return cue.words;
  const tokens = cue.text.trim().split(/\s+/).filter(Boolean);
  const step = (cue.end - cue.start) / tokens.length;
  return tokens.map((text, index) => ({
    text, start: cue.start + step * index, end: cue.start + step * (index + 1),
  }));
}

function displayEvents(rows, mode) {
  const events = [];
  for (const { span, cue } of rows) {
    const size = mode === "lines" ? 6 : Number(mode);
    const words = mode === "phrase" ? [cue]
      : wordsIn(cue).filter((word) => word.end > span.start && word.start < span.end);
    const step = mode === "phrase" ? 1 : size;
    for (let i = 0; i < words.length; i += step) {
      const group = words.slice(i, i + step);
      const lo = Math.max(span.start, group[0].start);
      const hi = Math.min(span.end, group.at(-1).end);
      if (hi <= lo || !cue.text.trim()) continue;
      const labels = group.map((word) => word.text);
      const text = labels.slice(0, 3).join(" ") + (labels.length > 3 ? "\n" + labels.slice(3).join(" ") : "");
      events.push({ start: span.offset + lo - span.start, end: span.offset + hi - span.start, text });
    }
  }
  events.sort((left, right) => left.start - right.start);
  return events.map((event, index) => ({
    ...event, end: Math.min(event.end, events[index + 1]?.start ?? event.end),
  })).filter((event) => event.end > event.start);
}

export function mountCaptions(root, editor, original, initialStyle, fonts, onEdit, onPreview) {
  const find = (selector) => root.querySelector(selector);
  const select = find(".caption-select");
  const lane = find(".caption-track");
  const text = find(".caption-text");
  const from = find(".caption-start");
  const to = find(".caption-end");
  const fields = find(".caption-fields");
  const message = find(".caption-message");
  const add = find(".caption-add");
  const split = find(".caption-split");
  const merge = find(".caption-merge");
  const remove = find(".caption-delete");
  const undo = find(".caption-undo");
  const prevButton = find(".caption-previous");
  const nextButton = find(".caption-next");
  const reset = find(".caption-reset");
  const srt = find(".caption-srt");
  let preservedWords;
  const style = { ...DEFAULT_STYLE, ...initialStyle };
  const font = find('[data-caption-style="font"]');
  for (const item of fonts) addFontOption(item);
  const history = [];
  let transcript = original;
  let selected;
  let rows = [];
  let spans = [];
  let textCheckpoint = false;

  function addFontOption(item) {
    font.add(new Option(item.name + (item.cyrillic ? "" : " · без кириллицы"), item.id));
  }

  function checkpoint(part) {
    history.push({ part, before: part.cues === null ? null : structuredClone(part.cues) });
    if (history.length > 30) history.shift();
    undo.disabled = false;
  }

  function editable(row) {
    const part = row.span.part;
    if (part.cues === null) {
      const values = transcript.filter((cue) => cue.start < row.span.end && cue.end > row.span.start);
      part.cues = values.map((cue) => ({ ...cue }));
      return part.cues[values.indexOf(row.cue)];
    }
    return row.cue;
  }

  function details() {
    fields.disabled = !selected;
    text.value = selected?.cue.text ?? "";
    from.value = selected ? round(selected.start) : "";
    to.value = selected ? round(selected.end) : "";
    textCheckpoint = false;
    preservedWords = null;
  }

  function choose(row, seek = false) {
    selected = row;
    details();
    syncSelection();
    if (row && seek) editor.seek(row.start);
  }

  function nextCue() {
    const index = rows.indexOf(selected);
    return index >= 0 && rows[index + 1]?.span.part === selected.span.part ? rows[index + 1] : null;
  }

  function syncSelection() {
    const index = rows.indexOf(selected);
    select.value = index < 0 ? "" : String(index);
    lane.querySelectorAll("button").forEach((button, entry) =>
      button.setAttribute("aria-pressed", String(entry === index)));
    prevButton.disabled = index <= 0;
    nextButton.disabled = index >= rows.length - 1;
    reset.disabled = !editor.currentPart() || editor.currentPart().cues === null;
    srt.disabled = !rows.some((row) => row.cue.text.trim());
    remove.disabled = !selected;
    merge.disabled = !nextCue();
    split.disabled = !selected || wordsIn(selected.cue).length < 2 || selected.end - selected.start < .2;
    undo.disabled = !history.some((item) => editor.segments.includes(item.part));
  }

  function paint(inputs = true) {
    let offset = 0;
    spans = editor.value().segments.flatMap((segment, index) => {
      if (!Number.isFinite(segment.start) || !Number.isFinite(segment.end) || segment.end <= segment.start) return [];
      const span = { part: editor.segments[index], start: segment.start, end: segment.end, offset };
      offset += segment.end - segment.start;
      return [span];
    });
    rows = spans.flatMap((span) => (span.part.cues ?? transcript).filter((cue) =>
      cue.start < span.end && cue.end > span.start).map((cue) => ({
      span, cue, start: span.offset + Math.max(span.start, cue.start) - span.start,
      end: span.offset + Math.min(span.end, cue.end) - span.start,
    })).sort((first, second) => first.start - second.start));
    const previous = selected;
    selected = rows.find((row) => previous && row.cue === previous.cue && row.span.part === previous.span.part);
    select.replaceChildren(new Option(rows.length ? "Выберите блок" : "Блоков пока нет", ""),
      ...rows.map((row, index) => new Option(`${clock(row.start)} · ${row.cue.text || "Пустой блок"}`, String(index))));
    lane.replaceChildren(...rows.map((row) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = row.cue.text || "Текст";
      button.title = `${clock(row.start)} · ${row.cue.text}`;
      button.style.left = (100 * row.start / offset) + "%";
      button.style.width = (100 * (row.end - row.start) / offset) + "%";
      for (const edge of ["start", "end"]) {
        const handle = document.createElement("span");
        handle.className = "caption-grip caption-grip-" + edge;
        handle.setAttribute("aria-hidden", "true");
        handle.addEventListener("pointerdown", (event) => trimCue(event, handle, button, row, edge, offset));
        button.append(handle);
      }
      button.addEventListener("click", () => {
        find('[data-editor-panel="caption-panel"]').click();
        find('[data-caption-panel="text"]').click();
        choose(row, true);
      });
      return button;
    }));
    add.disabled = !spans.length;
    if (inputs) details();
    syncSelection();
    onPreview(displayEvents(rows, style.mode), style);
  }

  function changed(cue, part, inputs = true) {
    selected = cue ? { cue, span: { part } } : null;
    message.textContent = "";
    paint(inputs);
    onEdit();
  }

  select.addEventListener("change", () => choose(select.value === "" ? null : rows[Number(select.value)], true));
  prevButton.addEventListener("click", () => choose(rows[rows.indexOf(selected) - 1], true));
  nextButton.addEventListener("click", () => choose(rows[rows.indexOf(selected) + 1], true));
  text.addEventListener("focus", () => { textCheckpoint = false; preservedWords = null; });
  text.addEventListener("input", () => {
    if (!selected) return;
    const { part } = selected.span;
    if (!textCheckpoint) {
      checkpoint(part);
      preservedWords = selected.cue.words;
      textCheckpoint = true;
    }
    const cue = editable(selected);
    cue.text = text.value;
    const tokens = cue.text.trim().split(/\s+/).filter(Boolean);
    if (preservedWords?.length === tokens.length) {
      cue.words = preservedWords.map((word, index) => ({ ...word, text: tokens[index] }));
    } else {
      delete cue.words;
    }
    changed(cue, part, false);
  });

  function editTimes() {
    if (!selected || from.value === "" || to.value === "") return;
    const { span } = selected;
    const start = round(span.start + Number(from.value) - span.offset);
    const end = round(span.start + Number(to.value) - span.offset);
    if (start < span.start - .01 || end > span.end + .01 || end - start < .08) {
      message.textContent = "Блок должен оставаться внутри своего видеоотрезка.";
      details();
      return;
    }
    retime(selected, Math.max(span.start, start), Math.min(span.end, end));
  }

  function retime(row, start, end) {
    const { part } = row.span;
    checkpoint(part);
    const cue = editable(row);
    const originalStart = cue.start;
    const originalLength = cue.end - cue.start;
    cue.start = start;
    cue.end = end;
    if (cue.words?.length && originalLength > 0) {
      const mapTime = (time) => cue.start + (cue.end - cue.start) * (time - originalStart) / originalLength;
      cue.words = cue.words.map((word) => ({ ...word, start: mapTime(word.start), end: mapTime(word.end) }));
    }
    changed(cue, part);
  }

  function trimCue(event, handle, block, row, edge, runtime) {
    if (event.button !== 0) return;
    event.preventDefault();
    const bounds = lane.getBoundingClientRect();
    const { span } = row;
    const lower = span.offset;
    const upper = span.offset + span.end - span.start;
    let start = row.start;
    let end = row.end;
    const toSource = (time) => span.start + time - span.offset;
    handle.setPointerCapture(event.pointerId);
    function move(change) {
      const time = round(runtime * (change.clientX - bounds.left) / bounds.width);
      if (edge === "start") start = Math.max(lower, Math.min(end - .08, time));
      else end = Math.min(upper, Math.max(start + .08, time));
      block.style.left = (100 * start / runtime) + "%";
      block.style.width = (100 * (end - start) / runtime) + "%";
    }
    function finish(change) {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", finish);
      handle.removeEventListener("pointercancel", finish);
      if (change.type === "pointerup" && Math.abs(change.clientX - event.clientX) >= 2) {
        move(change);
        retime(row, toSource(start), toSource(end));
      } else {
        block.style.left = (100 * row.start / runtime) + "%";
        block.style.width = (100 * (row.end - row.start) / runtime) + "%";
      }
    }
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", finish);
    handle.addEventListener("pointercancel", finish);
  }

  from.addEventListener("change", editTimes);
  to.addEventListener("change", editTimes);

  add.addEventListener("click", () => {
    const cursor = editor.cursor();
    const span = spans.find((span) => cursor < span.offset + span.end - span.start) ?? spans.at(-1);
    if (!span) return;
    const { part } = span;
    const cues = part.cues ?? transcript.filter((cue) => cue.start < span.end && cue.end > span.start);
    if (cues.length >= 160) {
      message.textContent = "В одном отрезке можно сохранить до 160 блоков.";
      return;
    }
    checkpoint(part);
    const start = Math.max(span.start, Math.min(span.end - .25, span.start + cursor - span.offset));
    const cue = { start, end: Math.min(span.end, start + 1.75), text: "" };
    part.cues = [...cues.map((existing) => ({ ...existing })), cue];
    changed(cue, part);
    text.focus();
  });

  remove.addEventListener("click", () => {
    if (!selected) return;
    const { part } = selected.span;
    checkpoint(part);
    const cue = editable(selected);
    part.cues = part.cues.filter((entry) => entry !== cue);
    changed(null, part);
  });

  split.addEventListener("click", () => {
    if (!selected) return;
    const { span, start, end } = selected;
    const { part } = span;
    const original = wordsIn(selected.cue);
    let time = editor.cursor();
    if (time <= start + .08 || time >= end - .08) time = (start + end) / 2;
    const requested = span.start + time - span.offset;
    const midpoint = original.map((word, index) => index
      ? (original[index - 1].end + word.start) / 2 : NaN);
    const splits = midpoint.map((at, index) => ({ at, index })).filter(({ at }) =>
      at > span.start + .08 && at < span.end - .08 && at > selected.cue.start + .08 && at < selected.cue.end - .08);
    if (!splits.length) return;
    const { at: pivot, index: idx } = splits.reduce((best, entry) =>
      Math.abs(entry.at - requested) < Math.abs(best.at - requested) ? entry : best);
    checkpoint(part);
    const cue = editable(selected);
    const right = { text: original.slice(idx).map((word) => word.text).join(" "), start: pivot, end: cue.end };
    cue.end = pivot;
    cue.text = original.slice(0, idx).map((word) => word.text).join(" ");
    if (cue.words?.length) {
      cue.words = original.slice(0, idx).map((word) => ({ ...word, end: Math.min(word.end, pivot) }));
      right.words = original.slice(idx).map((word) => ({ ...word, start: Math.max(word.start, pivot) }));
    }
    part.cues.push(right);
    changed(right, part);
  });

  merge.addEventListener("click", () => {
    const next = nextCue();
    if (!next) return;
    const { part } = selected.span;
    checkpoint(part);
    let following = next.cue;
    if (part.cues === null) {
      const auto = transcript.filter((cue) => cue.start < selected.span.end && cue.end > selected.span.start);
      const nextIndex = auto.indexOf(next.cue);
      const cue = editable(selected);
      following = part.cues[nextIndex];
      selected = { ...selected, cue };
    }
    const first = selected.cue;
    const joined = (first.text + " " + following.text).trim();
    const combined = [...wordsIn(first), ...wordsIn(following)];
    first.text = joined.slice(0, 600);
    first.end = Math.max(first.end, following.end);
    if (joined.length <= 600 && combined.length <= 160 &&
        combined.every((word, index) => !index || word.start >= combined[index - 1].end)) {
      first.words = combined;
    } else {
      delete first.words;
    }
    part.cues = part.cues.filter((cue) => cue !== following);
    changed(first, part);
  });

  reset.addEventListener("click", () => {
    const part = editor.currentPart();
    if (!part || part.cues === null) return;
    checkpoint(part);
    part.cues = null;
    changed(null, part);
    find('[data-caption-panel="text"]').click();
    message.textContent = "Автосубтитры восстановлены для этого отрезка. Действие можно отменить.";
  });

  undo.addEventListener("click", () => {
    let entry;
    while (history.length && !entry) {
      const candidate = history.pop();
      if (editor.segments.includes(candidate.part)) entry = candidate;
    }
    if (!entry) return;
    entry.part.cues = entry.before;
    changed(null, entry.part);
  });

  root.querySelectorAll("[data-caption-style]").forEach((field) => {
    const key = field.dataset.captionStyle;
    if (field.type === "checkbox") field.checked = style[key];
    else field.value = style[key];
    field.addEventListener(field.type === "color" ? "input" : "change", () => {
      if (!field.checkValidity() || field.value === "") {
        field.value = style[key];
        return;
      }
      style[key] = field.type === "checkbox" ? field.checked
        : field.type === "number" ? Number(field.value) : field.value;
      onPreview(displayEvents(rows, style.mode), style);
      onEdit();
    });
  });

  paint();
  return {
    paint, style,
    transcript: (value) => { transcript = value; paint(); },
    position() {
      const available = editor.currentPart()?.cues;
      reset.disabled = available === null || available === undefined;
    },
    addFont(item) {
      addFontOption(item);
      font.value = item.id;
      font.dispatchEvent(new Event("change"));
    },
  };
}

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
    const words = mode === "phrase" ? [cue] : wordsIn(cue);
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
    history.push({ part, before: part.cues === null ? null : structuredClone(part.cues), selected });
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
      button.addEventListener("click", () => choose(row, true));
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
  text.addEventListener("focus", () => { textCheckpoint = false; });
  text.addEventListener("input", () => {
    if (!selected) return;
    const { part } = selected.span;
    if (!textCheckpoint) {
      checkpoint(part);
      textCheckpoint = true;
    }
    const cue = editable(selected);
    cue.text = text.value;
    delete cue.words;
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
    checkpoint(span.part);
    const cue = editable(selected);
    cue.start = Math.max(span.start, start);
    cue.end = Math.min(span.end, end);
    delete cue.words;
    changed(cue, span.part);
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
    const pivot = span.start + time - span.offset;
    let idx = original.findIndex((word) => word.start >= pivot);
    if (idx < 1 || idx >= original.length) idx = Math.ceil(original.length / 2);
    checkpoint(part);
    const cue = editable(selected);
    const right = { text: original.slice(idx).map((word) => word.text).join(" "), start: pivot, end: cue.end };
    cue.end = pivot;
    cue.text = original.slice(0, idx).map((word) => word.text).join(" ");
    delete cue.words;
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
    first.text = (first.text + " " + following.text).trim().slice(0, 600);
    first.end = Math.max(first.end, following.end);
    delete first.words;
    part.cues = part.cues.filter((cue) => cue !== following);
    changed(first, part);
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
    addFont(item) {
      addFontOption(item);
      font.value = item.id;
      font.dispatchEvent(new Event("change"));
    },
  };
}

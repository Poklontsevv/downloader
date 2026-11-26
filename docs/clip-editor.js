const MAX_SEGMENTS = 12;
const seconds = (value) => value === "" ? NaN : Number(value);
const round = (value) => Math.round(value * 1000) / 1000;

function clock(value) {
  const tenths = Math.floor(Math.max(0, value) * 10 + 1e-6);
  const minutes = Math.floor(tenths / 600);
  const remainder = String(Math.floor(tenths / 10) % 60).padStart(2, "0");
  return `${minutes}:${remainder}${tenths % 10 ? "," + tenths % 10 : ""}`;
}

export function mountClipEditor(card, clip, { duration, getTranscript, player, isCurrent, onEdit }) {
  const find = (selector) => card.querySelector(selector);
  const editor = find(".clip-editor");
  const track = find(".montage-track");
  const seek = find(".montage-seek");
  const rows = find(".montage-segments");
  const layout = find(".clip-mode");
  const split = find(".montage-split");
  const add = find(".montage-add");
  const transcriptPanel = find(".montage-transcript");
  const search = find(".montage-search");
  const results = find(".montage-transcript-results");
  const transcriptState = find(".montage-transcript-state");
  const template = document.getElementById("montage-segment-template");
  const segments = (clip.segments || [clip]).map((part) => ({
    start: part.start, end: part.end, captions: part.captions || "", autoCaptions: true,
  }));
  let cursor = 0;
  let dragged = null;
  let transcript;
  let transcriptLoading = false;

  const length = (part) => Math.max(0, seconds(part.end) - seconds(part.start)) || 0;
  const total = () => segments.reduce((sum, part) => sum + length(part), 0);
  const offset = (index) => segments.slice(0, index).reduce((sum, part) => sum + length(part), 0);
  const partAt = () => segments.findIndex((part, index) => cursor < offset(index) + length(part));

  function setCursor(time, seekPlayer = false) {
    cursor = Math.max(0, Math.min(total(), time));
    seek.value = cursor;
    find(".montage-position").value = clock(cursor);
    const activeIndex = partAt();
    [...track.children].forEach((button, index) => {
      button.setAttribute("aria-pressed", String(index === activeIndex));
    });
    const splitOffset = cursor - offset(activeIndex);
    split.disabled = activeIndex < 0 || segments.length >= MAX_SEGMENTS
      || splitOffset < 1 || length(segments[activeIndex]) - splitOffset < 1;
    if (seekPlayer && isCurrent() && player.readyState) {
      player.currentTime = cursor;
    }
  }

  function paintTrack() {
    const runtime = total();
    seek.max = runtime;
    seek.disabled = runtime <= 0;
    find(".montage-duration").value = clock(runtime);
    find(".clip-total").textContent = `${segments.length} отр. · ${clock(runtime)}`;
    add.disabled = segments.length >= MAX_SEGMENTS || duration < 1;
    track.replaceChildren(...segments.map((part, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "montage-cut";
      button.style.flexGrow = Math.max(1, length(part));
      button.draggable = segments.length > 1;
      button.textContent = index + 1;
      const label = `Отрезок ${index + 1}, ${clock(offset(index))} в монтаже`;
      button.title = label;
      button.setAttribute("aria-label", label);
      button.addEventListener("click", () => {
        setCursor(offset(index), true);
        rows.children[index].querySelector(".clip-start").focus();
      });
      button.addEventListener("dragstart", (event) => {
        dragged = index;
        event.dataTransfer.setData("text/plain", String(index));
        event.dataTransfer.effectAllowed = "move";
      });
      button.addEventListener("dragend", () => { dragged = null; });
      button.addEventListener("dragover", (event) => {
        if (dragged !== null) event.preventDefault();
      });
      button.addEventListener("drop", (event) => {
        event.preventDefault();
        if (dragged !== null) move(dragged, index);
        dragged = null;
      });
      return button;
    }));
    setCursor(cursor);
  }

  function changed(structure = false) {
    if (structure) {
      paintRows();
      paintTranscript();
    }
    paintTrack();
    onEdit();
  }

  function move(from, to) {
    if (from === to) return;
    segments.splice(to, 0, segments.splice(from, 1)[0]);
    cursor = offset(to);
    changed(true);
    track.children[to].focus();
  }

  function paintRows() {
    rows.replaceChildren(...segments.map((part, index) => {
      const row = template.content.firstElementChild.cloneNode(true);
      const query = (selector) => row.querySelector(selector);
      query(".montage-segment-number").textContent = `Отрезок ${index + 1}`;
      for (const field of ["start", "end"]) {
        const input = query(".clip-" + field);
        input.value = part[field];
        input.max = duration;
        input.addEventListener("input", () => {
          part[field] = input.value;
          changed();
        });
      }
      const captions = query(".clip-captions");
      captions.value = part.captions;
      captions.addEventListener("input", () => {
        part.captions = captions.value;
        part.autoCaptions = false;
        onEdit();
      });
      query(".montage-captions").addEventListener("toggle", async (event) => {
        if (!event.target.open || !part.autoCaptions) return;
        try {
          const cues = await getTranscript();
          if (!part.autoCaptions) return;
          captions.value = cues.filter((cue) => cue.start < seconds(part.end) && cue.end > seconds(part.start))
            .map((cue) => cue.text).join("\n");
        } catch {
          // The captions already loaded with the proposal remain editable.
        }
      });
      const earlier = query(".montage-earlier");
      const later = query(".montage-later");
      const remove = query(".montage-remove");
      earlier.disabled = index === 0;
      later.disabled = index === segments.length - 1;
      remove.disabled = segments.length <= 1;
      earlier.setAttribute("aria-label", `Переместить отрезок ${index + 1} раньше`);
      later.setAttribute("aria-label", `Переместить отрезок ${index + 1} позже`);
      remove.setAttribute("aria-label", `Удалить отрезок ${index + 1}`);
      earlier.addEventListener("click", () => move(index, index - 1));
      later.addEventListener("click", () => move(index, index + 1));
      remove.addEventListener("click", () => {
        segments.splice(index, 1);
        changed(true);
        track.children[Math.min(index, segments.length - 1)].focus();
      });
      return row;
    }));
  }

  function append(start, end, captions = "") {
    if (segments.length >= MAX_SEGMENTS) return;
    cursor = total();
    segments.push({ start: round(start), end: round(end), captions, autoCaptions: true });
    changed(true);
    rows.lastElementChild.querySelector(".clip-start").focus();
  }

  function paintTranscript() {
    if (!transcript) return;
    const term = search.value.trim().toLocaleLowerCase();
    const matches = transcript.filter((cue) => cue.text.toLocaleLowerCase().includes(term));
    transcriptState.textContent = transcript.length === 0 ? "Распознанной речи нет."
      : matches.length === 0 ? "Реплики не найдены."
      : matches.length > 20 ? `Показано 20 из ${matches.length}. Уточните поиск.` : "";
    results.replaceChildren(...matches.slice(0, 20).map((cue) => {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button secondary";
      button.textContent = `${clock(cue.start)} · ${cue.text}`;
      button.disabled = segments.length >= MAX_SEGMENTS || duration < 1;
      button.addEventListener("click", () => {
        const start = Math.max(0, Math.min(cue.start - 0.2, duration - 1));
        append(start, Math.min(duration, Math.max(start + 1, cue.end + 0.3)), cue.text);
      });
      row.append(button);
      return row;
    }));
  }

  seek.addEventListener("input", () => setCursor(Number(seek.value), true));
  add.addEventListener("click", () => {
    const end = Math.min(duration, seconds(segments.at(-1).end) + 6) || Math.min(duration, 6);
    append(Math.max(0, end - 6), end);
  });
  split.addEventListener("click", () => {
    const index = partAt();
    const part = segments[index];
    const pivot = round(seconds(part.start) + cursor - offset(index));
    const right = { ...part, start: pivot };
    if (!part.autoCaptions) {
      const lines = part.captions.split(/\r?\n/);
      const count = Math.round(lines.length * (pivot - seconds(part.start)) / length(part));
      part.captions = lines.slice(0, count).join("\n");
      right.captions = lines.slice(count).join("\n");
    }
    part.end = pivot;
    segments.splice(index + 1, 0, right);
    changed(true);
    track.children[index + 1].focus();
  });
  layout.addEventListener("change", onEdit);
  search.addEventListener("input", paintTranscript);
  transcriptPanel.addEventListener("toggle", async () => {
    if (!transcriptPanel.open || transcript || transcriptLoading) return;
    transcriptLoading = true;
    transcriptState.textContent = "Загрузка расшифровки…";
    try {
      transcript = await getTranscript();
      paintTranscript();
    } catch (error) {
      transcriptState.textContent = error.message;
    } finally {
      transcriptLoading = false;
    }
  });

  paintRows();
  paintTrack();
  return {
    value: () => ({ layout: layout.value, segments: segments.map((part) => ({
      ...part, start: seconds(part.start), end: seconds(part.end),
    })) }),
    setBusy: (busy) => { editor.inert = busy; },
    playback: (time) => { if (isCurrent()) setCursor(time); },
  };
}

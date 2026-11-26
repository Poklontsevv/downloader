const MAX_SEGMENTS = 12;
const seconds = (value) => value === "" ? NaN : Number(value);
const round = (value) => Math.round(value * 1000) / 1000;

export function clock(value) {
  const tenths = Math.floor(Math.max(0, value) * 10 + 1e-6);
  const minutes = Math.floor(tenths / 600);
  const remainder = String(Math.floor(tenths / 10) % 60).padStart(2, "0");
  return `${minutes}:${remainder}${tenths % 10 ? "," + tenths % 10 : ""}`;
}

export function mountClipEditor(card, clip, { duration, getTranscript, onSeek, onEdit }) {
  const find = (selector) => card.querySelector(selector);
  const track = find(".montage-track");
  const ruler = find(".montage-ruler");
  const scroll = find(".timeline-scroll");
  const content = find(".timeline-content");
  const head = find(".timeline-playhead");
  const zoom = find(".montage-zoom");
  const openCuts = () => find('[data-editor-panel="cuts"]').click();
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
    start: part.start, end: part.end, cues: part.cues ?? null,
  }));
  let cursor = 0;
  let selected = 0;
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
    const x = total() ? cursor / total() * 100 : 0;
    head.style.left = x + "%";
    [...track.children].forEach((block, index) => block.classList.toggle("playing", index === activeIndex));
    const pixel = content.clientWidth * x / 100;
    if (pixel < scroll.scrollLeft || pixel > scroll.scrollLeft + scroll.clientWidth - 14) {
      scroll.scrollLeft = pixel - scroll.clientWidth / 2;
    }
    const splitOffset = cursor - offset(activeIndex);
    split.disabled = activeIndex < 0 || segments.length >= MAX_SEGMENTS
      || splitOffset < 1 || length(segments[activeIndex]) - splitOffset < 1;
    if (seekPlayer) onSeek(cursor);
  }

  function select(index, go = false) {
    selected = index;
    [...track.children].forEach((block, entry) =>
      block.querySelector(".montage-cut").setAttribute("aria-pressed", String(entry === index)));
    [...rows.children].forEach((row, entry) => { row.hidden = entry !== index; });
    if (go) {
      openCuts();
      setCursor(offset(index), true);
    }
  }

  function paintRuler() {
    const runtime = total();
    const step = [0.5, 1, 2, 5, 10, 15, 30, 60].find((value) => value * content.clientWidth / runtime >= 73) || 60;
    const ticks = [];
    for (let time = 0; time < runtime; time += step) {
      const tick = document.createElement("span");
      tick.className = "timeline-tick";
      tick.style.left = (100 * time / runtime) + "%";
      tick.textContent = clock(time);
      ticks.push(tick);
    }
    ruler.replaceChildren(...ticks);
  }

  function trim(part, index, edge, runtime, block) {
    const grip = document.createElement("button");
    grip.type = "button";
    grip.className = "trim-grip trim-" + edge;
    const label = edge === "start" ? "Подрезать начало" : "Подрезать конец";
    grip.title = label;
    grip.setAttribute("aria-label", label + " отрезка " + (index + 1));
    grip.addEventListener("click", () => {
      select(index, true);
      rows.children[index].querySelector(".clip-" + edge).focus();
    });
    grip.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !track.clientWidth) return;
      event.preventDefault();
      grip.setPointerCapture(event.pointerId);
      const origin = event.clientX;
      const old = seconds(part[edge]);
      const scale = runtime / track.clientWidth;
      const extra = Math.max(0, 180 - runtime);
      const low = edge === "start" ? Math.max(0, old - extra) : seconds(part.start) + 1;
      const high = edge === "start" ? seconds(part.end) - 1 : Math.min(duration, old + extra);
      let next = old;
      grip.onpointermove = (drag) => {
        next = round(Math.max(low, Math.min(high, old + (drag.clientX - origin) * scale)));
        const delta = (next - old) * 100 / runtime;
        block.style.width = (length(part) * 100 / runtime + (edge === "start" ? -delta : delta)) + "%";
        block.style.left = (offset(index) * 100 / runtime + (edge === "start" ? delta : 0)) + "%";
        block.classList.add("trimming");
      };
      grip.onpointerup = grip.onpointercancel = (release) => {
        grip.onpointermove = grip.onpointerup = grip.onpointercancel = null;
        if (release.type === "pointerup" && next !== old) {
          part[edge] = next;
          selected = index;
          cursor = offset(index);
          changed(true);
          track.children[index].querySelector(".montage-cut").focus();
        } else {
          paintTrack();
        }
      };
    });
    return grip;
  }

  function paintTrack() {
    const runtime = total();
    seek.max = runtime;
    seek.disabled = runtime <= 0;
    find(".montage-duration").value = clock(runtime);
    add.disabled = segments.length >= MAX_SEGMENTS || duration < 1;
    track.replaceChildren(...segments.map((part, index) => {
      const block = document.createElement("div");
      block.className = "timeline-cut";
      block.style.left = (100 * offset(index) / runtime) + "%";
      block.style.width = (100 * length(part) / runtime) + "%";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "montage-cut";
      button.draggable = segments.length > 1;
      button.textContent = `Отрезок ${index + 1} · ${clock(length(part))}`;
      const label = `Отрезок ${index + 1}, ${clock(offset(index))} в монтаже. Исходник ${clock(seconds(part.start))}–${clock(seconds(part.end))}`;
      button.title = label;
      button.setAttribute("aria-label", label);
      button.addEventListener("click", () => select(index, true));
      button.addEventListener("dragstart", (event) => {
        dragged = index;
        event.dataTransfer.setData("text/plain", String(index));
        event.dataTransfer.effectAllowed = "move";
      });
      button.addEventListener("dragend", () => {
        dragged = null;
        track.querySelectorAll(".drop-target").forEach((item) => item.classList.remove("drop-target"));
      });
      block.addEventListener("dragover", (event) => {
        if (dragged === null || dragged === index) return;
        event.preventDefault();
        block.classList.add("drop-target");
      });
      block.addEventListener("dragleave", (event) => {
        if (!block.contains(event.relatedTarget)) block.classList.remove("drop-target");
      });
      block.addEventListener("drop", (event) => {
        event.preventDefault();
        if (dragged !== null) move(dragged, index);
        dragged = null;
      });
      button.addEventListener("keydown", (event) => {
        if (event.key === "Delete" && segments.length > 1) {
          event.preventDefault();
          deletePart(index);
        } else if (event.altKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) {
          event.preventDefault();
          move(index, Math.min(segments.length - 1, Math.max(0, index + (event.key === "ArrowLeft" ? -1 : 1))));
        }
      });
      block.append(button, trim(part, index, "start", runtime, block), trim(part, index, "end", runtime, block));
      return block;
    }));
    select(selected);
    paintRuler();
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
    selected = to;
    cursor = offset(to);
    changed(true);
    track.children[to].querySelector(".montage-cut").focus();
  }

  function deletePart(index) {
    segments.splice(index, 1);
    selected = Math.min(index, segments.length - 1);
    cursor = offset(selected);
    changed(true);
    track.children[selected].querySelector(".montage-cut").focus();
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
        input.addEventListener("change", () => {
          const start = field === "start" ? seconds(input.value) : seconds(part.start);
          const end = field === "end" ? seconds(input.value) : seconds(part.end);
          const runtime = total() - length(part) + end - start;
          if (!input.checkValidity() || !(end - start >= 1 - .000001) || runtime > 180.000001) {
            input.value = part[field];
            input.setCustomValidity("Отрезок должен длиться от секунды, весь клип — до трёх минут.");
            input.reportValidity();
            input.setCustomValidity("");
            return;
          }
          part[field] = input.value;
          changed();
        });
      }
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
      remove.addEventListener("click", () => deletePart(index));
      return row;
    }));
  }

  function append(start, end) {
    if (segments.length >= MAX_SEGMENTS) return;
    end = Math.min(end, start + 180 - total());
    if (end - start < 1 - .000001) return;
    cursor = total();
    selected = segments.length;
    segments.push({ start: round(start), end: round(end), cues: null });
    changed(true);
    openCuts();
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
        append(start, Math.min(duration, Math.max(start + 1, cue.end + 0.3)));
      });
      row.append(button);
      return row;
    }));
  }

  seek.addEventListener("input", () => setCursor(Number(seek.value), true));
  zoom.addEventListener("input", () => {
    content.style.width = (100 * Number(zoom.value)) + "%";
    paintRuler();
    setCursor(cursor);
  });
  function scrub(event) {
    const bounds = ruler.getBoundingClientRect();
    if (bounds.width) setCursor((event.clientX - bounds.left) / bounds.width * total(), true);
  }
  ruler.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    ruler.setPointerCapture(event.pointerId);
    scrub(event);
  });
  ruler.addEventListener("pointermove", (event) => {
    if (ruler.hasPointerCapture(event.pointerId)) scrub(event);
  });
  card.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select, summary") || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.code === "KeyB") {
      event.preventDefault();
      split.click();
    } else if (event.code === "Space" && !event.target.matches("button")) {
      event.preventDefault();
      find(".editor-play").click();
    }
  });
  add.addEventListener("click", () => {
    const end = Math.min(duration, seconds(segments.at(-1).end) + 6) || Math.min(duration, 6);
    append(Math.max(0, end - 6), end);
  });
  split.addEventListener("click", () => {
    const index = partAt();
    const part = segments[index];
    const pivot = round(seconds(part.start) + cursor - offset(index));
    const right = { ...part, start: pivot, cues: part.cues && structuredClone(part.cues) };
    part.end = pivot;
    segments.splice(index + 1, 0, right);
    selected = index + 1;
    changed(true);
    track.children[selected].querySelector(".montage-cut").focus();
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

  layout.value = clip.layout || "crop";
  paintRows();
  paintTrack();
  return {
    value: () => ({ layout: layout.value, segments: segments.map((part) => ({
      ...part, start: seconds(part.start), end: seconds(part.end),
    })) }),
    segments,
    refreshTranscript: (rows) => { transcript = rows; paintTranscript(); },
    cursor: () => cursor,
    seek: (time) => setCursor(time, true),
    playback: (time) => setCursor(time),
  };
}

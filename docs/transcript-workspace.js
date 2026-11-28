import { byId, button, element, feedback, icon, setOptions, timecode } from "./studio-ui.js";

const STATES = { queued: "В очереди", running: "В работе", pausing: "Пауза после текущей части", paused: "На паузе",
  canceled: "Отменено", canceling: "Останавливаем после текущей части", error: "Ошибка", ready: "Готово" };
const ACTIVE = new Set(["queued", "running", "pausing", "canceling"]);

export function setupTranscript(api) {
  const page = byId("transcript-main");
  const notice = byId("tr-feedback");
  const modal = byId("tr-action-dialog");
  const importDialog = byId("tr-import-dialog");
  const player = byId("tr-player");
  const editor = byId("tr-editor");
  const search = byId("tr-search");
  const fullText = byId("tr-full-text-content");
  const fullStatus = byId("tr-full-text-status");
  const play = byId("tr-play");
  const seek = byId("tr-seek");
  const selected = new Set();
  let jobs = [];
  let translations = {};
  let id = "";
  let data;
  let files = [];
  let currentCue;
  let pageIndex = 0;
  let polling;
  let detailRevision = 0;
  let listRevision = 0;
  let cueSignature;
  let onAction;
  let outputFolder;
  let view = "cues";
  let cachedText;
  const fail = (error) => feedback(notice, error.message, true);
  const path = () => "transcript/" + id;

  function paintLibrary() {
    const folder = byId("tr-folder-filter");
    const folders = [...new Set(jobs.map((job) => job.folder).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru"));
    setOptions(folder, [["*", "Все папки"], ["", "Без папки"], ...folders.map((value) => [value, value])], folder.selectedIndex < 0 ? "*" : folder.value);
    const query = byId("tr-library-search").value.trim().toLocaleLowerCase("ru");
    const shown = jobs.filter((job) => (folder.value === "*" || folder.value === job.folder)
      && job.name.toLocaleLowerCase("ru").includes(query));
    const root = byId("tr-jobs");
    const signature = JSON.stringify(shown.map(({ id: key, name, state }) => [key, name, state, selected.has(key), id === key]));
    if (root.dataset.signature !== signature) {
      root.dataset.signature = signature;
      root.replaceChildren(...shown.map((job) => {
        const row = element("li");
        const check = element("input");
        check.type = "checkbox";
        check.checked = selected.has(job.id);
        check.setAttribute("aria-label", "Выбрать " + job.name);
        check.addEventListener("change", () => {
          if (check.checked) selected.add(job.id);
          else selected.delete(job.id);
          byId("tr-bulk").disabled = !selected.size;
        });
        const item = button("", () => open(job.id), "secondary");
        item.title = job.name;
        item.setAttribute("aria-current", String(job.id === id));
        item.append(element("strong", job.name), element("small", STATES[job.state]));
        row.append(check, item);
        return row;
      }));
    }
    byId("tr-bulk").disabled = !selected.size;
  }

  async function refresh() {
    clearTimeout(polling);
    if (page.hidden) return;
    const revision = ++listRevision;
    try {
      const result = await api("transcript");
      if (revision !== listRevision || page.hidden) return;
      jobs = result.jobs;
      translations = result.translations;
      const languages = new Intl.DisplayNames(["ru"], { type: "language", fallback: "code" });
      setOptions(byId("tr-language"), [["auto", "Определить автоматически"],
        ...result.languages.map((key) => [key, languages.of(key)]).sort((a, b) => a[1].localeCompare(b[1], "ru"))]);
      setOptions(byId("tr-downloads"), result.downloads.filter((row) => !/\.(jpeg|jpg|png|webp|avif)$/i.test(row.name))
        .map((row) => [row.id, row.name]));
      paintLibrary();
      if (id) await refreshDetail();
      else if (jobs.length) await open(jobs[0].id);
    } catch (error) {
      fail(error);
    }
    if (!page.hidden) polling = setTimeout(refresh, jobs.some((job) => ACTIVE.has(job.state)) ? 1500 : 5000);
  }

  async function open(key) {
    id = key;
    data = null;
    pageIndex = 0;
    search.value = "";
    currentCue = null;
    cachedText = undefined;
    fullText.textContent = "";
    editor.hidden = true;
    feedback(notice);
    paintLibrary();
    try {
      await refreshDetail();
    } catch (error) {
      fail(error);
    }
  }

  async function refreshDetail() {
    const revision = ++detailRevision;
    const payload = await api(path() + "?page=" + pageIndex + "&q=" + encodeURIComponent(search.value.trim()));
    if (revision !== detailRevision || page.hidden) return;
    if (data?.job.state !== payload.job.state || data?.job.cursor !== payload.job.cursor) cachedText = undefined;
    data = payload;
    pageIndex = data.page;
    render();
    if (view === "text") await paintFullText(revision);
  }

  async function paintFullText(revision) {
    if (cachedText === undefined || ACTIVE.has(data.job.state)) {
      if (cachedText === undefined) feedback(fullStatus, "Загружаем текст…");
      try {
        const { text } = await api(path() + "?view=text");
        if (revision !== detailRevision || view !== "text" || page.hidden) return;
        cachedText = text;
      } catch (error) {
        if (revision === detailRevision) feedback(fullStatus, error.message, true);
        return;
      }
    }
    if (fullText.textContent !== cachedText) fullText.textContent = cachedText;
    feedback(fullStatus, cachedText ? "" : "Распознанного текста пока нет.");
  }

  function render() {
    const { job, rows, count, pageSize, voices } = data;
    const busy = ACTIVE.has(job.state);
    byId("tr-empty").hidden = true;
    byId("tr-work").hidden = false;
    byId("tr-title").textContent = job.name;
    const total = job.length ? Math.round(100 * job.cursor / job.length) : null;
    byId("tr-status").textContent = [job.state === "ready" ? "" : STATES[job.state],
      job.length ? job.state === "ready" ? timecode(job.length) : `${timecode(job.cursor)} / ${timecode(job.length)}` : "",
      job.note, job.error].filter(Boolean).join(" · ");
    const progress = byId("tr-progress");
    progress.hidden = !busy && job.state !== "paused";
    if (total === null) progress.removeAttribute("value");
    else progress.value = total;
    const enabled = {
      pause: ["queued", "running"], resume: ["paused", "canceled", "error"], cancel: ["queued", "running", "paused", "pausing"],
    };
    for (const control of page.querySelectorAll("[data-tr-action]")) {
      control.hidden = !enabled[control.dataset.trAction].includes(job.state);
    }
    byId("tr-delete").hidden = busy;
    byId("tr-translate").disabled = busy || !count;
    byId("tr-replace").disabled = busy;
    byId("tr-add-cue").disabled = busy || !job.length;
    byId("tr-export").disabled = !job.length;
    byId("tr-transport").hidden = !job.media;
    paintPlayer();
    const source = job.media ? "/transcript-media/" + job.id : "";
    if ((player.getAttribute("src") || "") !== source) {
      byId("tr-player-error").hidden = true;
      player.pause();
      if (source) player.src = source;
      else player.removeAttribute("src");
      player.load();
    }
    if (busy) editor.hidden = true;
    byId("tr-voices").replaceChildren(...voices.map((voice) => new Option(voice)));
    const signature = JSON.stringify([job.id, rows, job.speakers, busy, currentCue?.id]);
    if (cueSignature !== signature) {
      cueSignature = signature;
      byId("tr-cues").replaceChildren(...rows.map((row) => {
        const li = element("li");
        li.dataset.start = row.start;
        li.dataset.end = row.end;
        li.dataset.selected = String(currentCue?.id === row.id);
        const seek = button(timecode(row.start), () => {
          if (player.readyState) player.currentTime = row.start;
        });
        seek.classList.add("tr-time");
        seek.setAttribute("aria-label", "Перейти к " + timecode(row.start));
        const edit = button(row.text || "Пустая реплика", () => pick(row));
        edit.classList.add("tr-text-button");
        edit.disabled = busy;
        if (row.speaker) edit.prepend(element("small", job.speakers[row.speaker] || row.speaker));
        li.append(seek, edit);
        return li;
      }));
    }
    feedback(byId("tr-empty-cues"), rows.length ? "" : search.value ? "Совпадений нет" : busy ? "Реплики появятся по мере обработки." : "Реплик пока нет.");
    byId("tr-page").value = count ? `${pageIndex + 1} из ${Math.ceil(count / pageSize)} · ${count} реплик` : "";
    byId("tr-prev").disabled = pageIndex === 0;
    byId("tr-next").disabled = (pageIndex + 1) * pageSize >= count;
  }

  function pick(row) {
    currentCue = row;
    for (const [index, cue] of [...byId("tr-cues").children].entries()) {
      cue.dataset.selected = String(data.rows[index]?.id === row.id);
    }
    editor.hidden = false;
    byId("tr-text").value = row.text;
    byId("tr-start").value = Number(row.start.toFixed(3));
    byId("tr-end").value = Number(row.end.toFixed(3));
    byId("tr-speaker").value = row.speaker;
    byId("tr-rename-voice").disabled = !row.speaker;
    for (const control of editor.querySelectorAll("[data-tr-edit]")) control.disabled = !row.id;
    byId("tr-text").focus();
  }

  async function act(action, values = {}) {
    await api(path(), "POST", { action, ...values });
    if (action === "delete") {
      selected.delete(id);
      id = "";
      data = null;
      byId("tr-empty").hidden = false;
      byId("tr-work").hidden = true;
      player.pause();
    }
    cachedText = undefined;
    await refresh();
  }

  async function edit(action, values) {
    await api(path() + "/edit", "POST", { action, ...values });
    cachedText = undefined;
    currentCue = null;
    editor.hidden = true;
    await refreshDetail();
    feedback(notice, "Правка сохранена.");
  }

  function input(label, key, value = "", choices) {
    const wrapper = element("label", label);
    const field = element(choices ? "select" : "input");
    if (choices) setOptions(field, choices, value);
    else field.value = value;
    field.name = key;
    wrapper.append(field);
    return wrapper;
  }

  function actionDialog(title, label, children, perform) {
    byId("tr-action-title").textContent = title;
    byId("tr-action-submit").textContent = label;
    byId("tr-action-fields").replaceChildren(...children);
    byId("tr-action-status").textContent = "";
    onAction = perform;
    modal.showModal();
  }

  function exportDialog(ids) {
    actionDialog("Экспорт расшифровки", "Сохранить файлы", [
      input("Формат", "kind", "srt", [["srt", "SRT · субтитры"], ["vtt", "VTT · субтитры"], ["txt", "TXT · текст"],
        ["docx", "DOCX · документ Word"], ["pdf", "PDF · документ"], ["csv", "CSV · таблица"]]),
      input("Включить говорящих", "speakers", "yes", [["yes", "Да"], ["no", "Нет"]]),
      input("Таймкоды в тексте", "timestamps", view === "text" ? "no" : "yes", [["yes", "Да"], ["no", "Нет"]]),
    ], async (values) => {
      const folder = await window.chooseVideoportFolder(outputFolder || "");
      if (!folder) return false;
      outputFolder = folder;
      byId("tr-action-status").textContent = "Сохраняем файлы…";
      const result = await api("transcript/export", "POST", { ids, folder, kind: values.kind,
        timestamps: values.timestamps === "yes", speakers: values.speakers === "yes" });
      feedback(notice, `Сохранено файлов: ${result.files.length}`);
      notice.append(" ", button("Открыть папку", () => window.openVideoportDownloads(folder).catch(fail)));
    });
  }

  byId("tr-action-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = byId("tr-action-submit");
    submit.disabled = true;
    try {
      if (await onAction(Object.fromEntries(new FormData(event.currentTarget))) !== false) modal.close();
    } catch (error) {
      byId("tr-action-status").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  byId("tr-import").addEventListener("click", () => {
    files = [];
    byId("tr-picked").value = "Файлы не выбраны";
    for (const option of byId("tr-downloads").options) option.selected = false;
    importDialog.showModal();
  });
  byId("tr-pick").addEventListener("click", async () => {
    try {
      files = await window.chooseVideoportRecordings();
      byId("tr-picked").value = files.length ? `Файлов выбрано: ${files.length}` : "Файлы не выбраны";
    } catch (error) {
      byId("tr-import-note").textContent = error.message;
    }
  });
  for (let i = 1; i <= 16; i++) byId("tr-recognize-speakers").add(new Option(String(i), String(i)));
  byId("tr-english").addEventListener("change", (event) => {
    if (event.target.checked && byId("tr-quality").value === "turbo") byId("tr-quality").value = "medium";
    byId("tr-import-note").textContent = event.target.checked ? "Перевод речи на английский доступен с Medium и Small." : "";
    byId("tr-quality").options[0].disabled = event.target.checked;
  });
  byId("tr-import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const submit = event.submitter;
    submit.disabled = true;
    try {
      const result = await api("transcript", "POST", { files,
        downloads: [...byId("tr-downloads").selectedOptions].map((item) => item.value),
        language: byId("tr-language").value, quality: byId("tr-quality").value, device: byId("tr-device").value,
        speakers: byId("tr-recognize-speakers").value, folder: byId("tr-folder").value,
        restore: byId("tr-restore").checked, english: byId("tr-english").checked });
      importDialog.close();
      await open(result.ids[0]);
      await refresh();
    } catch (error) {
      byId("tr-import-note").textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  });

  byId("tr-library-search").addEventListener("input", paintLibrary);
  byId("tr-folder-filter").addEventListener("change", paintLibrary);
  byId("tr-bulk").addEventListener("click", () => exportDialog([...selected]));
  byId("tr-export").addEventListener("click", () => exportDialog([id]));
  byId("tr-organize").addEventListener("click", () =>
    actionDialog("Название и папка", "Сохранить", [input("Название", "name", data.job.name), input("Папка", "folder", data.job.folder)],
      (values) => act("rename", values)));
  byId("tr-delete").addEventListener("click", () =>
    actionDialog("Удалить расшифровку?", "Удалить", [element("p", data.job.name + " · Исходный файл останется на диске.")],
      () => act("delete")));
  byId("tr-translate").addEventListener("click", () =>
    actionDialog("Перевод расшифровки", "Перевести", [
      input("Язык", "language", "en", Object.entries(translations)), element("p", "Перевод сохранится отдельной записью.", "studio-note"),
    ], async (values) => {
      const result = await api(path() + "/translate", "POST", values);
      await open(result.id);
      await refresh();
    }));
  byId("tr-replace").addEventListener("click", () =>
    actionDialog("Найти и заменить", "Заменить во всех репликах", [input("Найти", "query", search.value),
      input("Заменить на", "replacement")], (values) => edit("replace", values)));
  byId("tr-rename-voice").addEventListener("click", () => {
    const key = currentCue.speaker;
    actionDialog("Имя говорящего", "Применить", [input("Имя во всех репликах", "name", data.job.speakers[key] || key)],
      (values) => edit("speaker", { ...values, key }));
  });
  byId("tr-add-cue").addEventListener("click", () => {
    const start = Math.max(0, Math.min(player.currentTime || 0, data.job.length - .05));
    pick({ start, end: Math.min(data.job.length, start + 2), text: "", speaker: "" });
  });
  editor.addEventListener("submit", (event) => {
    event.preventDefault();
    edit(currentCue.id ? "save" : "add", { id: currentCue.id, text: byId("tr-text").value, speaker: byId("tr-speaker").value,
      start: byId("tr-start").value, end: byId("tr-end").value }).catch(fail);
  });
  for (const control of editor.querySelectorAll("[data-tr-edit]")) {
    control.addEventListener("click", () => {
      const row = currentCue;
      const action = control.dataset.trEdit;
      const split = () => {
        const match = byId("tr-text");
        const character = match.selectionStart > 0 && match.selectionStart < row.text.length
          ? match.selectionStart : Math.ceil(row.text.length / 2);
        const at = player.currentTime > row.start + .05 && player.currentTime < row.end - .05
          ? player.currentTime : (row.start + row.end) / 2;
        return edit("split", { id: row.id, at, character });
      };
      if (action === "split") split().catch(fail);
      else edit(action, { id: row.id }).catch(fail);
    });
  }
  for (const control of page.querySelectorAll("[data-tr-action]")) {
    control.addEventListener("click", () => act(control.dataset.trAction).catch(fail));
  }
  function paintPlayer() {
    const length = Number.isFinite(player.duration) ? player.duration : data?.job.length || 0;
    seek.max = length || 1;
    seek.value = player.currentTime;
    seek.disabled = !player.readyState;
    byId("tr-clock").value = timecode(player.currentTime).padStart(5, "0") + " / " + timecode(length).padStart(5, "0");
    seek.parentElement.style.setProperty("--played", (length ? player.currentTime / length * 100 : 0) + "%");
  }
  play.addEventListener("click", () => { if (player.paused) player.play().catch(fail); else player.pause(); });
  for (const event of ["play", "pause", "ended"]) player.addEventListener(event, () => {
    const label = player.paused ? "Воспроизвести" : "Пауза";
    play.replaceChildren(icon(player.paused ? "play" : "pause"));
    play.setAttribute("aria-label", label);
    play.title = label;
  });
  seek.addEventListener("input", () => { if (player.readyState) player.currentTime = Number(seek.value); });
  player.addEventListener("loadedmetadata", paintPlayer);
  player.addEventListener("error", () => { byId("tr-player-error").hidden = false; paintPlayer(); });
  player.addEventListener("timeupdate", () => {
    paintPlayer();
    for (const cue of byId("tr-cues").children) {
      cue.dataset.playing = String(Number(cue.dataset.start) <= player.currentTime && player.currentTime < Number(cue.dataset.end));
    }
  });
  for (const option of page.querySelectorAll("[data-tr-view]")) option.addEventListener("click", () => {
    view = option.dataset.trView;
    const plain = view === "text";
    for (const control of page.querySelectorAll("[data-tr-view]")) control.setAttribute("aria-pressed", String(control === option));
    byId("tr-full-text").hidden = !plain;
    for (const node of [byId("tr-edit-grid"), byId("tr-pagination"), search.closest("label"), byId("tr-add-cue")]) node.hidden = plain;
    if (plain && id && data) paintFullText(++detailRevision);
  });
  byId("tr-prev").addEventListener("click", () => { --pageIndex; refreshDetail().catch(fail); });
  byId("tr-next").addEventListener("click", () => { ++pageIndex; refreshDetail().catch(fail); });
  let searchTimer;
  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { pageIndex = 0; refreshDetail().catch(fail); }, 175);
  });

  return { tab: byId("open-transcript"), page, show: refresh, hide() { ++listRevision; ++detailRevision; clearTimeout(polling); player.pause(); } };
}

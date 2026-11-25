const byId = (id) => document.getElementById(id);
const onPages = location.origin === "https://poklontsevv.github.io";
const backend = onPages ? "http://127.0.0.1:8765" : "";
const active = new Set(["queued", "downloading", "processing"]);
const rows = new Map();
const autoDownloads = new Set();
const form = byId("search-form");
const link = byId("link");
const links = byId("links");
const results = byId("results");
const list = byId("result-list");
const inputError = byId("input-error");
const feedback = byId("feedback");
const stateText = byId("request-state");
const selectAll = byId("select-all");

let batch = false;
let currentId;
let online = false;
let searching = false;
let scheduling = false;
let desktop = false;
let timer;
let revision = 0;

async function api(path, method = "GET", body) {
  const headers = { "X-Videoport": "1" };
  if (body) headers["Content-Type"] = "application/json";
  const response = await fetch(backend + "/api/" + path, {
    method, headers, body: body && JSON.stringify(body), cache: "no-store",
    credentials: onPages ? "omit" : "same-origin",
    signal: AbortSignal.timeout(onPages ? 8000 : 40000),
    ...(onPages && { targetAddressSpace: "loopback" }),
  }).catch(() => { throw new Error("Загрузчик недоступен. Проверьте подключение."); });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Запрос не выполнен. Попробуйте ещё раз.");
  return payload;
}

function showError(text = "") {
  inputError.textContent = text;
  inputError.hidden = !text;
  (batch ? links : link).setAttribute("aria-invalid", String(Boolean(text)));
}

function tell(text = "", error = false) {
  feedback.textContent = text;
  feedback.hidden = !text;
  feedback.dataset.error = String(error);
}

async function refresh() {
  clearTimeout(timer);
  const version = ++revision;
  try {
    const workspace = await api("workspace");
    if (revision !== version) return online;
    online = true;
    desktop = workspace.desktop;
    byId("open-downloads").hidden = !desktop;
    byId("open-downloads").title = workspace.output;
    byId("connection-help").hidden = true;
    render(workspace);
    const busy = workspace.videos.some((video) => video.state === "loading")
      || workspace.jobs.some((job) => active.has(job.state));
    timer = setTimeout(refresh, busy ? 900 : 12000);
  } catch {
    if (revision === version) {
      online = false;
      byId("connection-help").hidden = false;
      renderControls();
    }
  }
  return online;
}

function duration(seconds) {
  if (!Number.isFinite(seconds)) return "";
  const minutes = Math.floor(seconds / 60);
  const tail = String(Math.floor(seconds % 60)).padStart(2, "0");
  if (minutes < 60) return minutes + ":" + tail;
  return Math.floor(minutes / 60) + ":" + String(minutes % 60).padStart(2, "0") + ":" + tail;
}

function syncRow(row) {
  const video = row.video;
  const ready = video.state === "ready";
  const title = video.title || (video.state === "loading" ? "Ищем видео…" : "Видео не найдено");
  row.title.textContent = title;
  row.channel.textContent = video.channel || "";
  row.duration.textContent = duration(video.duration);
  row.duration.hidden = !row.duration.textContent;
  const problem = video.error || (ready && video.live ? "Эфир пока недоступен для скачивания." : "");
  row.error.textContent = problem;
  row.error.hidden = !problem;
  row.retry.hidden = video.state !== "error";
  row.export.hidden = !ready;
  row.check.setAttribute("aria-label", "Выбрать «" + title + "»");
  row.quality.setAttribute("aria-label", "Качество для «" + title + "»");
  row.image.alt = ready ? "Обложка — " + title : "";
  if (video.preview && row.image.dataset.preview !== video.preview) {
    row.image.dataset.preview = video.preview;
    row.image.src = video.preview;
    row.image.hidden = false;
  }
  const previous = row.quality.value;
  row.quality.replaceChildren(...(video.qualities || []).map((quality) =>
    new Option(quality.p + "p · " + quality.width + " × " + quality.height, quality.value)));
  if (!row.quality.options.length) row.quality.add(new Option("Качество недоступно", ""));
  if ([...row.quality.options].some((option) => option.value === previous)) row.quality.value = previous;
}

function createRow(video) {
  const el = byId("video-template").content.firstElementChild.cloneNode(true);
  const find = (selector) => el.querySelector(selector);
  const row = {
    el, video, jobs: new Map(), pending: new Set(), title: find(".video-title"),
    channel: find(".channel"), duration: find(".duration"), error: find(".video-error"),
    image: find("img"), check: find(".video-check"), export: find(".export"), quality: find(".quality"),
    format: find(".format"), includeCover: find(".include-cover"), saveVideo: find(".save-video"),
    saveCover: find(".save-cover"), retry: find(".retry"), remove: find(".remove"), files: find(".files"),
  };
  row.check.checked = true;
  row.image.addEventListener("error", () => {
    const fallback = "https://i.ytimg.com/vi/" + row.video.id + "/hqdefault.jpg";
    if (row.image.src !== fallback) row.image.src = fallback;
    else row.image.hidden = true;
  });
  row.check.addEventListener("change", renderControls);
  row.saveVideo.addEventListener("click", () => schedule([row], "video", true));
  row.saveCover.addEventListener("click", () => schedule([row], "cover", true));
  row.retry.addEventListener("click", () => search([row.video.url]));
  row.remove.addEventListener("click", async () => {
    try {
      await api("videos/" + row.video.id, "DELETE");
      if (currentId === row.video.id) currentId = undefined;
      await refresh();
    } catch (error) {
      tell(error.message, true);
    }
  });
  list.append(el);
  return row;
}

function renderJob(row, job) {
  if (!row.jobs.has(job.id)) {
    const container = document.createElement("li");
    const status = document.createElement("span");
    const bar = document.createElement("progress");
    const file = document.createElement("a");
    status.setAttribute("aria-live", "polite");
    bar.max = 100;
    container.append(status, bar, file);
    row.files.append(container);
    row.jobs.set(job.id, { container, status, bar, file });
  }
  const { container, status, bar, file } = row.jobs.get(job.id);
  const name = job.kind === "cover" ? "Обложка" : "Видео " + job.quality.replace("x", " × ");
  const state = {
    queued: "в очереди", downloading: "скачиваем с YouTube", processing: "готовим файл",
    done: "готово", error: job.error,
  }[job.state];
  const content = name + " · " + state + (desktop && job.file ? " — " + job.file : "");
  if (status.textContent !== content) status.textContent = content;
  container.dataset.state = job.state;
  bar.hidden = job.state !== "downloading";
  bar.setAttribute("aria-label", name + ", ход скачивания");
  if (job.percent === null) bar.removeAttribute("value");
  else bar.value = job.percent;
  file.hidden = desktop || job.state !== "done";
  if (job.state === "done") {
    if (desktop) {
      autoDownloads.delete(job.id);
    } else {
      file.href = backend + "/files/" + encodeURIComponent(job.id);
      file.download = job.file;
      file.textContent = "Скачать " + job.file;
      if (autoDownloads.delete(job.id)) file.click();
    }
  } else if (job.state === "error") {
    autoDownloads.delete(job.id);
  }
}

function render(workspace) {
  const videos = new Set(workspace.videos.map(({ id }) => id));
  for (const [id, row] of rows) {
    if (!videos.has(id)) {
      row.el.remove();
      rows.delete(id);
    }
  }
  const groups = new Map();
  for (const job of workspace.jobs) {
    if (!groups.has(job.video_id)) groups.set(job.video_id, []);
    groups.get(job.video_id).push(job);
  }
  for (const video of workspace.videos) {
    if (!rows.has(video.id)) rows.set(video.id, createRow(video));
    const row = rows.get(video.id);
    const signature = JSON.stringify(video);
    if (row.signature !== signature) {
      row.signature = signature;
      row.video = video;
      syncRow(row);
    }
    const jobs = groups.get(video.id) || [];
    const ids = new Set(jobs.map(({ id }) => id));
    for (const [id, nodes] of row.jobs) {
      if (!ids.has(id)) {
        nodes.container.remove();
        row.jobs.delete(id);
        autoDownloads.delete(id);
      }
    }
    row.pending = new Set(jobs.filter((job) => active.has(job.state)).map(({ kind }) => kind));
    for (const job of jobs) renderJob(row, job);
    row.files.hidden = !jobs.length;
  }
  renderControls();
}

function available(row, kind) {
  if (row.video.state !== "ready" || row.pending.has(kind)) return false;
  return kind === "video" ? Boolean(!row.video.live && row.quality.value) : Boolean(row.video.preview);
}

function selected(kind) {
  return [...rows.values()].filter((row) => row.check.checked && available(row, kind));
}

function renderControls() {
  const shown = batch ? [...rows.values()] : [rows.get(currentId)].filter(Boolean);
  results.hidden = !shown.length;
  byId("batch-bar").hidden = !batch || !shown.length;
  const eligible = shown.filter((row) => available(row, "video") || available(row, "cover"));
  const checked = eligible.filter((row) => row.check.checked).length;
  selectAll.disabled = !online || !eligible.length;
  selectAll.checked = Boolean(eligible.length && checked === eligible.length);
  selectAll.indeterminate = checked > 0 && checked < eligible.length;
  byId("batch-download").disabled = !online || scheduling || !selected("video").length;
  byId("batch-covers").disabled = !online || scheduling || !selected("cover").length;
  for (const [id, row] of rows) {
    row.el.hidden = !batch && currentId !== id;
    row.check.hidden = !batch;
    row.check.disabled = !eligible.includes(row);
    row.remove.hidden = !batch;
    row.remove.disabled = !online || row.video.state === "loading" || Boolean(row.pending.size);
    row.saveVideo.disabled = !online || scheduling || !available(row, "video");
    row.saveCover.disabled = !online || scheduling || !available(row, "cover");
    row.quality.disabled = !online || !row.quality.value;
    row.retry.disabled = !online || searching;
  }
}

async function schedule(items, kind, downloadSingle = false) {
  if (!online || scheduling || !items.length) return;
  scheduling = true;
  tell();
  renderControls();
  const groups = new Map();
  for (const row of items) {
    const profile = row.format.value;
    const mode = kind === "video" && row.includeCover.checked ? "both" : kind;
    const key = profile + "/" + mode;
    if (!groups.has(key)) groups.set(key, { profile, mode, items: [] });
    groups.get(key).items.push({ id: row.video.id, quality: row.quality.value });
  }

  try {
    const reasons = new Set();
    let created = 0;
    for (const group of groups.values()) {
      for (let offset = 0; offset < group.items.length; offset += 50) {
        const data = await api("jobs", "POST", { ...group, items: group.items.slice(offset, offset + 50) });
        created += data.created;
        for (const skip of data.skipped) reasons.add(skip.reason);
        if (downloadSingle && data.jobs.length === 1) autoDownloads.add(data.jobs[0]);
      }
    }
    if (reasons.size) tell([...reasons].join(" "), !created);
    else if (batch) tell("Файлов в новой загрузке: " + created + ".");
    await refresh();
  } catch (error) {
    tell(error.message, true);
  } finally {
    scheduling = false;
    renderControls();
  }
}

async function search(values, field = null) {
  if (searching) return;
  searching = true;
  stateText.textContent = "Ищем видео…";
  byId("find-single").disabled = true;
  byId("find-batch").disabled = true;
  renderControls();
  try {
    if (!online && !await refresh()) throw new Error("Сначала подключите загрузчик.");
    const data = await api("videos", "POST", { links: values });
    if (data.rejected.length) {
      if (field === links) {
        const badLines = data.rejected.map(({ line }) => line);
        field.value = badLines.map((number) => values[number - 1]).join("\n");
        showError("Проверьте ссылки в строках: " + badLines.join(", ") + ".");
      } else if (field) {
        showError("Вставьте ссылку на обычное видео или Shorts с YouTube.");
      }
    } else if (field === links) {
      field.value = "";
    }
    if (data.accepted.length) {
      if (!batch) currentId = data.accepted[0];
      await refresh();
    }
  } catch (error) {
    if (field) showError(error.message);
    else tell(error.message, true);
  } finally {
    searching = false;
    stateText.textContent = "";
    byId("find-single").disabled = false;
    byId("find-batch").disabled = false;
    renderControls();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  showError();
  tell();
  const field = batch ? links : link;
  const values = batch ? field.value.trim().split(/\r?\n/) : [field.value.trim()];
  if (!values.some((value) => value.trim()) || values.length > 50) {
    showError(values.length > 50 ? "Добавьте до 50 ссылок за раз." : "Вставьте ссылку на YouTube.");
    field.focus();
    return;
  }
  if (batch) {
    const filled = values.map((value, index) => ({ value, index })).filter(({ value }) => value.trim());
    if (filled.length !== values.length) field.value = filled.map(({ value }) => value).join("\n");
    search(filled.map(({ value }) => value.trim()), field);
  } else {
    search(values, field);
  }
});
form.addEventListener("input", () => showError());
byId("batch-toggle").addEventListener("click", (event) => {
  showError();
  tell();
  batch = !batch;
  byId("single-input").hidden = batch;
  byId("batch-input").hidden = !batch;
  event.currentTarget.setAttribute("aria-pressed", String(batch));
  event.currentTarget.querySelector("span").textContent = batch ? "Одна ссылка" : "Пакетная загрузка";
  renderControls();
  (batch ? links : link).focus();
});
byId("paste").addEventListener("click", async () => {
  try {
    link.value = (await navigator.clipboard.readText()).trim();
    showError();
  } catch {
    showError("Вставьте ссылку через меню браузера или с клавиатуры.");
  }
  link.focus();
});

selectAll.addEventListener("change", () => {
  for (const row of rows.values()) {
    if (!row.check.disabled) row.check.checked = selectAll.checked;
  }
  renderControls();
});
byId("batch-download").addEventListener("click", () => schedule(selected("video"), "video"));
byId("batch-covers").addEventListener("click", () => schedule(selected("cover"), "cover"));

byId("open-downloads").addEventListener("click", () => {
  window.openVideoportDownloads?.();
});

byId("connect").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  stateText.textContent = "Подключаем загрузчик…";
  try {
    if (await refresh()) showError();
  } finally {
    stateText.textContent = "";
    button.disabled = false;
  }
});

byId("setup-detail").textContent = onPages
  ? "Запустите программу на своём компьютере и разрешите доступ к локальной сети, если браузер его запросит."
  : "Проверьте, что сервер Videoport запущен, и попробуйте подключиться снова.";
for (const id of ["setup-command", "setup-guide", "open-local"]) byId(id).hidden = !onPages;
if (onPages) byId("connection-help").hidden = false;
else refresh();

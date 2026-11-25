const get = (id) => document.getElementById(id);
const fromPages = window.location.origin === "https://poklontsevv.github.io";
const apiBase = fromPages ? "http://127.0.0.1:8765" : "";
const ui = {
  form: get("link-form"), links: get("links"), add: get("add-button"), inputError: get("input-error"),
  connection: get("connection"), connectionText: get("connection-text"),
  connectionHelp: get("connection-help"), reconnect: get("reconnect"),
  profile: get("profile"), profileHelp: get("profile-help"), withCover: get("with-cover"),
  output: get("output-path"), copy: get("copy-output"),
  count: get("queue-count"), subtitle: get("queue-subtitle"), batch: get("batch-button"),
  batchCount: get("batch-count"), toolbar: get("queue-toolbar"), covers: get("batch-covers"),
  selectAll: get("select-all"), message: get("queue-message"), empty: get("empty-queue"),
  list: get("video-list"), row: get("video-row"),
};

const rows = new Map();
const active = new Set(["queued", "downloading", "processing"]);
let workspace = { videos: [], jobs: [] };
let online = false;
let working = false;
let adding = false;
let timer;
let revision = 0;

async function api(path, options = {}) {
  const { body, method = "GET" } = options;
  const headers = { "X-Videoport": "1" };
  if (body) headers["Content-Type"] = "application/json";
  let response;
  try {
    response = await fetch(apiBase + "/api/" + path, {
      method, headers, body: body && JSON.stringify(body),
      credentials: "omit", cache: "no-store", signal: AbortSignal.timeout(8000),
      ...(fromPages && { targetAddressSpace: "loopback" }),
    });
  } catch {
    throw new Error("Не удалось подключиться к локальному загрузчику.");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Запрос не выполнен. Попробуйте ещё раз.");
  return data;
}

function connection(state) {
  if (ui.connection.dataset.state !== state) {
    ui.connection.dataset.state = state;
    ui.connectionText.textContent = {
      online: "Загрузчик подключён", offline: "Нет подключения", connecting: "Подключение…",
    }[state];
  }
  ui.connectionHelp.hidden = state === "online" || (state === "connecting" && ui.connectionHelp.hidden);
  ui.reconnect.disabled = state === "connecting";
}

async function refresh() {
  clearTimeout(timer);
  const current = ++revision;
  try {
    const data = await api("workspace");
    if (current !== revision) return;
    workspace = data;
    online = true;
    connection("online");
    render();
    const busy = data.videos.some((video) => video.state === "loading") || data.jobs.some((job) => active.has(job.state));
    timer = setTimeout(refresh, busy ? 750 : 4000);
  } catch {
    if (current !== revision) return;
    online = false;
    connection("offline");
    render();
  }
}

function inputError(text = "") {
  ui.inputError.textContent = text;
  ui.inputError.hidden = !text;
  ui.links.setAttribute("aria-invalid", String(Boolean(text)));
}

function message(text = "", isError = false) {
  ui.message.textContent = text;
  ui.message.dataset.error = String(isError);
  ui.message.hidden = !text;
}

function setProfileHelp() {
  ui.profileHelp.textContent = ui.profile.value === "mp4"
    ? "Для загрузки на другие площадки."
    : "Сохраняет исходные видео и звук без перекодирования.";
}

function videoDuration(seconds) {
  if (!Number.isFinite(seconds)) return "";
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  const tail = String(total % 60).padStart(2, "0");
  if (minutes < 60) return minutes + ":" + tail;
  return Math.floor(minutes / 60) + ":" + String(minutes % 60).padStart(2, "0") + ":" + tail;
}

function syncVideo(row, video) {
  const title = video.title || video.id;
  row.title.textContent = title;
  row.title.href = video.url;
  row.meta.textContent = video.state === "loading"
    ? "Запрашиваем данные у YouTube…"
    : [video.channel, video.live ? "Эфир ещё недоступен для скачивания" : videoDuration(video.duration)]
      .filter(Boolean).join(" · ");
  row.error.textContent = video.error || "";
  row.error.hidden = !video.error;
  const image = row.image;
  if (video.preview && image.dataset.preview !== video.preview) {
    image.dataset.preview = video.preview;
    image.src = video.preview;
    image.hidden = false;
  }
  const previous = row.quality.value;
  row.quality.replaceChildren(...(video.qualities || []).map((quality) =>
    new Option(quality.p + "p · " + quality.width + " × " + quality.height, quality.value)));
  if (!row.quality.options.length) row.quality.add(new Option("Без разрешения", ""));
  if ([...row.quality.options].some((option) => option.value === previous)) row.quality.value = previous;
  row.check.setAttribute("aria-label", "Выбрать «" + title + "»");
  row.quality.setAttribute("aria-label", "Разрешение для «" + title + "»");
  row.download.setAttribute("aria-label", "Скачать видео «" + title + "»");
  row.cover.setAttribute("aria-label", "Скачать обложку «" + title + "»");
  row.remove.setAttribute("aria-label", "Убрать «" + title + "» из очереди");
  row.remove.title = "Убрать из очереди";
}

function createRow(video) {
  const element = ui.row.content.firstElementChild.cloneNode(true);
  const query = (name) => element.querySelector(name);
  const row = {
    element, video, title: query(".video-title"), meta: query(".video-meta"), image: query("img"),
    error: query(".video-error"), quality: query(".row-quality"), check: query(".row-check"),
    download: query(".row-download"), cover: query(".row-cover"), retry: query(".retry"),
    remove: query(".remove"), jobs: query(".job-list"), jobNodes: new Map(),
  };
  row.check.checked = true;
  row.check.addEventListener("change", renderControls);
  row.download.addEventListener("click", () => start([row], ui.withCover.checked ? "both" : "video"));
  row.cover.addEventListener("click", () => start([row], "cover"));
  row.retry.addEventListener("click", async () => {
    try {
      await api("videos", { method: "POST", body: { links: [row.video.url] } });
      await refresh();
    } catch (error) {
      message(error.message, true);
    }
  });
  row.remove.addEventListener("click", async () => {
    try {
      await api("videos/" + row.video.id, { method: "DELETE" });
      await refresh();
    } catch (error) {
      message(error.message, true);
    }
  });
  row.image.addEventListener("error", () => {
    const fallback = "https://i.ytimg.com/vi/" + row.video.id + "/mqdefault.jpg";
    if (row.image.src !== fallback) row.image.src = fallback;
    else row.image.hidden = true;
  });
  ui.list.append(element);
  return row;
}

function renderJob(row, job) {
  if (!row.jobNodes.has(job.id)) {
    const element = document.createElement("li");
    const label = document.createElement("span");
    const bar = document.createElement("progress");
    label.setAttribute("aria-live", "polite");
    bar.max = 100;
    element.append(label, bar);
    row.jobs.append(element);
    row.jobNodes.set(job.id, { element, label, bar });
  }

  const { element, label, bar } = row.jobNodes.get(job.id);
  const type = job.kind === "cover" ? "Обложка" : "Видео " + job.profile.toUpperCase() + " · " + job.quality.replace("x", " × ");
  const state = {
    queued: "в очереди", downloading: "скачивается", processing: "собираем файл",
    done: "сохранено", error: job.error,
  }[job.state];
  const caption = type + " · " + state + (job.file ? " — " + job.file : "");
  if (label.textContent !== caption) label.textContent = caption;
  element.dataset.state = job.state;
  bar.hidden = job.state !== "downloading";
  bar.setAttribute("aria-label", type + ", ход скачивания");
  if (job.percent === null) bar.removeAttribute("value");
  else bar.value = job.percent;
}

function render() {
  const ids = new Set(workspace.videos.map((video) => video.id));
  for (const [id, row] of rows) {
    if (!ids.has(id)) {
      row.element.remove();
      rows.delete(id);
    }
  }

  const grouped = new Map();
  for (const job of workspace.jobs) {
    if (!grouped.has(job.video_id)) grouped.set(job.video_id, []);
    grouped.get(job.video_id).push(job);
  }
  for (const video of workspace.videos) {
    if (!rows.has(video.id)) rows.set(video.id, createRow(video));
    const row = rows.get(video.id);
    if (row.signature !== JSON.stringify(video)) {
      row.video = video;
      row.signature = JSON.stringify(video);
      syncVideo(row, video);
    }
    row.pending = new Set();
    const jobs = grouped.get(video.id) || [];
    const jobIds = new Set(jobs.map((job) => job.id));
    for (const [id, { element }] of row.jobNodes) {
      if (!jobIds.has(id)) {
        element.remove();
        row.jobNodes.delete(id);
      }
    }
    for (const job of jobs) {
      if (active.has(job.state)) row.pending.add(job.kind);
      renderJob(row, job);
    }
    row.jobs.hidden = !jobs.length;
  }

  const count = rows.size;
  const busy = workspace.jobs.filter((job) => active.has(job.state)).length;
  ui.count.hidden = !count;
  ui.count.textContent = count;
  ui.empty.hidden = Boolean(count);
  ui.toolbar.hidden = !count;
  ui.subtitle.textContent = busy ? "Файлов в обработке: " + busy : "Выберите разрешение для каждого видео.";
  if (workspace.output) {
    ui.output.textContent = workspace.output;
    ui.output.title = workspace.output;
  }
  renderControls();
}

function canDownload(row, kind) {
  if (row.video.state !== "ready" || row.pending.has(kind)) return false;
  if (kind === "cover") return Boolean(row.video.preview);
  return Boolean(!row.video.live && row.quality.value);
}

function selectedFor(kind) {
  return [...rows.values()].filter((row) => row.check.checked && canDownload(row, kind));
}

function renderControls() {
  ui.add.disabled = !online || adding;
  ui.copy.disabled = !online || !workspace.output;
  const available = [...rows.values()].filter((row) => canDownload(row, "video") || canDownload(row, "cover"));
  const selected = available.filter((row) => row.check.checked);
  const downloads = selectedFor("video").length;
  ui.batch.disabled = !online || working || !downloads;
  ui.batchCount.hidden = !downloads;
  ui.batchCount.textContent = downloads;
  ui.covers.disabled = !online || working || !selectedFor("cover").length;
  ui.selectAll.disabled = !online || !available.length;
  ui.selectAll.checked = Boolean(available.length && selected.length === available.length);
  ui.selectAll.indeterminate = Boolean(selected.length && selected.length < available.length);
  for (const row of rows.values()) {
    row.check.disabled = !online || (!canDownload(row, "video") && !canDownload(row, "cover"));
    row.quality.disabled = !online || !canDownload(row, "video");
    row.download.disabled = !online || working || !canDownload(row, "video");
    row.cover.disabled = !online || working || !canDownload(row, "cover");
    row.retry.hidden = row.video.state !== "error";
    row.retry.disabled = !online;
    row.remove.disabled = !online || row.video.state === "loading" || Boolean(row.pending.size);
  }
}

async function start(items, mode) {
  if (!items.length || working) return;
  working = true;
  message();
  renderControls();
  try {
    const list = items.map((row) => ({ id: row.video.id, quality: row.quality.value }));
    const profile = ui.profile.value;
    let created = 0;
    const skipped = [];
    for (let offset = 0; offset < list.length; offset += 50) {
      const group = list.slice(offset, offset + 50);
      const result = await api("jobs", { method: "POST", body: { items: group, mode, profile } });
      created += result.created;
      skipped.push(...result.skipped);
    }
    const reasons = [...new Set(skipped.map((item) => item.reason))];
    message(["Файлов добавлено в очередь: " + created + ".", ...reasons].join(" "), !created);
    await refresh();
  } catch (error) {
    message(error.message, true);
    await refresh();
  } finally {
    working = false;
    renderControls();
  }
}

ui.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (adding || !online) return;
  inputError();
  const lines = ui.links.value.split(/\r?\n/).map((link, index) => ({ link: link.trim(), number: index + 1 }))
    .filter(({ link }) => link);
  if (!lines.length || lines.length > 50) {
    inputError(lines.length ? "Добавьте не больше 50 ссылок за раз." : "Вставьте ссылку на видео.");
    ui.links.focus();
    return;
  }

  adding = true;
  ui.links.readOnly = true;
  renderControls();
  try {
    const data = await api("videos", { method: "POST", body: { links: lines.map((line) => line.link) } });
    const rejected = data.rejected.map(({ line }) => lines[line - 1]);
    ui.links.value = rejected.map(({ link }) => link).join("\n");
    if (rejected.length) {
      inputError("Проверьте ссылки на видео в строках: " + rejected.map(({ number }) => number).join(", ") + ".");
      ui.links.focus();
    }
    if (data.accepted.length) {
      message("Ссылок принято: " + data.accepted.length + ".");
      await refresh();
    }
  } catch (error) {
    inputError(error.message);
  } finally {
    adding = false;
    ui.links.readOnly = false;
    renderControls();
  }
});

ui.links.addEventListener("input", () => inputError());
ui.profile.addEventListener("change", setProfileHelp);
ui.selectAll.addEventListener("change", () => {
  for (const row of rows.values()) {
    if (!row.check.disabled) row.check.checked = ui.selectAll.checked;
  }
  renderControls();
});
ui.batch.addEventListener("click", () => start(selectedFor("video"), ui.withCover.checked ? "both" : "video"));
ui.covers.addEventListener("click", () => start(selectedFor("cover"), "cover"));
ui.reconnect.addEventListener("click", () => {
  connection("connecting");
  refresh();
});
ui.copy.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(workspace.output);
    message("Путь к папке скопирован.");
  } catch {
    const range = document.createRange();
    range.selectNodeContents(ui.output);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    message("Путь к папке выделен. Скопируйте его.");
  }
});

if (!fromPages) {
  get("setup-command").textContent = "python -m videoport";
  get("setup-text").textContent = "Проверьте, что локальный загрузчик запущен, и подключитесь снова.";
} else {
  get("local-link").hidden = false;
}
setProfileHelp();
refresh();

import { setupClips } from "./clips.js";
import { setupTranscript } from "./transcript-workspace.js";
import { setupScripts } from "./script-workspace.js";
import { button, element, icon, setupStudioNavigation } from "./studio-ui.js";
import { setupUpdates } from "./updates.js";
const byId = (id) => document.getElementById(id);
const onPages = location.origin === "https://poklontsevv.github.io";
const backend = onPages ? "http://127.0.0.1:8765" : "";
const active = new Set(["queued", "downloading", "processing", "paused", "canceling", "publishing"]);
const stages = {
  connecting: "Подключение к площадке и выбор дорожек",
  transfer: "Скачивание файла", video: "Скачивание видеодорожки", audio: "Скачивание звуковой дорожки",
  merging: "Объединение видео и звука без перекодирования",
  remuxing: "Запись дорожек в новый контейнер без перекодирования",
  encoding: "Перекодирование для совместимости с MP4",
  saving: "Сохранение в выбранную папку",
};
const categories = {
  youtube: { all: "Все", videos: "Длинные видео", shorts: "Shorts" },
  instagram: { all: "Все", posts: "Посты", reels: "Рилсы", highlights: "Хайлайты", stories: "Сторис" },
};
const manager = byId("download-manager");
const managerItems = new Map();
const queueFilters = new Map();
const pickedJobs = new Map();
const platformViews = new Map();
const mediaMain = byId("media-main");
const downloadsMain = byId("downloads-main");
let categorySignature = "";
const rows = new Map();
const autoDownloads = new Set();
const form = byId("search-form");
const link = byId("link");
const links = byId("links");
const sourceQuality = byId("source-quality");
const results = byId("results");
const list = byId("result-list");
const inputError = byId("input-error");
const feedback = byId("feedback");
const stateText = byId("request-state");
const selectAll = byId("select-all");

let batch = false;
const platforms = {
  youtube: ["YouTube", "Видео, Shorts, плейлисты и каналы", "youtube.com/watch?v=…"],
  instagram: ["Instagram", "Посты, Reels, Stories и хайлайты", "instagram.com/reel/…"],
  tiktok: ["TikTok", "Видео по ссылке или из профиля", "tiktok.com/@…/video/…"],
};
const tabs = Object.fromEntries(Object.keys(platforms).map((name) => [name, { link: "", links: "", batch: false, quality: "auto", ids: null, category: "all" }]));
let platform = "youtube";
let latest;
let changingFolder = false;
let online = false;
let searching = false;
let scheduling = false;
let desktop = false;
let timer;
let revision = 0;
let themeLoaded = false;
let savingTheme = false;

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const label = theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему";
  byId("theme-switch").title = label;
  byId("theme-switch").setAttribute("aria-label", label);
  for (const option of document.querySelectorAll("[data-theme-choice]")) {
    option.setAttribute("aria-pressed", String(option.dataset.themeChoice === theme));
  }
}

try { applyTheme(localStorage.getItem("videoport-theme") === "dark" ? "dark" : "light"); }
catch { applyTheme("light"); }

async function chooseTheme(theme) {
  if (savingTheme) return;
  const previous = document.documentElement.dataset.theme;
  if (previous === theme) return;
  savingTheme = true;
  applyTheme(theme);
  try {
    if (desktop) await api("ui", "POST", { theme });
    try { localStorage.setItem("videoport-theme", theme); } catch { /* Storage may be disabled. */ }
  } catch (error) {
    applyTheme(previous);
    byId("theme-switch").title = error.message;
  } finally { savingTheme = false; }
}

byId("theme-switch").addEventListener("click", () => chooseTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
for (const option of document.querySelectorAll("[data-theme-choice]")) {
  option.addEventListener("click", () => chooseTheme(option.dataset.themeChoice));
}

async function api(path, method = "GET", body) {
  const headers = { "X-Videoport": "1" };
  if (body) headers["Content-Type"] = "application/json";
  const response = await fetch(backend + "/api/" + path, {
    method, headers, body: body && JSON.stringify(body), cache: "no-store",
    credentials: onPages ? "omit" : "same-origin",
    signal: AbortSignal.timeout(onPages ? 8000 : path === "transcript/export" ? 600000 : 40000),
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
    if (desktop && !themeLoaded) {
      themeLoaded = true;
      applyTheme(workspace.theme === "dark" ? "dark" : "light");
    }
    document.body.classList.toggle("desktop-app", desktop);
    byId("open-clips").hidden = !workspace.clips;
    byId("open-transcript").hidden = !workspace.transcripts;
    byId("open-scripts").hidden = !workspace.scripts;
    byId("open-update").hidden = !workspace.updates;
    byId("open-downloads").hidden = !desktop;
    latest = workspace;
    byId("connection-help").hidden = true;
    render(workspace);
    const busy = workspace.catalogs.some((item) => item.state === "loading")
      || workspace.videos.some((video) => ["loading", "quality_queued"].includes(video.state))
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
  const waiting = ["loading", "quality_queued"].includes(video.state);
  const ready = video.state === "ready" || (waiting && Boolean(video.title));
  const title = video.title || (video.state === "loading" ? "Ищем видео…" : "Видео не найдено");
  row.title.textContent = title;
  row.channel.textContent = video.channel || "";
  row.duration.textContent = duration(video.duration);
  row.duration.hidden = !row.duration.textContent;
  const problem = video.error || (ready && video.live ? "Эфир пока недоступен для скачивания." : "");
  row.error.textContent = problem;
  row.error.hidden = !problem;
  row.retry.hidden = !video.error || waiting;
  row.loadQuality.hidden = video.platform === "youtube" || video.details || video.photo || !video.has_video;
  row.loadQuality.textContent = video.state === "loading" ? "Уточняем…" : "Показать варианты";
  row.qualityLabel.hidden = video.photo;
  row.format.parentElement.parentElement.hidden = video.photo;
  row.saveVideo.querySelector("span").textContent = video.photo ? "Скачать фото" : "Скачать видео";
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
    new Option(({ 2160: "4K", 4320: "8K" }[quality.p] || quality.p + "p")
      + " · " + quality.width + " × " + quality.height, quality.value)));
  if (video.has_video && video.platform !== "youtube") row.quality.prepend(new Option("Лучшее доступное", "best"));
  if (!row.quality.options.length) {
    const label = video.state === "quality_queued" ? "Разрешение в очереди · авто"
      : waiting ? "Определяем разрешение · авто" : "Разрешение определится при скачивании";
    row.quality.add(new Option(label, video.has_video ? "best" : ""));
  }
  if (row.qualityEdited && [...row.quality.options].some((option) => option.value === previous)) {
    row.quality.value = previous;
  } else {
    const target = Number(tabs[video.platform]?.quality);
    const choices = video.qualities || [];
    const match = target && (choices.find((quality) => quality.p <= target) || choices.at(-1));
    if (match) row.quality.value = match.value;
  }
}

function createRow(video) {
  const el = byId("video-template").content.firstElementChild.cloneNode(true);
  const find = (selector) => el.querySelector(selector);
  const row = {
    el, video, jobs: new Map(), pending: new Set(), title: find(".video-title"),
    channel: find(".channel"), duration: find(".duration"), error: find(".video-error"),
    image: find("img"), check: find(".video-check"), export: find(".export"), quality: find(".quality"),
    loadQuality: find(".load-quality"), qualityLabel: find(".quality-label"), format: find(".format"), includeCover: find(".include-cover"), saveVideo: find(".save-video"),
    saveCover: find(".save-cover"), retry: find(".retry"), remove: find(".remove"), files: find(".files"),
  };
  row.check.checked = false;
  const loadDetails = async () => {
    row.loadQuality.disabled = true;
    try {
      await api("videos/" + row.video.id + "/details", "POST");
      await refresh();
    } catch (error) {
      tell(error.message, true);
      renderControls();
    }
  };
  row.loadQuality.addEventListener("click", loadDetails);
  row.retry.addEventListener("click", loadDetails);
  row.image.addEventListener("error", () => {
    const fallback = "https://i.ytimg.com/vi/" + row.video.id + "/hqdefault.jpg";
    if (row.video.platform === "youtube" && row.image.src !== fallback) row.image.src = fallback;
    else row.image.hidden = true;
  });
  row.check.addEventListener("change", renderControls);
  row.quality.addEventListener("change", () => { row.qualityEdited = true; });
  row.saveVideo.addEventListener("click", () => schedule([row], "video", true));
  row.saveCover.addEventListener("click", () => schedule([row], "cover", true));
  row.remove.addEventListener("click", async () => {
    try {
      await api("videos/" + row.video.id, "DELETE");
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
  const name = { cover: "Обложка", photo: "Фото" }[job.kind]
    || (job.quality === "best" ? "Видео" : "Видео " + job.quality.replace("x", " × "));
  const state = jobStatus(job);
  const content = name + " · " + state + (desktop && job.file ? " — " + job.file : "");
  if (status.textContent !== content) status.textContent = content;
  container.dataset.state = job.state;
  bar.hidden = !["downloading", "processing", "publishing"].includes(job.state);
  bar.setAttribute("aria-label", name + ", ход скачивания");
  if (!Number.isFinite(job.percent)) bar.removeAttribute("value");
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
  } else if (["error", "canceled"].includes(job.state)) {
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
  const video = row.video;
  if (video.state === "error" || (video.state === "loading" && !video.title)
      || row.pending.has(video.photo ? "photo" : kind)) return false;
  if (video.photo) return kind === "video" && Boolean(video.preview);
  return kind === "video" ? Boolean(!video.live && row.quality.value) : Boolean(video.preview);
}

function sizeText(bytes) {
  const mib = bytes / 1048576;
  return mib.toLocaleString("ru", { maximumFractionDigits: 1 }) + " МБ";
}

function jobStatus(job) {
  if (job.state === "done") return "Готово";
  if (job.state === "error") return job.error || "Ошибка загрузки";
  if (job.state === "canceled") return "Отменено";
  if (job.state === "canceling") return "Отмена и удаление неполного файла";
  if (job.state === "queued") return "В очереди";
  const stage = stages[job.stage] || "Подключение к площадке";
  if (job.state === "paused") return "На паузе · " + (job.stage ? stage : "в очереди");
  const parts = [stage];
  if (Number.isFinite(job.percent)) parts.push(job.percent + "%");
  if (Number.isFinite(job.loaded)) parts.push(sizeText(job.loaded) + (job.total ? " из " + sizeText(job.total) : ""));
  if (job.speed > 0) parts.push(sizeText(job.speed) + "/с");
  if (Number.isFinite(job.eta)) parts.push("осталось ≈ " + duration(job.eta));
  else {
    if (job.stage_started) parts.push("этап идёт " + duration(Math.max(0, Date.now() / 1000 - job.stage_started)));
    parts.push("оценка времени пока недоступна");
  }
  return parts.join(" · ");
}

function managerMessage(text = "") {
  const message = byId("manager-message");
  message.textContent = text;
  message.hidden = !text;
}

async function manageJob(id, action) {
  try {
    managerMessage();
    if (action === "file" || action === "folder") {
      const location = await api("jobs/" + id + "/location");
      if (action === "file") await window.openVideoportFile(location.file);
      else await window.openVideoportDownloads(location.folder);
    } else {
      await api("jobs/" + id + "/" + action, "POST");
      await refresh();
    }
  } catch (error) {
    managerMessage(String(error.message || error));
  }
}

function queueScope() {
  return downloadsMain.hidden ? platform : "all";
}

function jobProfile(job) {
  if (job.kind === "cover") return "Обложка";
  if (job.kind === "photo") return "Фото";
  const dimensions = /^(\d+)x(\d+)$/.exec(job.quality);
  const height = dimensions && Math.min(Number(dimensions[1]), Number(dimensions[2]));
  const quality = { 2160: "4K", 4320: "8K" }[height] || (height ? height + "p" : "Авто");
  return [quality, job.profile?.toUpperCase()].filter(Boolean).join(" · ");
}

function shortStatus(job) {
  if (job.state === "downloading") return "Скачивается";
  if (["merging", "remuxing"].includes(job.stage) && job.state === "processing") return "Сборка " + (job.profile || "mkv").toUpperCase();
  if (job.state === "processing" && job.stage === "encoding") return "Перекодирование";
  return { done: "Готово", error: "Ошибка", canceled: "Отменено", paused: "На паузе", queued: "В очереди",
    canceling: "Отмена", processing: "Обработка", publishing: "Сохранение" }[job.state] || "Подключение";
}

const queueActions = {
  pause: ["pause", "Поставить на паузу"], resume: ["play", "Возобновить загрузку"],
  cancel: ["close", "Отменить загрузку"], file: ["play", "Открыть файл"], folder: ["folder", "Открыть папку"],
};

function actionButton(job, action, options, compact = true) {
  const [symbol, name] = options;
  const node = button(compact ? "" : name, () => manageJob(job.id, action));
  node.prepend(icon(symbol));
  if (compact) node.classList.add("ui-tool");
  node.title = name;
  node.setAttribute("aria-label", name);
  return node;
}

function coverImage(image, url) {
  if (image.dataset.cover === url) return;
  image.dataset.cover = url;
  image.hidden = !url;
  if (url) image.src = url;
  else image.removeAttribute("src");
}

function renderJobDetails(job, video) {
  const panel = byId("job-details");
  panel.hidden = !job;
  if (!job) return;
  coverImage(byId("job-cover"), video?.preview || job.preview || "");
  byId("job-title").textContent = job.file || job.title || job.video_id;
  byId("job-source").textContent = [platforms[job.platform]?.[0], video?.channel || job.channel].filter(Boolean).join(" · ");
  const values = [
    ["Статус", shortStatus(job)], ["Скорость", job.speed > 0 ? sizeText(job.speed) + "/с" : "—"],
    ["Размер", job.total > 0 ? sizeText(job.total) : job.loaded > 0 ? sizeText(job.loaded) : "—"],
    ["Формат", jobProfile(job)],
  ];
  const stats = byId("job-stats");
  const signature = JSON.stringify(values);
  if (stats.dataset.signature !== signature) {
    stats.dataset.signature = signature;
    stats.replaceChildren(...values.map(([label, value]) => {
      const row = element("div");
      row.append(element("dt", label), element("dd", value));
      return row;
    }));
  }
  const actions = byId("job-detail-actions");
  const key = job.id + "/" + (job.state === "done" && desktop);
  if (actions.dataset.key !== key) {
    actions.dataset.key = key;
    actions.replaceChildren(...(job.state === "done" && desktop
      ? ["file", "folder"].map((action) => actionButton(job, action, queueActions[action], false)) : []));
  }
}

function renderManager() {
  const jobs = latest?.jobs || [];
  const working = jobs.filter((job) => active.has(job.state)).length;
  byId("downloads-active").textContent = working + " в работе";
  byId("platform-active").textContent = jobs.filter((job) => job.platform === platform && active.has(job.state)).length + " в работе";
  byId("show-downloads").title = working ? "Загрузки · " + working + " в работе" : "Загрузки";
  if (downloadsMain.hidden && mediaMain.hidden) { manager.hidden = true; return; }
  const scope = queueScope();
  const mount = byId(scope === "all" ? "all-downloads-queue" : "platform-queue");
  if (manager.parentElement !== mount) mount.append(manager);
  manager.hidden = mount.hidden;
  if (manager.hidden) return;
  byId("downloads-title").textContent = scope === "all" ? "Очередь" : "Передачи";
  const filter = queueFilters.get(scope) || "all";
  const group = jobs.filter((job) => scope === "all" || job.platform === scope);
  const counts = { all: group.length, active: group.filter((job) => active.has(job.state)).length,
    done: group.filter((job) => job.state === "done").length };
  for (const option of manager.querySelectorAll("[data-download-filter]")) {
    const id = option.dataset.downloadFilter;
    const label = { all: "Все", active: "В процессе", done: "Готовые" }[id];
    const text = element("small", String(counts[id]));
    if (option.textContent !== label + " " + counts[id]) option.replaceChildren(label + " ", text);
    option.setAttribute("aria-pressed", String(id === filter));
  }
  const shown = group.filter((job) => filter === "all" || (filter === "active" && active.has(job.state))
    || (filter === "done" && job.state === "done"));
  const picked = shown.find((job) => job.id === pickedJobs.get(scope)) || shown[0];
  if (picked) pickedJobs.set(scope, picked.id);
  byId("manager-empty").hidden = Boolean(shown.length);
  const ids = new Set(shown.map((job) => job.id));
  for (const [id, entry] of managerItems) {
    if (!ids.has(id)) { entry.el.remove(); managerItems.delete(id); }
  }
  const videos = new Map((latest?.videos || []).map((video) => [video.id, video]));
  const list = byId("manager-list");
  for (const job of shown) {
    if (!managerItems.has(job.id)) {
      const el = byId("manager-template").content.firstElementChild.cloneNode(true);
      const find = (name) => el.querySelector(".manager-" + name);
      const actions = find("actions");
      const buttons = Object.fromEntries(Object.entries(queueActions).map(([action, options]) => [action, actionButton(job, action, options)]));
      const choice = el.querySelector(".job-identity");
      choice.addEventListener("click", () => { pickedJobs.set(queueScope(), job.id); renderManager(); });
      const cover = find("cover");
      cover.addEventListener("error", () => { cover.hidden = true; });
      const file = element("a", "Скачать файл", "button secondary");
      actions.append(...Object.values(buttons), file);
      managerItems.set(job.id, { el, choice, cover, title: find("title"), source: find("source"),
        state: find("state"), progress: find("progress"), percent: find("percent"), eta: find("eta"), buttons, file });
      list.append(el);
    }
    const entry = managerItems.get(job.id);
    coverImage(entry.cover, videos.get(job.video_id)?.preview || job.preview || "");
    entry.title.textContent = job.file || job.title || job.video_id;
    entry.source.textContent = [scope === "all" && platforms[job.platform]?.[0], jobProfile(job),
      job.total > 0 ? sizeText(job.total) : ""].filter(Boolean).join(" · ");
    entry.state.textContent = shortStatus(job);
    entry.state.title = jobStatus(job);
    entry.el.dataset.state = job.state;
    entry.el.classList.toggle("current", job === picked);
    entry.choice.setAttribute("aria-pressed", String(job === picked));
    const percent = Number.isFinite(job.percent) ? job.percent : job.state === "done" ? 100 : job.state === "queued" ? 0 : null;
    if (percent === null) entry.progress.removeAttribute("value");
    else entry.progress.value = percent;
    entry.percent.textContent = percent === null ? "" : percent + "%";
    entry.eta.textContent = Number.isFinite(job.eta) ? duration(job.eta) : "—";
    const canPause = ["queued", "downloading", "processing"].includes(job.state);
    entry.buttons.pause.hidden = !canPause;
    entry.buttons.resume.hidden = job.state !== "paused";
    entry.buttons.cancel.hidden = !canPause && job.state !== "paused";
    entry.buttons.file.hidden = entry.buttons.folder.hidden = job.state !== "done" || !desktop;
    entry.file.hidden = job.state !== "done" || desktop;
    if (!entry.file.hidden) entry.file.href = backend + "/files/" + encodeURIComponent(job.id);
  }
  renderJobDetails(picked, videos.get(picked?.video_id));
}

byId("job-cover").addEventListener("error", (event) => { event.currentTarget.hidden = true; });
for (const option of manager.querySelectorAll("[data-download-filter]")) {
  option.addEventListener("click", () => { queueFilters.set(queueScope(), option.dataset.downloadFilter); renderManager(); });
}
for (const tab of document.querySelectorAll("[data-media-view]")) {
  tab.addEventListener("click", () => { platformViews.set(platform, tab.dataset.mediaView); renderControls(); });
}

function renderCategories() {
  const tabsElement = byId("content-tabs");
  const choices = categories[platform];
  tabsElement.hidden = !choices;
  const signature = platform + "/" + tabs[platform].category;
  if (signature === categorySignature) return;
  categorySignature = signature;
  tabsElement.replaceChildren();
  for (const [value, label] of Object.entries(choices || {})) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-pressed", String(tabs[platform].category === value));
    button.addEventListener("click", () => {
      tabs[platform].category = value;
      renderControls();
      [...tabsElement.children].find((tab) => tab.textContent === label)?.focus();
    });
    tabsElement.append(button);
  }
}

function shownRows() {
  const { ids, category } = tabs[platform];
  return [...rows.values()].filter(({ video }) => video.platform === platform
    && (!ids || ids.has(video.id) || video.catalog_ids?.some((id) => ids.has(id)))
    && (category === "all" || category === (video.category || (platform === "youtube" ? "videos" : "posts"))));
}

function renderControls() {
  const shown = shownRows();
  const eligible = shown.filter((row) => available(row, "video"));
  const checked = eligible.filter((row) => row.check.checked).length;
  const catalogState = byId("catalog-state");
  const found = latest?.catalogs.filter((item) => item.platform === platform
    && (!tabs[platform].ids || tabs[platform].ids.has(item.id))) || [];
  const busy = found.some((item) => item.state === "loading");
  const problems = [...new Set(found.filter((item) => item.state === "error").map((item) => item.error))];
  const status = [busy ? "Загружаем список материалов…" : "", ...problems].filter(Boolean).join(" ");
  if (catalogState.textContent !== status) catalogState.textContent = status;
  catalogState.hidden = !status;
  renderCategories();
  const hasMaterials = shown.length > 0 || found.length > 0;
  const materials = hasMaterials && platformViews.get(platform) === "materials";
  byId("platform-views").hidden = !hasMaterials;
  byId("platform-queue").hidden = materials;
  for (const tab of document.querySelectorAll("[data-media-view]")) {
    tab.setAttribute("aria-pressed", String(tab.dataset.mediaView === (materials ? "materials" : "queue")));
  }
  results.hidden = !materials;
  byId("batch-download").hidden = byId("download-all").hidden = !materials;
  byId("open-queue").hidden = materials;
  renderManager();
  renderSettingsFolders();
  const inspecting = shown.filter((row) => ["loading", "quality_queued"].includes(row.video.state)).length;
  if (!busy && inspecting) {
    catalogState.textContent = "Уточняем разрешения · " + inspecting + ". Можно скачать в автоматическом качестве.";
    catalogState.hidden = false;
  }
  byId("batch-bar").hidden = !shown.length;
  selectAll.disabled = !online || !eligible.length;
  selectAll.checked = Boolean(eligible.length && checked === eligible.length);
  selectAll.indeterminate = checked > 0 && checked < eligible.length;
  byId("batch-download").disabled = !online || scheduling || !checked;
  byId("batch-download").textContent = checked ? "Скачать выбранные (" + checked + ")" : "Скачать выбранные";
  byId("download-all").disabled = !online || scheduling || busy || !eligible.length;
  byId("batch-covers").disabled = !online || scheduling || !shown.some((row) => row.check.checked && available(row, "cover"));
  const path = latest?.folders[platform] || "—";
  byId("output-folder").textContent = path;
  byId("output-folder").title = path;
  byId("choose-folder").disabled = !desktop || !online || changingFolder || scheduling;
  byId("open-downloads").disabled = !online || path === "—";
  byId("firefox-label").hidden = byId("firefox-help").hidden = !desktop || platform === "youtube";
  byId("firefox-session").checked = Boolean(latest?.firefox[platform]);
  const visible = new Set(shown);
  for (const row of rows.values()) {
    row.el.hidden = !visible.has(row);
    row.check.hidden = false;
    row.check.disabled = !available(row, "video") && !available(row, "cover");
    row.remove.hidden = false;
    row.remove.disabled = !online || ["loading", "quality_queued"].includes(row.video.state) || Boolean(row.pending.size);
    row.saveVideo.disabled = !online || scheduling || !available(row, "video");
    row.saveCover.hidden = Boolean(row.video.photo);
    row.saveCover.disabled = !online || scheduling || !available(row, "cover");
    row.quality.disabled = !online || !row.quality.value;
    row.loadQuality.disabled = !online || row.video.state === "loading";
    row.retry.disabled = !online || searching;
  }
}

async function schedule(items, kind, downloadSingle = false) {
  if (!online || scheduling || !items.length) return;
  scheduling = true;
  tell();
  renderControls();
  const sourcePlatform = items[0].video.platform;
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
        const data = await api("jobs", "POST", { ...group, platform: sourcePlatform, items: group.items.slice(offset, offset + 50) });
        created += data.created;
        for (const skip of data.skipped) reasons.add(skip.reason);
        if (downloadSingle && data.jobs.length === 1) autoDownloads.add(data.jobs[0]);
      }
    }
    if (created) platformViews.set(sourcePlatform, "queue");
    if (reasons.size) tell([...reasons].join(" "), !created);
    else tell("Добавлено в очередь · " + created);
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
  const sourcePlatform = platform;
  searching = true;
  stateText.textContent = "Ищем видео…";
  byId("find-single").disabled = true;
  byId("find-batch").disabled = true;
  renderControls();
  try {
    if (!online && !await refresh()) throw new Error("Сначала подключите загрузчик.");
    const data = await api("videos", "POST", { links: values, platform: sourcePlatform });
    if (platform === sourcePlatform && data.rejected.length) {
      if (field === links) {
        const badLines = data.rejected.map(({ line }) => line);
        field.value = badLines.map((number) => values[number - 1]).join("\n");
        showError("Проверьте ссылки в строках: " + badLines.join(", ") + ".");
      } else if (field) {
        showError("Проверьте ссылку и выбранную площадку.");
      }
    } else if (platform === sourcePlatform && field === links && !data.rejected.length) {
      field.value = "";
    }
    if (data.accepted.length) {
      if (field) {
        const tab = tabs[sourcePlatform];
        tab.category = "all";
        tab.ids = new Set([...(field === links && tab.ids ? tab.ids : []), ...data.accepted]);
      }
      platformViews.set(sourcePlatform, "materials");
      await refresh();
    }
  } catch (error) {
    if (field && platform === sourcePlatform) showError(error.message);
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
    showError(values.length > 50 ? "Добавьте до 50 ссылок за раз." : "Вставьте ссылку на материал или профиль.");
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
sourceQuality.addEventListener("change", () => {
  tabs[platform].quality = sourceQuality.value;
  for (const row of rows.values()) if (row.video.platform === platform && !row.qualityEdited) syncRow(row);
});
function outputQualitySlot() {
  if (batch) byId("batch-quality").append(sourceQuality);
  else byId("find-single").before(sourceQuality);
}
byId("source-example").addEventListener("click", () => {
  const examples = { youtube: "https://www.youtube.com/watch?v=jNQXAC9IVRw", instagram: "https://www.instagram.com/instagram/", tiktok: "https://www.tiktok.com/@tiktok" };
  const field = batch ? links : link;
  if (!field.value.trim()) field.value = examples[platform];
  field.focus();
  field.select();
});
byId("batch-toggle").addEventListener("click", (event) => {
  showError();
  tell();
  batch = !batch;
  byId("single-input").hidden = batch;
  byId("batch-input").hidden = !batch;
  outputQualitySlot();
  event.currentTarget.setAttribute("aria-pressed", String(batch));
  event.currentTarget.querySelector("span").textContent = batch ? "Одна ссылка" : "Пакетный ввод";
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
  for (const row of shownRows()) {
    if (!row.check.disabled) row.check.checked = selectAll.checked;
  }
  renderControls();
});
byId("batch-download").addEventListener("click", () => schedule(shownRows().filter((row) => row.check.checked && available(row, "video")), "video"));
byId("download-all").addEventListener("click", () => schedule(shownRows().filter((row) => available(row, "video")), "video"));
byId("batch-covers").addEventListener("click", () => schedule(shownRows().filter((row) => row.check.checked && available(row, "cover")), "cover"));

byId("open-downloads").addEventListener("click", () => {
  window.openVideoportDownloads?.(latest.folders[platform]).catch((error) => tell(String(error), true));
});

async function chooseFolder(tab) {
  if (!window.chooseVideoportFolder || !latest || changingFolder) return;
  changingFolder = true;
  renderControls();
  try {
    const folder = await window.chooseVideoportFolder(latest.folders[tab]);
    if (folder) {
      await api("settings/" + tab, "POST", { folder });
      await refresh();
    }
  } catch (error) {
    tell(String(error), true);
  } finally {
    changingFolder = false;
    renderControls();
  }
}
byId("choose-folder").addEventListener("click", () => chooseFolder(platform));

function renderSettingsFolders() {
  const node = byId("settings-folders");
  const signature = JSON.stringify([latest?.folders, latest?.firefox, changingFolder, desktop, online]);
  if (node.dataset.signature === signature) return;
  node.dataset.signature = signature;
  node.replaceChildren(...Object.entries(platforms).map(([key, [name]]) => {
    const row = element("div", "", "setting-line");
    const location = element("div");
    location.append(element("strong", name), element("p", latest?.folders[key] || "—"));
    if (key === "youtube" && desktop) {
      const session = element("label", "", "check-label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(latest?.firefox[key]);
      input.disabled = !online;
      input.addEventListener("change", () => saveFirefox(key, input));
      session.append(input, "Сессия Firefox");
      location.append(session);
    }
    const change = button("Изменить", () => chooseFolder(key), "secondary");
    change.disabled = !desktop || !online || changingFolder;
    row.append(location, change);
    return row;
  }));
}

async function saveFirefox(key, input) {
  try {
    await api("settings/" + key, "POST", { firefox: input.checked });
    await refresh();
  } catch (error) {
    input.checked = Boolean(latest?.firefox[key]);
    tell(String(error), true);
    renderControls();
  }
}
byId("firefox-session").addEventListener("change", (event) => saveFirefox(platform, event.target));

function switchPlatform(next) {
  if (next !== platform) {
    Object.assign(tabs[platform], { link: link.value, links: links.value, batch });
    showError();
    tell();
    platform = next;
    const tab = tabs[platform];
    const [name, hint, example] = platforms[platform];
    link.value = tab.link;
    links.value = tab.links;
    link.placeholder = links.placeholder = example;
    byId("page-title").firstElementChild.textContent = "Загрузки " + name;
    mediaMain.querySelector(".subtitle").textContent = hint;
    document.querySelector("label[for='link']").textContent = "Ссылка на " + name;
    document.querySelector("label[for='links']").textContent = "Ссылки на " + name;
    batch = tab.batch;
    sourceQuality.value = tab.quality;
    byId("single-input").hidden = batch;
    byId("batch-input").hidden = !batch;
    outputQualitySlot();
    byId("batch-toggle").setAttribute("aria-pressed", String(batch));
    byId("batch-toggle").querySelector("span").textContent = batch ? "Одна ссылка" : "Пакетный ввод";
  }
  renderControls();
}

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

const platformRoutes = [...document.querySelectorAll("[data-platform]")].map((tab) => ({
  page: mediaMain, tab, show: () => switchPlatform(tab.dataset.platform),
}));
setupStudioNavigation([
  ...platformRoutes,
  { tab: byId("show-downloads"), page: downloadsMain, show: () => { managerMessage(); renderControls(); } },
  setupClips(api), setupTranscript(api), setupScripts(api),
  { tab: byId("open-settings"), page: byId("settings-main"), show: renderSettingsFolders },
]);
byId("open-queue").addEventListener("click", () => byId("show-downloads").click());
setupUpdates(api);
renderControls();

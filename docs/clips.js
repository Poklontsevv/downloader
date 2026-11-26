import { mountClipEditor } from "./clip-editor.js";

export function setupClips(api) {
  const byId = (id) => document.getElementById(id);
  const open = byId("open-clips");
  const page = byId("clip-main");
  const player = byId("clip-player");
  const items = byId("clip-items");
  const downloads = byId("clip-downloads");
  let source = "";
  let folder = "";
  let state = {};
  let signature = "";
  let downloadSignature = "";
  let clipTimer;
  let transcriptRequest;
  let requestedPreview;
  let activePreview;
  const editors = new WeakMap();
  const controls = ["clip-pick", "clip-downloads", "clip-length", "clip-count", "clip-language",
    "clip-semantic", "clip-analyse", "clip-folder", "clip-export"];

  function showError(message = "") {
    const label = byId("clip-error");
    label.textContent = message;
    label.hidden = !message;
  }

  function showField(id, text) {
    const element = byId(id);
    if (element.textContent !== (text || "")) element.textContent = text || "";
    element.hidden = !text;
  }

  function enabled() {
    const disabled = Boolean(state.busy);
    for (const id of controls) byId(id).disabled = disabled;
    byId("clip-analyse").disabled = disabled || !source && !downloads.value;
    byId("clip-export").disabled = disabled || !items.querySelector(".include-clip:checked");
    for (const button of items.querySelectorAll(".clip-watch, .clip-save")) button.disabled = disabled;
    for (const card of items.querySelectorAll(".clip-card")) editors.get(card).setBusy(disabled);
  }

  function getTranscript() {
    transcriptRequest ||= api("clip/transcript").catch((error) => {
      transcriptRequest = null;
      throw error;
    });
    return transcriptRequest;
  }

  function formClip(card) {
    return { title: card.querySelector(".clip-title-input").value, ...editors.get(card).value() };
  }

  function previewIsCurrent(card) {
    return activePreview?.card === card && activePreview.key === player.dataset.key
      && activePreview.draft === JSON.stringify(editors.get(card).value());
  }

  function showPreviewHint() {
    let hint = "";
    if (player.hidden) hint = "Нажмите «Превью» у клипа.";
    else if (activePreview && !previewIsCurrent(activePreview.card)) {
      hint = "Монтаж изменился. Нажмите «Превью», чтобы обновить видео.";
    }
    showField("clip-preview-hint", hint);
  }

  async function submit(operation, data, card) {
    if (state.busy) return;
    showError();
    clearTimeout(clipTimer);
    state.busy = true;
    if (operation === "preview") {
      requestedPreview = { card, draft: JSON.stringify(editors.get(card).value()) };
    }
    enabled();
    try {
      await api("clip/" + operation, "POST", data);
      if (operation === "analyse") {
        signature = "";
        transcriptRequest = null;
        activePreview = null;
        requestedPreview = null;
        items.replaceChildren();
        player.pause();
        player.removeAttribute("src");
        player.hidden = true;
        byId("clip-results").hidden = true;
      }
      await refresh();
    } catch (error) {
      state.busy = false;
      showError(error.message);
      enabled();
    }
  }

  function addCard(clip) {
    const card = byId("clip-template").content.firstElementChild.cloneNode(true);
    card.querySelector(".clip-title-input").value = clip.title;
    card.querySelector(".clip-reason").textContent = clip.reason;
    editors.set(card, mountClipEditor(card, clip, {
      duration: state.duration, getTranscript, player,
      isCurrent: () => previewIsCurrent(card), onEdit: showPreviewHint,
    }));
    card.querySelector(".include-clip").addEventListener("change", enabled);
    card.querySelector(".clip-watch").addEventListener("click", () => submit("preview", { clips: [formClip(card)] }, card));
    card.querySelector(".clip-save").addEventListener("click", () => submit("export", { clips: [formClip(card)], folder }));
    items.append(card);
  }

  function showFiles(files) {
    const list = byId("clip-files");
    const next = JSON.stringify(files);
    if (list.dataset.files === next) return;
    list.dataset.files = next;
    list.replaceChildren();
    for (const file of files) {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button text-button";
      button.textContent = file.split(/[\\/]/).pop();
      button.title = file;
      button.addEventListener("click", () => window.openVideoportFile(file).catch((error) => showError(String(error))));
      row.append(button);
      list.append(row);
    }
    if (files.length) {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "button secondary";
      button.textContent = "Открыть папку";
      button.addEventListener("click", () => window.openVideoportDownloads(state.folder).catch((error) => showError(String(error))));
      row.append(button);
      list.append(row);
    }
  }

  async function refresh() {
    clearTimeout(clipTimer);
    if (page.hidden) return;
    try {
      state = await api("clip/state");
      const status = state.stage + (Number.isFinite(state.percent) && state.busy ? " · " + state.percent + "%" : "");
      showField("clip-status", status);
      showField("clip-warning", state.warning);
      showError(state.error);
      const progress = byId("clip-progress");
      progress.hidden = !state.busy;
      if (Number.isFinite(state.percent)) progress.value = state.percent;
      else progress.removeAttribute("value");
      const nextDownloads = JSON.stringify(state.downloads);
      if (nextDownloads !== downloadSignature) {
        const selected = downloads.value;
        downloads.replaceChildren(new Option("Выберите готовый файл", ""),
          ...[...state.downloads].reverse().map((file) => new Option(file.name, file.id)));
        downloads.value = selected;
        downloadSignature = nextDownloads;
      }
      if (!folder) folder = state.folder;
      showField("clip-location", folder);
      const next = JSON.stringify(state.clips);
      if (signature !== next) {
        signature = next;
        items.replaceChildren();
        for (const clip of state.clips) addCard(clip);
      }
      byId("clip-results").hidden = !state.clips.length;
      if (state.preview && player.dataset.key !== state.preview) {
        player.dataset.key = state.preview;
        activePreview = requestedPreview ? { ...requestedPreview, key: state.preview } : null;
        requestedPreview = null;
        player.src = "/clip-video/" + state.preview;
        player.hidden = false;
        if (activePreview && window.innerWidth <= 660) player.scrollIntoView({ block: "nearest" });
      } else if (!state.preview) {
        player.pause();
        player.hidden = true;
        if (player.hasAttribute("src")) {
          player.removeAttribute("src");
          player.load();
        }
        delete player.dataset.key;
        activePreview = null;
      }
      showPreviewHint();
      showFiles(state.files);
      enabled();
      if (!page.hidden) clipTimer = setTimeout(refresh, state.busy ? 700 : 5000);
    } catch (error) {
      showError(error.message);
      state.busy = false;
      enabled();
    }
  }

  player.addEventListener("timeupdate", () => {
    if (activePreview) editors.get(activePreview.card).playback(player.currentTime);
  });

  open.addEventListener("click", () => {
    if (!page.hidden) return;
    page.hidden = false;
    open.setAttribute("aria-pressed", "true");
    for (const tab of document.querySelectorAll("[data-platform]")) tab.setAttribute("aria-pressed", "false");
    byId("media-main").hidden = true;
    document.querySelector(".download-footer").hidden = true;
    document.body.classList.add("clip-active");
    window.scrollTo(0, 0);
    refresh();
  });
  for (const tab of document.querySelectorAll("[data-platform]")) {
    tab.addEventListener("click", () => {
      if (page.hidden) return;
      page.hidden = true;
      player.pause();
      clearTimeout(clipTimer);
      open.setAttribute("aria-pressed", "false");
      tab.setAttribute("aria-pressed", "true");
      byId("media-main").hidden = false;
      document.querySelector(".download-footer").hidden = false;
      document.body.classList.remove("clip-active");
    });
  }
  byId("clip-pick").addEventListener("click", async () => {
    try {
      const path = await window.chooseVideoportVideo();
      if (!path) return;
      downloads.value = "";
      source = path;
      showField("clip-name", source);
      enabled();
    } catch (error) {
      showError(String(error));
    }
  });
  downloads.addEventListener("change", () => {
    source = "";
    showField("clip-name", downloads.value ? downloads.selectedOptions[0].textContent : "");
    enabled();
  });
  byId("clip-analyse").addEventListener("click", () => {
    const number = byId("clip-count");
    if (!number.reportValidity()) return;
    submit("analyse", { file: source, id: downloads.value, length: byId("clip-length").value,
      number: number.value, language: byId("clip-language").value, semantic: byId("clip-semantic").checked });
  });
  byId("clip-folder").addEventListener("click", async () => {
    try {
      folder = await window.chooseVideoportFolder(folder) || folder;
      showField("clip-location", folder);
    } catch (error) {
      showError(String(error));
    }
  });
  byId("clip-export").addEventListener("click", () => {
    const clips = [...items.querySelectorAll(".include-clip:checked")].map((check) => formClip(check.closest(".clip-card")));
    submit("export", { clips, folder });
  });
  enabled();
}

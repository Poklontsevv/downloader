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
    for (const button of items.querySelectorAll(".clip-watch")) button.disabled = disabled;
  }

  function formClip(card) {
    const find = (name) => card.querySelector(".clip-" + name);
    return { start: find("start").value, end: find("end").value, title: find("title-input").value,
      captions: find("captions").value, layout: find("mode").value,
      autoCaptions: card.dataset.editedCaptions !== "yes" };
  }

  async function submit(operation, data) {
    if (state.busy) return;
    showError();
    clearTimeout(clipTimer);
    state.busy = true;
    enabled();
    try {
      await api("clip/" + operation, "POST", data);
      if (operation === "analyse") {
        signature = "";
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
    const set = (id, value) => { card.querySelector(id).value = value; };
    set(".clip-start", clip.start);
    set(".clip-end", clip.end);
    set(".clip-title-input", clip.title);
    set(".clip-captions", clip.captions);
    card.querySelector(".clip-captions").addEventListener("input", () => { card.dataset.editedCaptions = "yes"; });
    card.querySelector(".clip-reason").textContent = clip.reason;
    card.querySelector(".include-clip").addEventListener("change", enabled);
    card.querySelector(".clip-watch").addEventListener("click", () => submit("preview", { clips: [formClip(card)] }));
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
        player.src = "/clip-video/" + state.preview;
        player.hidden = false;
        byId("clip-preview-hint").hidden = true;
      } else if (!state.preview) {
        player.pause();
        player.hidden = true;
        if (player.hasAttribute("src")) {
          player.removeAttribute("src");
          player.load();
        }
        delete player.dataset.key;
        byId("clip-preview-hint").hidden = false;
      }
      showFiles(state.files);
      enabled();
      if (!page.hidden) clipTimer = setTimeout(refresh, state.busy ? 700 : 5000);
    } catch (error) {
      showError(error.message);
      state.busy = false;
      enabled();
    }
  }

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

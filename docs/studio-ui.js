export const byId = (id) => document.getElementById(id);

export function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content) node.textContent = content;
  if (className) node.className = className;
  return node;
}

export function icon(name) {
  const namespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(namespace, "svg");
  const use = document.createElementNS(namespace, "use");
  svg.setAttribute("class", "ui-icon");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", "#u-" + name);
  svg.append(use);
  return svg;
}

export function button(label, command, variant = "quiet") {
  const node = element("button", label, "button " + variant);
  node.type = "button";
  node.addEventListener("click", command);
  return node;
}

export function feedback(node, message = "", error = false) {
  node.textContent = message;
  node.hidden = !message;
  node.dataset.error = String(error);
}

export function timecode(value) {
  let seconds = Math.floor(Math.max(0, value || 0));
  const hours = Math.floor(seconds / 3600);
  seconds %= 3600;
  const minutes = Math.floor(seconds / 60);
  const pad = (unit) => String(unit).padStart(2, "0");
  return (hours ? hours + ":" : "") + (hours ? pad(minutes) : minutes) + ":" + pad(seconds % 60);
}

export function setOptions(select, items, value = select.value) {
  const signature = JSON.stringify(items);
  if (select.dataset.options === signature) return;
  select.dataset.options = signature;
  select.replaceChildren(...items.map(([key, label]) => new Option(label, key, false, key === value)));
}

export function setupStudioNavigation(views) {
  let current = views[0];
  const pages = new Set(views.map((view) => view.page));
  function show(view) {
    if (current !== view) current.hide?.();
    current = view;
    for (const page of pages) page.hidden = page !== view.page;
    for (const item of views) {
      item.tab.setAttribute("aria-pressed", String(item === view));
    }
    byId("workspace-title").textContent = view.tab.getAttribute("aria-label");
    document.querySelector(".download-footer").hidden = view.page.id !== "media-main";
    window.scrollTo(0, 0);
    view.show?.();
  }
  for (const item of views) item.tab.addEventListener("click", () => show(item));
  byId("home").addEventListener("click", () => show(views[0]));
  for (const close of document.querySelectorAll("[data-close-dialog]")) {
    close.addEventListener("click", () => close.closest("dialog").close());
  }
}

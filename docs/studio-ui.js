export const byId = (id) => document.getElementById(id);

export function element(tag, content, className) {
  const node = document.createElement(tag);
  if (content) node.textContent = content;
  if (className) node.className = className;
  return node;
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
  function show(view) {
    for (const item of views) {
      const open = item === view;
      item.tab.setAttribute("aria-pressed", String(open));
      if (!open && !item.page.hidden) item.hide();
      item.page.hidden = !open;
    }
    if (view) {
      for (const tab of document.querySelectorAll("[data-platform],#open-clips")) tab.setAttribute("aria-pressed", "false");
      byId("media-main").hidden = true;
      document.querySelector(".download-footer").hidden = true;
      document.body.classList.add("clip-active");
      document.documentElement.scrollTop = 0;
      view.show();
    }
  }
  for (const item of views) item.tab.addEventListener("click", () => show(item));
  for (const tab of document.querySelectorAll("[data-platform], #open-clips")) {
    tab.addEventListener("click", () => {
      const isOpen = views.some((view) => !view.page.hidden);
      show(null);
      if (isOpen && tab.dataset.platform) {
        byId("media-main").hidden = false;
        document.querySelector(".download-footer").hidden = false;
        document.body.classList.remove("clip-active");
      }
    });
  }
  for (const close of document.querySelectorAll("[data-close-dialog]")) {
    close.addEventListener("click", () => close.closest("dialog").close());
  }
}

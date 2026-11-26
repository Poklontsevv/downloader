const fontFaces = new Map();
const builtin = { blocks: "VP Blocks", manrope: "VP Manrope", oswald: "VP Oswald" };

function family(font) {
  if (builtin[font]) return `"${builtin[font]}", "VP Manrope", sans-serif`;
  const name = "VP Custom " + font;
  if (!fontFaces.has(font)) {
    const face = new FontFace(name, `url("/clip-font/${font}")`);
    const load = face.load().then((loaded) => document.fonts.add(loaded)).catch(() => fontFaces.delete(font));
    fontFaces.set(font, load);
  }
  return `"${name}", "VP Manrope", sans-serif`;
}

export function liveClipPlayer(root, source, onPosition) {
  const video = root.querySelector(".editor-video");
  const play = root.querySelector(".editor-play");
  const status = root.querySelector(".editor-video-status");
  const caption = root.querySelector(".editor-overlay span");
  const volume = root.querySelector(".editor-volume");
  let parts = [];
  let index = 0;
  let position = 0;
  let total = 0;
  let seeking = false;
  let playing = false;
  let frame;
  let events = [];
  let captionStyle;
  let displayed;
  let disposed = false;

  function overlay(time) {
    const visible = events.findLast((cue) => cue.start <= time && time < cue.end);
    if (visible === displayed) return;
    displayed = visible;
    caption.textContent = visible?.text || "";
    caption.hidden = !visible;
    if (visible) {
      const longest = Math.max(1, ...visible.text.split("\n").map((line) => [...line].length));
      const size = captionStyle.mode === "phrase" ? captionStyle.size
        : Math.min(captionStyle.size, Math.max(1, Math.round(980 / (longest * .96))));
      caption.style.fontSize = (size / 10.8) + "cqw";
    }
  }

  function announce(time) {
    position = time;
    onPosition(time);
    overlay(time);
  }

  function stop() {
    playing = false;
    video.pause();
    cancelAnimationFrame(frame);
    play.textContent = "Воспроизвести";
  }

  function jump(time) {
    if (!parts.length) return;
    time = Math.min(Math.max(0, time), total);
    index = parts.findIndex((part) => time < part.offset + part.end - part.start);
    if (index < 0) index = parts.length - 1;
    const part = parts[index];
    announce(time);
    if (video.readyState) {
      const target = Math.min(part.end - .001, part.start + time - part.offset);
      if (Math.abs(video.currentTime - target) > .01) {
        seeking = true;
        video.pause();
        video.currentTime = target;
      }
    }
  }

  function tick() {
    if (!playing || disposed) return;
    if (!seeking && !video.seeking && parts[index]) {
      const part = parts[index];
      if (video.currentTime >= part.end - .025 || video.ended) {
        if (index + 1 >= parts.length) {
          stop();
          announce(total);
          return;
        }
        jump(parts[index + 1].offset + .001);
        if (!seeking && video.paused) playVideo();
      } else {
        announce(part.offset + Math.max(0, video.currentTime - part.start));
      }
    }
    frame = requestAnimationFrame(tick);
  }

  function playVideo() {
    video.play().catch((error) => {
      if (error.name !== "AbortError" && playing) {
        stop();
        status.textContent = "Не удалось начать воспроизведение.";
      }
    });
  }

  play.addEventListener("click", () => {
    if (playing) {
      stop();
      return;
    }
    if (position >= total - .025) jump(0);
    playing = true;
    play.textContent = "Пауза";
    if (!seeking) playVideo();
    frame = requestAnimationFrame(tick);
  });
  video.addEventListener("seeked", () => {
    seeking = false;
    if (playing) playVideo();
  });
  video.addEventListener("loadedmetadata", () => {
    status.textContent = "";
    play.disabled = !parts.length;
    jump(position);
  });
  video.addEventListener("error", () => {
    if (disposed) return;
    stop();
    play.disabled = true;
    status.textContent = "Не удалось открыть предпросмотр. Проверьте исходный файл.";
  });
  volume.addEventListener("input", () => { video.volume = Number(volume.value); });
  video.addEventListener("click", () => { if (!play.disabled) play.click(); });
  play.disabled = true;
  status.textContent = "Открываем видео…";
  video.src = source;

  return {
    track(segments, layout, time) {
      stop();
      let next = 0;
      parts = segments.filter((part) => Number.isFinite(part.start) && Number.isFinite(part.end) && part.end > part.start)
        .map((part) => {
          const result = { ...part, offset: next };
          next += part.end - part.start;
          return result;
        });
      total = next;
      play.disabled = !video.readyState || !parts.length;
      video.style.objectFit = layout === "fit" ? "contain" : "cover";
      jump(time);
    },
    captions(nextEvents, style) {
      events = nextEvents;
      captionStyle = style;
      const overlayBox = caption.parentElement;
      overlayBox.style.top = style.position + "%";
      caption.style.fontFamily = family(style.font);
      caption.style.color = style.color;
      caption.style.backgroundColor = style.background ? style.stroke + "c7" : "transparent";
      caption.style.whiteSpace = style.mode === "phrase" ? "pre-wrap" : "pre";
      caption.style.webkitTextStroke = (style.background ? 0 : style.outline / 10.8) + "cqw " + style.stroke;
      caption.style.textShadow = style.shadow ? "0 " + (style.shadow / 10.8) + "cqw .15em #0009" : "none";
      displayed = null;
      overlay(position);
    },
    seek: jump,
    close() {
      disposed = true;
      stop();
      video.removeAttribute("src");
      video.load();
    },
  };
}

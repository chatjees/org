// ChatJEEs front end: chat state, streaming over Server-Sent Events, Markdown + KaTeX rendering.
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const chatEl = $("chat");
  const inputEl = $("input");
  const sendEl = $("send");
  const fileEl = $("file");
  const previewEl = $("preview");
  const previewImg = $("preview-img");
  const subjectEl = $("subject");

  const state = {
    mode: "doubt",
    history: [],        // [{role, content, image?}] sent to the server each turn
    pendingImage: null, // data URL of the image attached to the next message
    busy: false,
  };

  // ---------- rendering ----------
  const escapeHtml = (s) =>
    s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Pull $$...$$ and $...$ out before Markdown so the parser cannot mangle them.
  function renderRich(text) {
    const maths = [];
    const protectedText = text
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => `@@MATH${maths.push({ tex, display: true }) - 1}@@`)
      .replace(/(^|[^\\$])\$([^\n$]+?)\$/g, (_, pre, tex) => `${pre}@@MATH${maths.push({ tex, display: false }) - 1}@@`);

    let html;
    if (window.marked && window.DOMPurify) {
      html = window.DOMPurify.sanitize(window.marked.parse(protectedText, { breaks: true }));
    } else {
      html = escapeHtml(protectedText).replace(/\n/g, "<br>");
    }

    return html.replace(/@@MATH(\d+)@@/g, (_, i) => {
      const { tex, display } = maths[Number(i)];
      if (window.katex) {
        try {
          return window.katex.renderToString(tex, { displayMode: display, throwOnError: false });
        } catch (_) { /* fall through to raw TeX */ }
      }
      return escapeHtml(display ? `$$${tex}$$` : `$${tex}$`);
    });
  }

  function hideWelcome() {
    const w = $("welcome");
    if (w) w.remove();
  }

  function addBubble(role, text, image) {
    hideWelcome();
    const row = document.createElement("div");
    row.className = `msg ${role}`;
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (image) {
      const img = document.createElement("img");
      img.src = image;
      img.alt = "Question image";
      bubble.appendChild(img);
    }
    const body = document.createElement("div");
    body.className = "body";
    body.innerHTML = role === "user" ? renderRich(text) : "";
    bubble.appendChild(body);
    row.appendChild(bubble);
    chatEl.appendChild(row);
    scrollDown();
    return body;
  }

  function scrollDown() {
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  // ---------- server-sent events over fetch ----------
  async function streamChat(payload, onEvent) {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok || !res.body) {
      let msg = `Server error (${res.status})`;
      try { msg = (await res.json()).error || msg; } catch (_) { /* not JSON */ }
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        let event = "message";
        let data = "";
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event: ")) event = line.slice(7);
          else if (line.startsWith("data: ")) data += line.slice(6);
        }
        if (data) onEvent(event, JSON.parse(data));
      }
    }
  }

  // ---------- sending ----------
  async function send(text) {
    text = text.trim();
    if (state.busy || (!text && !state.pendingImage)) return;
    state.busy = true;
    sendEl.disabled = true;

    const image = state.pendingImage;
    const userMsg = { role: "user", content: text || "Solve the question in this image." };
    if (image) userMsg.image = image;
    addBubble("user", userMsg.content, image);
    state.history.push(userMsg);
    clearImage();
    inputEl.value = "";
    autoGrow();

    const body = addBubble("assistant", "");
    body.classList.add("typing");
    let answer = "";
    let pendingFrame = false;
    const paint = () => {
      pendingFrame = false;
      body.innerHTML = renderRich(answer);
      scrollDown();
    };

    try {
      await streamChat(
        { subject: subjectEl.value, mode: state.mode, messages: state.history },
        (event, data) => {
          if (event === "delta") {
            answer += data.text;
            if (!pendingFrame) { pendingFrame = true; requestAnimationFrame(paint); }
          } else if (event === "error") {
            answer += `\n\n<span class="error-text">⚠️ ${escapeHtml(data.message)}</span>`;
          } else if (event === "notice") {
            answer += `\n\n<span class="notice-text">${escapeHtml(data.message)}</span>`;
          }
        }
      );
    } catch (err) {
      answer += `\n\n<span class="error-text">⚠️ ${escapeHtml(err.message)}</span>`;
    }

    paint();
    body.classList.remove("typing");
    // Images are only sent with the turn they were attached to; keep history light.
    delete userMsg.image;
    state.history.push({ role: "assistant", content: answer.replace(/<span class="(error|notice)-text">[\s\S]*?<\/span>/g, "").trim() });
    state.busy = false;
    sendEl.disabled = false;
    inputEl.focus();
  }

  // ---------- images ----------
  // Downscale large photos so uploads stay small and fast on mobile data.
  function loadImage(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Could not read the image."));
      reader.onload = () => {
        const img = new Image();
        img.onerror = () => reject(new Error("Unsupported image."));
        img.onload = () => {
          const maxSide = 1600;
          const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
          const canvas = document.createElement("canvas");
          canvas.width = Math.round(img.width * scale);
          canvas.height = Math.round(img.height * scale);
          canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
          resolve(canvas.toDataURL("image/jpeg", 0.85));
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    });
  }

  function clearImage() {
    state.pendingImage = null;
    previewEl.hidden = true;
    previewImg.removeAttribute("src");
    fileEl.value = "";
  }

  fileEl.addEventListener("change", async () => {
    const file = fileEl.files && fileEl.files[0];
    if (!file) return;
    try {
      state.pendingImage = await loadImage(file);
      previewImg.src = state.pendingImage;
      previewEl.hidden = false;
    } catch (err) {
      alert(err.message);
      clearImage();
    }
  });
  $("remove-img").addEventListener("click", clearImage);

  // ---------- UI wiring ----------
  function autoGrow() {
    inputEl.style.height = "auto";
    inputEl.style.height = `${Math.min(inputEl.scrollHeight, 180)}px`;
  }
  inputEl.addEventListener("input", autoGrow);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(inputEl.value);
    }
  });
  $("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    send(inputEl.value);
  });

  $("modes").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-mode]");
    if (!btn) return;
    state.mode = btn.dataset.mode;
    for (const b of $("modes").querySelectorAll("button")) b.classList.toggle("active", b === btn);
    if (state.mode === "practice" && !state.busy) {
      send(`Give me a practice question${subjectEl.value === "general" ? "" : ` in ${subjectEl.options[subjectEl.selectedIndex].text}`}.`);
    }
  });

  for (const ex of document.querySelectorAll(".example")) {
    ex.dataset.q = ex.textContent;
    ex.addEventListener("click", () => send(ex.dataset.q));
  }

  $("new-chat").addEventListener("click", () => {
    if (state.busy) return;
    state.history = [];
    clearImage();
    location.reload();
  });

  // Render maths in the welcome examples once KaTeX has loaded.
  window.addEventListener("load", () => {
    for (const ex of document.querySelectorAll(".example")) ex.innerHTML = renderRich(ex.textContent);
  });
})();

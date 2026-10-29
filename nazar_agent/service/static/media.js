"use strict";
// NAZAR Agent — photos and the camera, the same everywhere in the window:
// drop a file, paste it (Ctrl+V), choose it, or take it with the webcam or the
// phone's camera. Uses el() and t() from app.js and i18n.js at call time.

const zoneHandlers = new WeakMap(); // zone element → what to do with a file
let lastZone = null; // the zone a paste goes to: the one last pointed at

// Whether a file is one the zone takes: "image/*", "image/png" or ".xlsx".
function fits(file, accept) {
  const name = (file.name || "").toLowerCase();
  return accept.split(",").map((item) => item.trim().toLowerCase()).some((item) =>
    item.endsWith("/*") ? (file.type || "").startsWith(item.slice(0, -1))
      : item.startsWith(".") ? name.endsWith(item) : file.type === item);
}

// A zone: `hint` is the dictionary key of its text; `compact` is one row of buttons.
function photoZone({ accept = "image/*", facing = "environment", compact = false, hint = "zone_hint", onFile }) {
  const input = el("input", { type: "file", accept, hidden: "" });
  const choose = el("button", { type: "button", "data-i18n": "zone_choose" }, t("zone_choose"));
  const camera = el("button", { type: "button" }, "📷 ", el("span", { "data-i18n": "zone_camera" }, t("zone_camera")));
  const message = el("div", { class: "zone-msg", role: "status" });
  const zone = el("div", { class: compact ? "zone compact" : "zone" },
    compact ? null : el("div", { class: "zone-hint", "data-i18n": hint }, t(hint)),
    el("div", { class: "row" }, choose, camera), message, input);

  const take = (file) => {
    message.textContent = "";
    if (!file) return;
    if (!fits(file, accept)) { message.textContent = t("zone_wrong"); return; }
    onFile(file);
  };
  zoneHandlers.set(zone, take);
  choose.addEventListener("click", () => input.click());
  input.addEventListener("change", () => { take(input.files[0]); input.value = ""; });
  camera.addEventListener("click", async () => {
    try { take(await openCamera(facing)); } catch (error) { message.textContent = error.message; }
  });
  for (const kind of ["pointerenter", "focusin"]) zone.addEventListener(kind, () => { lastZone = zone; });
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("over"); lastZone = zone; });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("over");
    take(event.dataTransfer.files[0]);
  });
  return zone;
}

// A pasted picture goes to the zone last pointed at, or to the first one on screen.
document.addEventListener("paste", (event) => {
  const file = [...(event.clipboardData ? event.clipboardData.files : [])][0];
  if (!file) return;
  const shown = (zone) => zone && zone.isConnected && zone.offsetParent !== null;
  const zone = shown(lastZone) ? lastZone : [...document.querySelectorAll(".zone")].find(shown);
  if (!zone) return;
  event.preventDefault();
  zoneHandlers.get(zone)(file);
});

// The webcam or the phone's camera, in a dialog; resolves with a JPEG, or null if cancelled.
async function openCamera(facing = "environment") {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error(t("camera_denied"));
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing, width: { ideal: 1280 } }, audio: false });
  } catch {
    throw new Error(t("camera_denied"));
  }
  return new Promise((resolve) => {
    const video = el("video", { autoplay: "", playsinline: "" });
    video.muted = true;
    video.srcObject = stream;
    const shoot = el("button", { class: "primary", type: "button" }, t("camera_take"));
    const cancel = el("button", { type: "button" }, t("camera_cancel"));
    const dialog = el("div", { class: "camera-overlay", role: "dialog", "aria-modal": "true" },
      el("div", { class: "camera-box" }, video, el("div", { class: "row" }, shoot, cancel)));
    const close = (file) => {
      for (const track of stream.getTracks()) track.stop();
      dialog.remove();
      document.removeEventListener("keydown", onKey);
      resolve(file);
    };
    const onKey = (event) => { if (event.key === "Escape") close(null); };
    shoot.addEventListener("click", () => {
      if (!video.videoWidth) return; // the camera has not started yet
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      canvas.toBlob((blob) => close(blob ? new File([blob], "camera.jpg", { type: "image/jpeg" }) : null), "image/jpeg", 0.92);
    });
    cancel.addEventListener("click", () => close(null));
    document.addEventListener("keydown", onKey);
    document.body.append(dialog);
    shoot.focus();
  });
}

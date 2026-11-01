"use strict";
// NAZAR Agent — the window's icons: one line weight, drawn in the text's colour
// (violet where the page says so), the same on every system — emoji are not.
// Loaded after i18n.js on both pages.

const ICONS = {
  "user-x": '<circle cx="9" cy="8" r="4"/><path d="M3 21v-1a6 6 0 0 1 12 0v1"/><path d="M17 9l4 4M21 9l-4 4"/>',
  cash: '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6.5 9.5h.01M17.5 14.5h.01"/>',
  "map-pin": '<path d="M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  shield: '<path d="M12 3l7.5 3v6c0 4.6-3.2 7.8-7.5 9-4.3-1.2-7.5-4.4-7.5-9V6z"/><path d="M9 12l2 2 4-4"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9M12 3c-2.5 2.6-3.8 5.6-3.8 9s1.3 6.4 3.8 9"/>',
  message: '<path d="M4 5h16v11H10l-6 4z"/><path d="M8 9h8M8 12h5"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M3 20v-1a6 6 0 0 1 12 0v1"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8M21 20v-1a6 6 0 0 0-4-5.7"/>',
  table: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M10 10v10"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
};

function icon(name, size = 20) {
  const holder = document.createElement("span");
  holder.className = "icon";
  holder.setAttribute("aria-hidden", "true");
  holder.innerHTML = `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" `
    + `stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
  return holder;
}

/* core/theme.js —— 明/暗主题。
 *
 * 默认 dark（羊皮纸暗调）；选择写 localStorage，刷新后保留。
 * 令牌全部在 style.css 的 :root / [data-theme="light"] 里，这里只翻转属性。
 */

const KEY = "mmagent.theme";
const DARK = "dark";
const LIGHT = "light";

function read() {
  try {
    const v = localStorage.getItem(KEY);
    return v === LIGHT || v === DARK ? v : DARK;
  } catch (_e) {
    return DARK;
  }
}

function save(t) {
  try { localStorage.setItem(KEY, t); } catch (_e) { /* 隐私模式：忽略 */ }
}

function apply(t) {
  document.documentElement.setAttribute("data-theme", t);
}

/** 在 #theme-slot 里挂一个主题切换按钮，并立即应用已记忆的主题。 */
export function initTheme() {
  let t = read();
  apply(t);
  const slot = document.getElementById("theme-slot");
  if (!slot) return;

  const paint = () => {
    slot.innerHTML = `<button type="button" class="btn ghost" id="b-theme"
      aria-label="切换明暗主题" title="切换明暗主题">${t === DARK ? "◐ 暗色" : "◑ 亮色"}</button>`;
    const btn = document.getElementById("b-theme");
    if (!btn) return;
    btn.addEventListener("click", () => {
      t = t === DARK ? LIGHT : DARK;
      apply(t);
      save(t);
      paint();
    });
  };
  paint();
}

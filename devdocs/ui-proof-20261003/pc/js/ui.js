// 通用交互：图标、轻提示、右侧抽屉（焦点圈定、Esc 与遮罩关闭）。
import { esc } from './data.js';

export const icon = (name, size = 20) => window.Icon(name, size);

export function mountStaticIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach(el => {
    el.innerHTML = window.Icon(el.dataset.icon, Number(el.dataset.size || 20));
  });
}

export function toast(message, tone = 'success') {
  const region = document.getElementById('toasts');
  const item = document.createElement('div');
  item.className = `toast toast-${tone}`;
  item.innerHTML = `<span class="toast-icon">${icon(tone === 'error' ? 'circle-alert' : 'check-circle-2', 17)}</span><span>${esc(message)}</span>`;
  region.appendChild(item);
  requestAnimationFrame(() => item.classList.add('is-in'));
  setTimeout(() => {
    item.classList.remove('is-in');
    setTimeout(() => item.remove(), 200);
  }, 2600);
}

const LAYER = () => document.getElementById('layer');
let active = null;

export function drawerKey() {
  return active ? active.key : '';
}

export function drawerOpen() {
  return Boolean(active);
}

export function closeDrawer({ restoreFocus = true } = {}) {
  if (!active) return;
  const { layer, panel, prevFocus, onClose } = active;
  active = null;
  document.removeEventListener('keydown', onKeydown, true);
  layer.classList.remove('is-open');
  const remove = () => layer.remove();
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) remove();
  else setTimeout(remove, 200);
  if (restoreFocus && prevFocus && prevFocus.isConnected) prevFocus.focus();
  if (onClose) onClose();
}

// 路由或记录变化后不再适用的抽屉需要关闭。
export function closeDrawerUnless(key) {
  if (active && active.key !== key) closeDrawer({ restoreFocus: false });
}

function focusable(panel) {
  return [...panel.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')]
    .filter(el => !el.disabled && el.offsetParent !== null);
}

function onKeydown(event) {
  if (!active) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeDrawer();
    return;
  }
  if (event.key !== 'Tab') return;
  const items = focusable(active.panel);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

/**
 * 打开右侧抽屉。
 * @param {object} options
 * @param {string} options.key      抽屉上下文（路由键），路由变化后自动关闭
 * @param {string} options.title    已转义的标题
 * @param {string} options.subtitle 已转义的副标题
 * @param {string} options.body     已转义的正文 HTML
 * @param {string} options.footer   已转义的底部操作 HTML
 * @param {(layer: HTMLElement, panel: HTMLElement) => void} [options.onMount]
 * @param {() => void} [options.onClose]
 */
export function openDrawer({ key, title, subtitle = '', body, footer, onMount, onClose }) {
  closeDrawer({ restoreFocus: false });
  const prevFocus = document.activeElement;
  const layer = document.createElement('div');
  layer.className = 'drawer-layer';
  layer.innerHTML = `
    <div class="drawer-mask" data-close></div>
    <aside class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title" tabindex="-1">
      <header class="drawer-head">
        <div class="drawer-heading">
          <h2 class="drawer-title" id="drawer-title">${title}</h2>
          ${subtitle ? `<p class="drawer-sub">${subtitle}</p>` : ''}
        </div>
        <button type="button" class="icon-btn" data-close aria-label="关闭">${icon('x', 18)}</button>
      </header>
      <div class="drawer-body">${body}</div>
      <footer class="drawer-foot">${footer}</footer>
    </aside>`;
  LAYER().appendChild(layer);
  const panel = layer.querySelector('.drawer');
  layer.addEventListener('click', event => {
    if (event.target.closest('[data-close]')) closeDrawer();
  });
  active = { key, layer, panel, prevFocus, onClose };
  document.addEventListener('keydown', onKeydown, true);
  requestAnimationFrame(() => layer.classList.add('is-open'));
  if (onMount) onMount(layer, panel);
  const target = panel.querySelector('[data-autofocus]')
    || panel.querySelector('.drawer-body input, .drawer-body textarea, .drawer-body select')
    || panel;
  target.focus({ preventScroll: true });
}

export function setButtonLoading(button, loading, loadingText) {
  if (loading) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.classList.add('is-loading');
    button.innerHTML = `<span class="spinner" aria-hidden="true"></span>${esc(loadingText || '处理中')}`;
  } else {
    button.disabled = false;
    button.classList.remove('is-loading');
    if (button.dataset.label) {
      button.innerHTML = button.dataset.label;
      delete button.dataset.label;
    }
  }
}

export function showFieldError(panel, fieldName, message) {
  const node = panel.querySelector(`[data-error-for="${fieldName}"]`);
  if (!node) return;
  node.textContent = message;
  node.hidden = false;
}

export function clearFieldErrors(panel) {
  panel.querySelectorAll('[data-error-for]').forEach(node => {
    node.hidden = true;
    node.textContent = '';
  });
}

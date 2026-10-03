// 列表、状态与分页等复用片段。
import { esc, statusLabel, shortTime, park } from './data.js';
import { icon } from './ui.js';

export function statusDot(status) {
  return `<span class="status status-${esc(status)}"><i class="dot" aria-hidden="true"></i>${esc(statusLabel(status))}</span>`;
}

export function statusChip(status) {
  return `<span class="chip chip-${esc(status)}"><i class="dot" aria-hidden="true"></i>${esc(statusLabel(status))}</span>`;
}

export function priorityText(priority) {
  return priority === '紧急'
    ? '<span class="priority is-urgent">紧急</span>'
    : '<span class="priority">普通</span>';
}

export function pageHead(title, { hint = '', sub = '', actions = '' } = {}) {
  return `
    <header class="page-head">
      <div class="page-head-main">
        <h1 class="page-title">${esc(title)}${hint ? `<span class="hint-tag">${esc(hint)}</span>` : ''}</h1>
        ${sub ? `<p class="page-sub">${esc(sub)}</p>` : ''}
      </div>
      ${actions ? `<div class="page-head-actions">${actions}</div>` : ''}
    </header>`;
}

export function emptyState({ title, hint = '', action = '' }) {
  return `
    <div class="empty" role="status">
      <span class="empty-icon" aria-hidden="true">${icon('clipboard-list', 26)}</span>
      <p class="empty-title">${esc(title)}</p>
      ${hint ? `<p class="empty-hint">${esc(hint)}</p>` : ''}
      ${action}
    </div>`;
}

export function pagination({ total, page, pageSize, hashFor }) {
  if (total === 0) return '';
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, pageCount);
  const pages = Array.from({ length: pageCount }, (_, i) => i + 1);
  const navButton = (target, label, iconName, disabled) => (disabled
    ? `<span class="page-btn is-disabled" aria-hidden="true">${icon(iconName, 16)}</span>`
    : `<a class="page-btn" href="${hashFor(target)}" aria-label="${esc(label)}">${icon(iconName, 16)}</a>`);
  return `
    <div class="pagination">
      <span class="page-summary">共 ${total} 条 · 每页 ${pageSize} 条</span>
      <nav class="page-nav" aria-label="分页">
        ${navButton(current - 1, '上一页', 'chevron-left', current <= 1)}
        ${pages.map(number => (number === current
          ? `<span class="page-btn is-current" aria-current="page">${number}</span>`
          : `<a class="page-btn" href="${hashFor(number)}" aria-label="第 ${number} 页">${number}</a>`)).join('')}
        ${navButton(current + 1, '下一页', 'chevron-right', current >= pageCount)}
      </nav>
    </div>`;
}

export function infoRow(label, value, { strong = false } = {}) {
  return `<div class="info-row"><dt>${esc(label)}</dt><dd class="${strong ? 'is-strong' : ''}">${value}</dd></div>`;
}

export function timeline(entries) {
  const items = [...entries].reverse();
  return `
    <ol class="timeline">
      ${items.map((item, index) => `
        <li class="tl-item${index === 0 ? ' is-current' : ''}">
          <span class="tl-dot" aria-hidden="true"></span>
          <div class="tl-body">
            <div class="tl-head">
              <span class="tl-title">${esc(item.title)}</span>
              <time class="tl-time">${esc(item.time)}</time>
            </div>
            ${item.note ? `<p class="tl-note">${esc(item.note)}</p>` : ''}
          </div>
        </li>`).join('')}
    </ol>`;
}

export function recordSummary(row) {
  return `
    <div class="drawer-summary">
      <div class="info-row"><span class="info-label">报修编号</span><span class="info-value num">${esc(row.id)}</span></div>
      <div class="info-row"><span class="info-label">设备</span><span class="info-value">${esc(row.device)}<span class="muted"> · ${esc(row.deviceId)}</span></span></div>
      <div class="info-row"><span class="info-label">位置</span><span class="info-value">${esc(row.location)}</span></div>
      <div class="info-row"><span class="info-label">设备类别</span><span class="info-value">${esc(row.category)}</span></div>
      <div class="info-row"><span class="info-label">最近状态</span><span class="info-value">${esc(statusLabel(row.status))} · ${esc(shortTime(row.created))}</span></div>
    </div>`;
}

export function technicianName(techId) {
  const tech = park.technicians.find(t => t.id === techId);
  return tech ? `${tech.name}（${tech.fullName}）` : '—';
}

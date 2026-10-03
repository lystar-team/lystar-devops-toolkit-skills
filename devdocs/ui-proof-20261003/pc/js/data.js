// 数据与派生计算：状态、时间、筛选条件、列表地址。
export const park = window.Park;

// 状态定义以 store.js 为准，页面不再维护第二份。
export const STATUS_ORDER = Object.keys(park.statuses);
export const statusLabel = id => park.statuses[id] || id;

export const PRIORITIES = ['紧急', '普通'];

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

export function records() {
  return park.list();
}

export function sortByCreatedDesc(rows) {
  return rows.slice().sort((a, b) => b.created.localeCompare(a.created) || b.id.localeCompare(a.id));
}

export function statusCounts(rows) {
  const counts = Object.fromEntries(STATUS_ORDER.map(s => [s, 0]));
  rows.forEach(row => {
    if (counts[row.status] !== undefined) counts[row.status] += 1;
  });
  return counts;
}

export function categories() {
  return [...new Set(park.devices.map(d => d.category))];
}

export function techById(id) {
  return park.technicians.find(t => t.id === id) || null;
}

export function deviceById(id) {
  return park.devices.find(d => d.id === id) || null;
}

// 在办：已派单与维修中的记录，用于判断维修人员当前负荷。
export function inFlight(rows, techId) {
  return rows.filter(r => r.technician === techId && (r.status === 'assigned' || r.status === 'repairing'));
}

export function deviceRecords(rows, deviceId) {
  return sortByCreatedDesc(rows.filter(r => r.deviceId === deviceId));
}

export function normalizeText(value) {
  return String(value ?? '').trim().toLowerCase();
}

export function matchKeyword(row, keyword) {
  const q = normalizeText(keyword);
  if (!q) return true;
  return [row.id, row.device, row.location, row.title, row.description, row.reporter, row.category]
    .some(field => normalizeText(field).includes(q));
}

export function filterOrders(rows, filters) {
  return rows.filter(row => (
    matchKeyword(row, filters.q)
    && (!filters.category || row.category === filters.category)
    && (!filters.priority || row.priority === filters.priority)
    && (!filters.status || row.status === filters.status)
  ));
}

export function ordersHash(filters = {}) {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  if (filters.category) params.set('category', filters.category);
  if (filters.priority) params.set('priority', filters.priority);
  if (filters.status) params.set('status', filters.status);
  if (filters.page && Number(filters.page) > 1) params.set('page', String(filters.page));
  const query = params.toString();
  return `#orders${query ? `?${query}` : ''}`;
}

export function devicesHash(filters = {}) {
  const params = new URLSearchParams();
  if (filters.q) params.set('q', filters.q);
  if (filters.category) params.set('category', filters.category);
  const query = params.toString();
  return `#devices${query ? `?${query}` : ''}`;
}

export function parseHash(raw) {
  const text = String(raw || '').replace(/^#\/?/, '');
  const [path, query = ''] = text.split('?');
  const parts = path.split('/').filter(Boolean);
  return {
    name: parts[0] || 'orders',
    id: parts[1] || '',
    query: Object.fromEntries(new URLSearchParams(query)),
  };
}

export function shortTime(ts) {
  return ts ? ts.slice(5) : '—';
}

export function dateOf(ts) {
  return ts ? ts.slice(0, 10) : '—';
}

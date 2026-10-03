// 报修管理：队列筛选、状态页签、列表与行内动作。
import {
  records, sortByCreatedDesc, statusCounts, filterOrders, ordersHash,
  categories, PRIORITIES, esc, shortTime, statusLabel, STATUS_ORDER,
} from '../data.js';
import { icon } from '../ui.js';
import { pageHead, statusDot, priorityText, emptyState, pagination } from '../components.js';

const PAGE_SIZE = 10;

function rowHtml(row) {
  const rowAction = row.status === 'pending'
    ? `<a class="link-btn is-primary" href="#detail/${esc(row.id)}?action=dispatch">派单</a>`
    : (row.status === 'assigned' || row.status === 'repairing')
      ? `<a class="link-btn is-primary" href="#detail/${esc(row.id)}?action=complete">登记结果</a>`
      : '';
  return `
    <tr class="data-row" data-id="${esc(row.id)}">
      <td><a class="id-link num" href="#detail/${esc(row.id)}">${esc(row.id)}</a></td>
      <td>
        <span class="cell-strong" title="${esc(row.device)}">${esc(row.device)}</span>
        <span class="cell-sub" title="${esc(row.location)}">${esc(row.location)}</span>
      </td>
      <td><p class="cell-desc" title="${esc(row.description)}">${esc(row.description)}</p></td>
      <td class="cell-muted">${esc(row.category)}</td>
      <td>${priorityText(row.priority)}</td>
      <td>${statusDot(row.status)}</td>
      <td>
        <span class="cell-strong">${esc(row.reporter)}</span>
        <span class="cell-sub num">${esc(shortTime(row.created))}</span>
      </td>
      <td class="col-actions">
        <a class="link-btn" href="#detail/${esc(row.id)}">查看</a>
        ${rowAction}
      </td>
    </tr>`;
}

function activeFilterNames(filters) {
  const names = [];
  if (filters.q) names.push(`关键词“${filters.q}”`);
  if (filters.category) names.push(`类别“${filters.category}”`);
  if (filters.priority) names.push(`优先级“${filters.priority}”`);
  return names;
}

export function mount(root, ctx) {
  const all = records();
  const counts = statusCounts(all);
  const filters = {
    q: ctx.route.query.q || '',
    category: ctx.route.query.category || '',
    priority: ctx.route.query.priority || '',
    status: ctx.route.query.status || '',
    page: Math.max(1, Number(ctx.route.query.page) || 1),
  };
  const hashFor = patch => ordersHash({ ...filters, ...patch });
  const matched = filterOrders(sortByCreatedDesc(all), filters);
  const pageCount = Math.max(1, Math.ceil(matched.length / PAGE_SIZE));
  const page = Math.min(filters.page, pageCount);
  const rows = matched.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const tab = (label, count, status) => `
    <a class="tab${filters.status === status ? ' is-active' : ''}" href="${hashFor({ status, page: 1 })}"
       ${filters.status === status ? 'aria-current="page"' : ''}>${esc(label)}<span class="tab-count">${count}</span></a>`;

  const filterNames = activeFilterNames(filters);
  const emptyText = filterNames.length || filters.status
    ? '没有符合条件的报修记录'
    : '暂无报修记录';
  const emptyHint = filterNames.length || filters.status
    ? `当前条件：${[...(filters.status ? [`状态“${statusLabel(filters.status)}”`] : []), ...filterNames].join('、')}。调整条件后重新查询。`
    : '新的报修提交后会出现在这里。';

  root.innerHTML = `
    ${pageHead('报修管理', { sub: `共 ${all.length} 条报修记录 · ${counts.pending} 条待派单` })}
    <form class="filter-card" id="order-filter" role="search" aria-label="报修筛选">
      <div class="filter-grid">
        <label class="filter-field">
          <span class="filter-label">关键词</span>
          <span class="input-affix">
            ${icon('search', 16)}
            <input type="search" name="q" value="${esc(filters.q)}" placeholder="编号、设备、故障描述或报修人" autocomplete="off">
          </span>
        </label>
        <label class="filter-field">
          <span class="filter-label">设备类别</span>
          <span class="select-affix">
            <select name="category">
              <option value="">全部类别</option>
              ${categories().map(item => `<option value="${esc(item)}"${filters.category === item ? ' selected' : ''}>${esc(item)}</option>`).join('')}
            </select>
            ${icon('chevron-down', 16)}
          </span>
        </label>
        <label class="filter-field">
          <span class="filter-label">优先级</span>
          <span class="select-affix">
            <select name="priority">
              <option value="">全部优先级</option>
              ${PRIORITIES.map(item => `<option value="${esc(item)}"${filters.priority === item ? ' selected' : ''}>${esc(item)}</option>`).join('')}
            </select>
            ${icon('chevron-down', 16)}
          </span>
        </label>
        <div class="filter-actions">
          <button type="submit" class="btn btn-primary">${icon('search', 16)}查询</button>
          <a class="btn" href="${hashFor({ q: '', category: '', priority: '', page: 1 })}">重置</a>
        </div>
      </div>
    </form>
    <section class="table-card" aria-label="报修记录">
      <nav class="tabs" aria-label="按状态筛选">
        ${tab('全部', all.length, '')}
        ${STATUS_ORDER.map(status => tab(statusLabel(status), counts[status], status)).join('')}
      </nav>
      ${rows.length ? `
        <div class="table-wrap">
          <table class="data-table">
            <caption class="sr-only">报修记录列表，共 ${matched.length} 条</caption>
            <colgroup>
              <col class="w-id"><col class="w-device"><col><col class="w-category">
              <col class="w-priority"><col class="w-status"><col class="w-reporter"><col class="w-actions">
            </colgroup>
            <thead>
              <tr>
                <th scope="col">报修编号</th>
                <th scope="col">设备 / 位置</th>
                <th scope="col">故障描述</th>
                <th scope="col">设备类别</th>
                <th scope="col">优先级</th>
                <th scope="col">状态</th>
                <th scope="col">报修人 / 提交时间</th>
                <th scope="col" class="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>${rows.map(rowHtml).join('')}</tbody>
          </table>
        </div>` : emptyState({
          title: emptyText,
          hint: emptyHint,
          action: '<a class="btn" href="#orders">清除筛选条件</a>',
        })}
      ${pagination({ total: matched.length, page, pageSize: PAGE_SIZE, hashFor: target => hashFor({ page: target }) })}
    </section>`;

  const form = root.querySelector('#order-filter');
  form.addEventListener('submit', event => {
    event.preventDefault();
    const data = new FormData(form);
    ctx.navigate(ordersHash({
      q: String(data.get('q') || '').trim(),
      category: String(data.get('category') || ''),
      priority: String(data.get('priority') || ''),
      status: filters.status,
      page: 1,
    }));
  });

  // 整行点击进入详情；行内链接与按钮保留自身行为。
  root.querySelector('.data-table')?.addEventListener('click', event => {
    if (event.target.closest('a, button')) return;
    const row = event.target.closest('tr[data-id]');
    if (row) ctx.navigate(`#detail/${row.dataset.id}`);
  });
}

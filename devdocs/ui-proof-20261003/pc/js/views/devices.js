// 设备档案：只读资料，用于核对设备信息与历史报修。
import {
  park, records, esc, categories, deviceRecords, devicesHash, normalizeText, dateOf,
} from '../data.js';
import { icon, openDrawer } from '../ui.js';
import { pageHead, statusDot, emptyState, infoRow } from '../components.js';

function matches(device, q) {
  const keyword = normalizeText(q);
  if (!keyword) return true;
  return [device.code, device.name, device.location, device.model, device.category]
    .some(field => normalizeText(field).includes(keyword));
}

function openDeviceDrawer(device) {
  const history = deviceRecords(records(), device.id);
  openDrawer({
    key: 'devices',
    title: device.name,
    subtitle: `设备编号 ${device.code}`,
    body: `
      <div class="drawer-summary">
        ${infoRow('设备名称', esc(device.name))}
        ${infoRow('设备编号', `<span class="num">${esc(device.code)}</span>`)}
        ${infoRow('设备类别', esc(device.category))}
        ${infoRow('位置', esc(device.location))}
        ${infoRow('型号', esc(device.model))}
        ${infoRow('最近保养', `<span class="num">${esc(device.lastService)}</span>`)}
      </div>
      <div class="drawer-section">
        <h3 class="drawer-section-title">报修记录</h3>
        ${history.length ? `
          <ul class="mini-list">
            ${history.map(row => `
              <li>
                <a class="mini-item" href="#detail/${esc(row.id)}">
                  <span class="mini-main">
                    <span class="mini-title">${esc(row.title)}</span>
                    <span class="mini-sub num">${esc(row.id)} · ${esc(dateOf(row.created))}</span>
                  </span>
                  ${statusDot(row.status)}
                </a>
              </li>`).join('')}
          </ul>`
        : '<p class="side-note">该设备暂无报修记录。</p>'}
      </div>`,
    footer: '<button type="button" class="btn" data-close>关闭</button>',
  });
}

export function mount(root, ctx) {
  const q = ctx.route.query.q || '';
  const category = ctx.route.query.category || '';
  const list = park.devices.filter(device => matches(device, q) && (!category || device.category === category));
  const rows = records();
  const filterNames = [q ? `关键词“${q}”` : '', category ? `类别“${category}”` : ''].filter(Boolean);

  root.innerHTML = `
    ${pageHead('设备档案', {
      hint: '只读',
      sub: `共 ${park.devices.length} 台设备，用于核对型号、位置与保养时间。`,
    })}
    <form class="filter-card" id="device-filter" role="search" aria-label="设备筛选">
      <div class="filter-grid">
        <label class="filter-field">
          <span class="filter-label">关键词</span>
          <span class="input-affix">
            ${icon('search', 16)}
            <input type="search" name="q" value="${esc(q)}" placeholder="设备编号、名称、位置或型号" autocomplete="off">
          </span>
        </label>
        <label class="filter-field">
          <span class="filter-label">设备类别</span>
          <span class="select-affix">
            <select name="category">
              <option value="">全部类别</option>
              ${categories().map(item => `<option value="${esc(item)}"${category === item ? ' selected' : ''}>${esc(item)}</option>`).join('')}
            </select>
            ${icon('chevron-down', 16)}
          </span>
        </label>
        <div class="filter-actions">
          <button type="submit" class="btn btn-primary">${icon('search', 16)}查询</button>
          <a class="btn" href="#devices">重置</a>
        </div>
      </div>
    </form>
    <section class="table-card" aria-label="设备列表">
      ${list.length ? `
        <div class="table-wrap">
          <table class="data-table">
            <caption class="sr-only">设备档案列表，共 ${list.length} 台</caption>
            <colgroup>
              <col class="w-code"><col class="w-device"><col class="w-category"><col class="w-location">
              <col class="w-model"><col class="w-date"><col class="w-count"><col class="w-actions">
            </colgroup>
            <thead>
              <tr>
                <th scope="col">设备编号</th>
                <th scope="col">设备名称</th>
                <th scope="col">设备类别</th>
                <th scope="col">位置</th>
                <th scope="col">型号</th>
                <th scope="col">最近保养</th>
                <th scope="col">报修记录</th>
                <th scope="col" class="col-actions">操作</th>
              </tr>
            </thead>
            <tbody>
              ${list.map(device => {
                const count = rows.filter(row => row.deviceId === device.id).length;
                return `
                  <tr class="data-row" data-device="${esc(device.id)}">
                    <td><span class="cell-strong num">${esc(device.code)}</span></td>
                    <td><span class="cell-strong" title="${esc(device.name)}">${esc(device.name)}</span></td>
                    <td class="cell-muted">${esc(device.category)}</td>
                    <td><span class="cell-sub" title="${esc(device.location)}">${esc(device.location)}</span></td>
                    <td><span class="cell-sub" title="${esc(device.model)}">${esc(device.model)}</span></td>
                    <td><span class="num">${esc(device.lastService)}</span></td>
                    <td><span class="num">${count} 条</span></td>
                    <td class="col-actions">
                      <button type="button" class="link-btn" data-open="${esc(device.id)}">查看</button>
                    </td>
                  </tr>`;
              }).join('')}
            </tbody>
          </table>
        </div>
        <div class="list-foot">
          <span>${filterNames.length ? `筛选后 ${list.length} 台设备` : `共 ${list.length} 台设备`}</span>
        </div>`
      : emptyState({
        title: '没有符合条件的设备',
        hint: `当前条件：${filterNames.join('、')}。调整条件后重新查询。`,
        action: '<a class="btn" href="#devices">清除筛选条件</a>',
      })}
    </section>`;

  root.querySelector('#device-filter').addEventListener('submit', event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    ctx.navigate(devicesHash({
      q: String(data.get('q') || '').trim(),
      category: String(data.get('category') || ''),
    }));
  });

  const open = id => {
    const device = park.devices.find(item => item.id === id);
    if (device) openDeviceDrawer(device);
  };
  root.querySelector('.data-table')?.addEventListener('click', event => {
    const button = event.target.closest('[data-open]');
    const row = event.target.closest('tr[data-device]');
    if (button) { open(button.dataset.open); return; }
    if (row) open(row.dataset.device);
  });
}

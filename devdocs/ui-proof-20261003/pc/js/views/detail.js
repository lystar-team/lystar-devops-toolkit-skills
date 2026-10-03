// 报修详情：材料、处理记录、维修安排与派单入口。
import {
  park, records, deviceById, techById, esc, shortTime, deviceRecords, devicesHash,
} from '../data.js';
import { icon } from '../ui.js';
import { statusChip, priorityText, timeline, infoRow } from '../components.js';
import { openDispatchDrawer, openCompleteDrawer } from '../drawers.js';

function notFound(ctx) {
  return `
    <nav class="crumbs" aria-label="返回">
      <a class="crumb-link" href="${ctx.ordersListHash}">${icon('arrow-left', 16)}报修管理</a>
    </nav>
    <section class="card">
      <div class="empty" role="status">
        <span class="empty-icon" aria-hidden="true">${icon('circle-alert', 26)}</span>
        <p class="empty-title">没有找到这条报修记录</p>
        <p class="empty-hint">记录可能已被删除，或编号有误。</p>
        <a class="btn" href="#orders">返回报修管理</a>
      </div>
    </section>`;
}

function scheduleCard(row, tech) {
  const rows = [
    tech ? infoRow('维修人员', `${esc(tech.name)}<span class="muted">（${esc(tech.fullName)} · ${esc(tech.specialty)}）</span>`) : '',
    tech ? infoRow('联系电话', `<span class="num">${esc(tech.phone)}</span>`) : '',
    tech && row.appointment ? infoRow('预约时间', `<span class="num">${esc(row.appointment)}</span>`) : '',
  ].join('');
  if (row.status === 'pending') {
    return `
      <section class="card side-card">
        <h2 class="card-title">维修安排</h2>
        <p class="side-lead">尚未派单</p>
        <p class="side-note">选择维修人员并设置预约时间后，记录进入已派单。</p>
        <button type="button" class="btn btn-primary btn-block" data-primary-action data-action="dispatch">
          ${icon('wrench', 16)}派单
        </button>
      </section>`;
  }
  if (row.status === 'assigned' || row.status === 'repairing') {
    return `
      <section class="card side-card">
        <h2 class="card-title">维修安排</h2>
        <dl class="info-list">${rows}</dl>
        <p class="side-note">${row.status === 'assigned' ? '已派单，等待维修人员到场。' : '维修人员已到场，正在处理。'}</p>
        <button type="button" class="btn btn-primary btn-block" data-primary-action data-action="complete">
          ${icon('check', 16)}登记维修结果
        </button>
      </section>`;
  }
  const note = row.status === 'confirm'
    ? '维修已完成，等待报修人确认。'
    : '报修人已确认，本单结束。';
  return `
    <section class="card side-card">
      <h2 class="card-title">维修安排</h2>
      <dl class="info-list">${rows}</dl>
      <p class="side-note${row.status === 'closed' ? ' is-done' : ''}">${icon(row.status === 'closed' ? 'check-circle-2' : 'clock-3', 15)}${note}</p>
    </section>`;
}

function reporterCard(row) {
  const user = park.user;
  const isSelf = row.reporter === user.name;
  return `
    <section class="card side-card">
      <h2 class="card-title">报修人</h2>
      <dl class="info-list">
        ${infoRow('姓名', esc(row.reporter))}
        ${infoRow('联系电话', `<span class="num">${esc(row.phone)}</span>`)}
        ${isSelf ? infoRow('公司', esc(user.company)) : ''}
        ${isSelf ? infoRow('常用位置', esc(user.location)) : ''}
      </dl>
    </section>`;
}

function deviceCard(row) {
  const device = deviceById(row.deviceId) || {};
  const historyCount = deviceRecords(records(), row.deviceId).filter(item => item.id !== row.id).length;
  return `
    <section class="card side-card">
      <h2 class="card-title">设备资料</h2>
      <dl class="info-list">
        ${infoRow('设备编号', `<span class="num">${esc(device.code || row.deviceId)}</span>`)}
        ${infoRow('型号', esc(device.model || '—'))}
        ${infoRow('设备类别', esc(device.category || row.category))}
        ${infoRow('最近保养', `<span class="num">${esc(device.lastService || '—')}</span>`)}
      </dl>
      ${historyCount ? `<p class="side-note">该设备另有 ${historyCount} 条历史报修记录。</p>` : ''}
      <a class="side-link" href="${devicesHash({ q: row.deviceId })}">查看设备档案${icon('chevron-right', 15)}</a>
    </section>`;
}

export function mount(root, ctx) {
  const row = park.get(ctx.route.id);
  if (!row) {
    root.innerHTML = notFound(ctx);
    return;
  }
  const tech = techById(row.technician);
  const device = deviceById(row.deviceId) || {};
  const images = row.images || [];
  const action = row.status === 'pending'
    ? `<button type="button" class="btn btn-primary" data-primary-action data-action="dispatch">${icon('wrench', 16)}派单</button>`
    : (['assigned', 'repairing'].includes(row.status)
      ? `<button type="button" class="btn btn-primary" data-primary-action data-action="complete">${icon('check', 16)}登记维修结果</button>`
      : `<span class="action-note">${icon(row.status === 'closed' ? 'check-circle-2' : 'clock-3', 16)}${row.status === 'closed' ? '本单已完成' : '等待报修人确认'}</span>`);

  root.innerHTML = `
    <nav class="crumbs" aria-label="面包屑">
      <a class="crumb-link" href="${ctx.ordersListHash}">${icon('arrow-left', 16)}报修管理</a>
      <span class="crumb-sep" aria-hidden="true">/</span>
      <span class="crumb-current num">${esc(row.id)}</span>
    </nav>
    <header class="detail-head">
      <div class="detail-head-main">
        <h1 class="detail-title">${esc(row.title)}</h1>
        <div class="detail-meta">
          ${statusChip(row.status)}
          ${priorityText(row.priority)}
          <span class="meta-item">提交于 <time class="num">${esc(row.created)}</time></span>
        </div>
      </div>
      <div class="detail-actions">${action}</div>
    </header>
    <div class="detail-grid">
      <div class="detail-main">
        <section class="card">
          <h2 class="card-title">故障信息</h2>
          <dl class="info-grid">
            ${infoRow('设备名称', esc(row.device))}
            ${infoRow('设备编号', `<span class="num">${esc(device.code || row.deviceId)}</span>`)}
            ${infoRow('位置', esc(row.location))}
            ${infoRow('设备类别', esc(row.category))}
          </dl>
          <div class="block">
            <h3 class="block-title">故障描述</h3>
            <p class="desc-text">${esc(row.description)}</p>
          </div>
          ${images.length ? `
            <div class="block">
              <h3 class="block-title">报修照片</h3>
              <ul class="photo-list">
                ${images.map((src, index) => `<li><img src="${esc(src)}" alt="报修照片 ${index + 1}"></li>`).join('')}
              </ul>
            </div>` : ''}
        </section>
        <section class="card">
          <h2 class="card-title">处理记录</h2>
          ${timeline(row.timeline)}
        </section>
      </div>
      <aside class="detail-side">
        ${scheduleCard(row, tech)}
        ${reporterCard(row)}
        ${deviceCard(row)}
      </aside>
    </div>`;

  const openAction = () => {
    if (row.status === 'pending') openDispatchDrawer(row);
    else if (['assigned', 'repairing'].includes(row.status)) openCompleteDrawer(row);
  };
  root.querySelector('[data-action="dispatch"]')?.addEventListener('click', openAction);
  root.querySelector('[data-action="complete"]')?.addEventListener('click', openAction);

  const deepLink = ctx.route.query.action;
  if ((deepLink === 'dispatch' && row.status === 'pending') || (deepLink === 'complete' && ['assigned', 'repairing'].includes(row.status))) {
    history.replaceState(null, '', `#detail/${row.id}`);
    openAction();
  }
}

// 维修人员：只读资料，用于核对专长、班次与当前负荷。
import { park, records, inFlight, esc, shortTime } from '../data.js';
import { icon, openDrawer } from '../ui.js';
import { pageHead, statusDot, infoRow } from '../components.js';

function openTechDrawer(tech, rows) {
  const current = inFlight(rows, tech.id);
  const done = rows.filter(row => row.technician === tech.id && ['confirm', 'closed'].includes(row.status));
  openDrawer({
    key: 'team',
    title: `${tech.name}`,
    subtitle: `${tech.fullName} · 专长 ${tech.specialty}`,
    body: `
      <div class="drawer-summary">
        ${infoRow('联系电话', `<span class="num">${esc(tech.phone)}</span>`)}
        ${infoRow('班次', `<span class="num">${esc(tech.shift)}</span>`)}
        ${infoRow('当前在办', `${current.length} 单`)}
        ${infoRow('已完成', `${done.length} 单`)}
      </div>
      <div class="drawer-section">
        <h3 class="drawer-section-title">在办报修</h3>
        ${current.length ? `
          <ul class="mini-list">
            ${current.map(row => `
              <li>
                <a class="mini-item" href="#detail/${esc(row.id)}">
                  <span class="mini-main">
                    <span class="mini-title">${esc(row.device)}</span>
                    <span class="mini-sub num">${esc(row.id)} · 预约 ${esc(shortTime(row.appointment))}</span>
                  </span>
                  ${statusDot(row.status)}
                </a>
              </li>`).join('')}
          </ul>`
        : '<p class="side-note">当前没有在办报修。</p>'}
      </div>`,
    footer: '<button type="button" class="btn" data-close>关闭</button>',
  });
}

export function mount(root, ctx) {
  const rows = records();
  const totalInFlight = park.technicians.reduce((sum, tech) => sum + inFlight(rows, tech.id).length, 0);
  root.innerHTML = `
    ${pageHead('维修人员', {
      hint: '只读',
      sub: `共 ${park.technicians.length} 人，当前在办 ${totalInFlight} 单。`,
    })}
    <div class="person-grid">
      ${park.technicians.map(tech => {
        const current = inFlight(rows, tech.id);
        return `
          <button type="button" class="person-card" data-tech="${esc(tech.id)}">
            <span class="person-head">
              <span class="person-name">${esc(tech.name)}</span>
              <span class="person-full">${esc(tech.fullName)}</span>
            </span>
            <span class="person-specialty">${esc(tech.specialty)}</span>
            <span class="person-meta">
              <span class="person-meta-item">${icon('phone', 15)}<span class="num">${esc(tech.phone)}</span></span>
              <span class="person-meta-item">${icon('clock-3', 15)}<span class="num">${esc(tech.shift)}</span></span>
            </span>
            <span class="person-foot">
              <span class="person-load${current.length ? ' is-busy' : ''}">在办 ${current.length} 单</span>
              <span class="person-more">在办明细${icon('chevron-right', 15)}</span>
            </span>
          </button>`;
      }).join('')}
    </div>`;

  root.querySelector('.person-grid').addEventListener('click', event => {
    const card = event.target.closest('[data-tech]');
    if (!card) return;
    const tech = park.technicians.find(item => item.id === card.dataset.tech);
    if (tech) openTechDrawer(tech, rows);
  });
}

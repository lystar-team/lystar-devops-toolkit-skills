// 派单与维修结果登记抽屉：草稿只存在表单里，取消不写记录。
import { esc, park, records, inFlight, techById } from './data.js';
import { icon, openDrawer, closeDrawer, setButtonLoading, showFieldError, clearFieldErrors, toast } from './ui.js';
import { recordSummary } from './components.js';

function focusAfterClose(record) {
  const next = document.querySelector('[data-primary-action]');
  if (next) next.focus();
  else document.getElementById('content')?.focus();
  return record;
}

export function openDispatchDrawer(record) {
  const rows = records();
  const options = park.technicians.map(tech => {
    const load = inFlight(rows, tech.id).length;
    const match = tech.specialty === record.category;
    return `
      <label class="tech-option">
        <input class="sr-radio" type="radio" name="technician" value="${esc(tech.id)}">
        <span class="tech-body">
          <span class="tech-line">
            <span class="tech-name">${esc(tech.name)}</span>
            <span class="tech-full">${esc(tech.fullName)}</span>
            ${match ? '<span class="tech-match">专长匹配</span>' : ''}
          </span>
          <span class="tech-sub">${esc(tech.specialty)} · ${esc(tech.shift)} · 在办 ${load} 单</span>
        </span>
        <span class="tech-check" aria-hidden="true">${icon('check', 16)}</span>
      </label>`;
  }).join('');

  openDrawer({
    key: `detail:${record.id}`,
    title: '派单',
    subtitle: esc(record.title),
    body: `
      ${recordSummary(record)}
      <div class="field-block" role="radiogroup" aria-labelledby="tech-label">
        <span class="field-label" id="tech-label">选择维修人员 <span class="req" aria-hidden="true">*</span><span class="sr-only">（必填）</span></span>
        <div class="tech-options">${options}</div>
        <p class="field-error" data-error-for="tech" hidden></p>
      </div>
      <label class="field-block" for="dispatch-time">
        <span class="field-label">预约时间 <span class="req" aria-hidden="true">*</span><span class="sr-only">（必填）</span></span>
        <input type="datetime-local" id="dispatch-time" step="900" value="${esc((record.appointment || '').replace(' ', 'T'))}">
        <p class="field-error" data-error-for="time" hidden></p>
      </label>
      <label class="field-block" for="dispatch-note">
        <span class="field-label">备注</span>
        <textarea id="dispatch-note" rows="3" placeholder="交接说明或注意事项（选填）"></textarea>
        <p class="field-hint">备注会写入处理记录。</p>
      </label>
      <p class="drawer-alert" data-error role="alert" hidden></p>`,
    footer: `
      <button type="button" class="btn" data-close>取消</button>
      <button type="button" class="btn btn-primary" data-submit>确认派单</button>`,
    onMount(layer, panel) {
      const submit = panel.querySelector('[data-submit]');
      const alert = panel.querySelector('[data-error]');
      submit.addEventListener('click', async () => {
        clearFieldErrors(panel);
        alert.hidden = true;
        const picked = panel.querySelector('input[name="technician"]:checked');
        const time = panel.querySelector('#dispatch-time').value;
        const note = panel.querySelector('#dispatch-note').value.trim();
        if (!picked) {
          showFieldError(panel, 'tech', '请选择维修人员。');
          panel.querySelector('input[name="technician"]')?.focus();
          return;
        }
        if (!time) {
          showFieldError(panel, 'time', '请选择预约时间。');
          panel.querySelector('#dispatch-time').focus();
          return;
        }
        setButtonLoading(submit, true, '提交中');
        try {
          const updated = await park.dispatch(record.id, picked.value, time.replace('T', ' '), note);
          const tech = techById(picked.value);
          closeDrawer();
          toast(`已派单给${tech.name}，预约 ${updated.appointment}`);
          focusAfterClose(updated);
        } catch (error) {
          setButtonLoading(submit, false);
          alert.innerHTML = `${icon('circle-alert', 16)}<span>${esc(error.message)}</span>`;
          alert.hidden = false;
        }
      });
    },
  });
}

export function openCompleteDrawer(record) {
  const tech = techById(record.technician);
  openDrawer({
    key: `detail:${record.id}`,
    title: '登记维修结果',
    subtitle: esc(record.title),
    body: `
      ${recordSummary(record)}
      <div class="drawer-summary">
        <div class="info-row"><span class="info-label">维修人员</span><span class="info-value">${tech ? esc(`${tech.name}（${tech.fullName}）`) : '—'}</span></div>
        <div class="info-row"><span class="info-label">预约时间</span><span class="info-value num">${esc(record.appointment || '—')}</span></div>
      </div>
      <label class="field-block" for="complete-summary">
        <span class="field-label">维修结果 <span class="req" aria-hidden="true">*</span><span class="sr-only">（必填）</span></span>
        <textarea id="complete-summary" rows="4" placeholder="说明处理方式与结果，例如更换的部件、测试情况。"></textarea>
        <p class="field-error" data-error-for="summary" hidden></p>
        <p class="field-hint">提交后报修进入待确认，报修人确认后结束。</p>
      </label>
      <p class="drawer-alert" data-error role="alert" hidden></p>`,
    footer: `
      <button type="button" class="btn" data-close>取消</button>
      <button type="button" class="btn btn-primary" data-submit>保存结果</button>`,
    onMount(layer, panel) {
      const submit = panel.querySelector('[data-submit]');
      const alert = panel.querySelector('[data-error]');
      const summary = panel.querySelector('#complete-summary');
      submit.addEventListener('click', async () => {
        clearFieldErrors(panel);
        alert.hidden = true;
        if (!summary.value.trim()) {
          showFieldError(panel, 'summary', '请填写维修结果。');
          summary.focus();
          return;
        }
        setButtonLoading(submit, true, '提交中');
        try {
          const updated = await park.complete(record.id, summary.value);
          closeDrawer();
          toast('维修结果已登记，等待报修人确认');
          focusAfterClose(updated);
        } catch (error) {
          setButtonLoading(submit, false);
          alert.innerHTML = `${icon('circle-alert', 16)}<span>${esc(error.message)}</span>`;
          alert.hidden = false;
        }
      });
    },
  });
}

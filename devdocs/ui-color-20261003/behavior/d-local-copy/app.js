(function () {
  'use strict';

  /* 本地内存样例，仅用于页面行为验证，不是真实接口数据 */
  var RECORDS = [
    { id: 'r1', title: '空调无法制冷', status: 'pending' },
    { id: 'r2', title: '门禁刷卡无响应', status: 'processing' },
    { id: 'r3', title: '办公室照明不亮', status: 'done' }
  ];

  var STATUS = {
    pending: { label: '待受理', className: 'status--pending' },
    processing: { label: '处理中', className: 'status--processing' },
    done: { label: '已完成', className: 'status--done' }
  };

  var el = {
    themeToggle: document.getElementById('themeToggle'),
    queryInput: document.getElementById('queryInput'),
    queryBtn: document.getElementById('queryBtn'),
    filterNote: document.getElementById('filterNote'),
    filterNoteText: document.getElementById('filterNoteText'),
    clearFilter: document.getElementById('clearFilter'),
    recordList: document.getElementById('recordList')
  };

  function currentQuery() {
    return el.queryInput.value.trim();
  }

  function matches(record, query) {
    if (!query) return true;
    var haystack = (record.title + ' ' + STATUS[record.status].label).toLowerCase();
    return haystack.indexOf(query.toLowerCase()) !== -1;
  }

  function detailRow(label, value) {
    var dt = document.createElement('dt');
    dt.textContent = label;
    var dd = document.createElement('dd');
    dd.textContent = value;
    return [dt, dd];
  }

  function buildRecord(record) {
    var li = document.createElement('li');
    li.className = 'record';

    var head = document.createElement('div');
    head.className = 'record-head';

    var title = document.createElement('h2');
    title.className = 'record-title';
    title.textContent = record.title;
    head.appendChild(title);

    var statusInfo = STATUS[record.status];
    var status = document.createElement('span');
    status.className = 'status ' + statusInfo.className;
    status.textContent = statusInfo.label;
    head.appendChild(status);

    var detailId = 'record-detail-' + record.id;
    var toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'toggle';
    toggle.textContent = '查看记录';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-controls', detailId);
    head.appendChild(toggle);

    var detail = document.createElement('div');
    detail.className = 'record-detail';
    detail.id = detailId;
    detail.hidden = true;

    var dl = document.createElement('dl');
    detailRow('故障描述', record.title).forEach(function (node) {
      dl.appendChild(node);
    });
    detailRow('当前状态', statusInfo.label).forEach(function (node) {
      dl.appendChild(node);
    });
    detail.appendChild(dl);

    toggle.addEventListener('click', function () {
      var expanded = toggle.getAttribute('aria-expanded') === 'true';
      toggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      detail.hidden = expanded;
    });

    li.appendChild(head);
    li.appendChild(detail);
    return li;
  }

  function render() {
    var query = currentQuery();
    var visible = RECORDS.filter(function (record) {
      return matches(record, query);
    });

    el.recordList.textContent = '';

    if (visible.length === 0) {
      var empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = '没有找到匹配的记录';
      el.recordList.appendChild(empty);
    } else {
      visible.forEach(function (record) {
        el.recordList.appendChild(buildRecord(record));
      });
    }

    if (query) {
      el.filterNote.hidden = false;
      el.filterNoteText.textContent = '已筛选“' + query + '”，共 ' + visible.length + ' 条记录';
    } else {
      el.filterNote.hidden = true;
      el.filterNoteText.textContent = '';
    }
  }

  el.queryBtn.addEventListener('click', render);

  el.queryInput.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      render();
    }
  });

  el.clearFilter.addEventListener('click', function () {
    el.queryInput.value = '';
    render();
    el.queryInput.focus();
  });

  el.themeToggle.addEventListener('click', function () {
    var root = document.documentElement;
    var dark = root.getAttribute('data-theme') === 'dark';
    root.setAttribute('data-theme', dark ? 'light' : 'dark');
    el.themeToggle.textContent = dark ? '深色模式' : '浅色模式';
  });

  render();
})();

(function () {
  'use strict';

  /* ---------- 本次调拨的本地模拟数据 ---------- */

  var TRANSFER = {
    id: 'DB20261003018',
    from: '北区设备耗材仓',
    to: '南区实验室备件仓',
    status: '待核对',
    created: '2026-10-03 09:30',
    operator: '许知夏',
    note: '部分物料分两批到货，核对后请说明差异原因。'
  };

  var OUTGOING = [
    { id: 'M101', name: '工业以太网交换机', spec: '8口千兆，导轨安装', unit: '台', quantity: 12 },
    { id: 'M102', name: '楼宇自控系统多回路温湿度采集模块及扩展连接组件', spec: 'RS485，24VDC，8路输入', unit: '套', quantity: 8 },
    { id: 'M103', name: '设备柜门密封条', spec: 'EPDM，宽20mm', unit: '米', quantity: 45 },
    { id: 'M104', name: '不锈钢安装支架', spec: '304，L型', unit: '个', quantity: 20 },
    { id: 'M105', name: '通讯电缆', spec: 'RVSP 2×0.75', unit: '米', quantity: 100 }
  ];

  var INCOMING = [
    { id: 'M101', name: '工业以太网交换机', spec: '8口千兆，导轨安装', unit: '台', quantity: 10 },
    { id: 'M102', name: '楼宇自控系统多回路温湿度采集模块及扩展连接组件', spec: 'RS485，24VDC，8路输入', unit: '套', quantity: 8 },
    { id: 'M103', name: '设备柜门密封条', spec: 'EPDM，宽20mm', unit: '米', quantity: 42 },
    { id: 'M104', name: '不锈钢安装支架', spec: '304，L型', unit: '个', quantity: 20 },
    { id: 'M105', name: '通讯电缆', spec: 'RVSP 2×0.75', unit: '米', quantity: 0 }
  ];

  /* ---------- 本地模拟数据层 ----------
     保存说明通过 store.saveNote 完成；测试适配器可在页面加载后包装该方法。 */

  var savedNote = TRANSFER.note;

  var store = {
    getTransfer: function () {
      return TRANSFER;
    },
    getNote: function () {
      return savedNote;
    },
    saveNote: function (text) {
      return new Promise(function (resolve) {
        window.setTimeout(function () {
          savedNote = text;
          resolve({ note: savedNote });
        }, 350);
      });
    }
  };

  window.TransferCheck = { store: store };

  /* ---------- 派生对照数据：按物料 ID 匹配，差异为调入数减调出数 ---------- */

  var incomingById = {};
  INCOMING.forEach(function (item) {
    incomingById[item.id] = item;
  });

  var comparisons = OUTGOING.map(function (out) {
    var inc = incomingById[out.id] || null;
    return {
      out: out,
      inc: inc,
      diff: inc ? inc.quantity - out.quantity : null
    };
  });

  var diffCount = comparisons.filter(function (row) {
    return row.diff !== null && row.diff !== 0;
  }).length;

  /* ---------- DOM ---------- */

  var el = {
    search: document.getElementById('searchInput'),
    clearSearch: document.getElementById('clearSearch'),
    summary: document.getElementById('summary'),
    rows: document.getElementById('compareRows'),
    noteView: document.getElementById('noteView'),
    noteForm: document.getElementById('noteForm'),
    noteInput: document.getElementById('noteInput'),
    noteCounter: document.getElementById('noteCounter'),
    noteError: document.getElementById('noteError'),
    noteFeedback: document.getElementById('noteFeedback'),
    editNoteBtn: document.getElementById('editNoteBtn'),
    cancelNoteBtn: document.getElementById('cancelNoteBtn'),
    saveNoteBtn: document.getElementById('saveNoteBtn')
  };

  /* ---------- 对照表 ---------- */

  function diffLabel(diff) {
    if (diff === 0) return '相符';
    return diff < 0 ? '调入短少' : '调入多出';
  }

  function diffValue(diff) {
    if (diff < 0) return '−' + Math.abs(diff);
    if (diff > 0) return '+' + diff;
    return '0';
  }

  function makeCell(className) {
    var td = document.createElement('td');
    td.className = className;
    return td;
  }

  function buildRow(row) {
    var tr = document.createElement('tr');

    var material = makeCell('col-material');
    var line = document.createElement('div');
    line.className = 'material-line';
    var id = document.createElement('span');
    id.className = 'material-id';
    id.textContent = row.out.id;
    var name = document.createElement('span');
    name.className = 'material-name';
    name.textContent = row.out.name;
    line.appendChild(id);
    line.appendChild(name);
    var spec = document.createElement('div');
    spec.className = 'material-spec';
    spec.textContent = row.out.spec;
    material.appendChild(line);
    material.appendChild(spec);
    tr.appendChild(material);

    var unit = makeCell('col-unit num');
    var unitText = document.createElement('span');
    unitText.className = 'unit';
    unitText.textContent = row.out.unit;
    unit.appendChild(unitText);
    tr.appendChild(unit);

    var outQty = makeCell('col-qty num');
    var outText = document.createElement('span');
    outText.className = 'qty';
    outText.textContent = String(row.out.quantity);
    outQty.appendChild(outText);
    tr.appendChild(outQty);

    var inQty = makeCell('col-qty num');
    var inText = document.createElement('span');
    inText.className = 'qty';
    inText.textContent = row.inc ? String(row.inc.quantity) : '—';
    inQty.appendChild(inText);
    tr.appendChild(inQty);

    var diff = makeCell('col-diff num');
    var diffText = document.createElement('span');
    diffText.className = 'diff-value';
    if (row.diff === null) {
      diff.classList.add('is-even');
      diffText.textContent = '—';
      diff.appendChild(diffText);
    } else {
      diff.classList.add(row.diff === 0 ? 'is-even' : 'is-diff');
      diffText.textContent = diffValue(row.diff);
      var label = document.createElement('span');
      label.className = 'diff-label';
      var dot = document.createElement('i');
      dot.className = 'diff-dot';
      dot.setAttribute('aria-hidden', 'true');
      var labelText = document.createElement('span');
      labelText.textContent = diffLabel(row.diff);
      label.appendChild(dot);
      label.appendChild(labelText);
      diff.appendChild(diffText);
      diff.appendChild(label);
    }
    tr.appendChild(diff);

    return tr;
  }

  function matchesQuery(row, query) {
    if (!query) return true;
    var text = (row.out.id + ' ' + row.out.name + ' ' + row.out.spec).toLowerCase();
    return text.indexOf(query) !== -1;
  }

  function renderSummary() {
    if (diffCount === 0) {
      el.summary.textContent = '共 ' + comparisons.length + ' 项物料，数量均相符';
    } else {
      el.summary.textContent = '共 ' + comparisons.length + ' 项物料，其中 ' + diffCount + ' 项数量有差异';
    }
  }

  function renderEmpty(query) {
    var tr = document.createElement('tr');
    var td = makeCell('empty-cell');
    td.colSpan = 5;
    var box = document.createElement('div');
    box.className = 'empty';
    var text = document.createElement('p');
    text.textContent = '没有找到与“' + query + '”匹配的物料';
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-secondary';
    button.textContent = '清除搜索';
    button.addEventListener('click', function () {
      clearSearch();
    });
    box.appendChild(text);
    box.appendChild(button);
    td.appendChild(box);
    tr.appendChild(td);
    return tr;
  }

  function renderRows() {
    var query = el.search.value.trim().toLowerCase();
    el.rows.textContent = '';
    var visible = comparisons.filter(function (row) {
      return matchesQuery(row, query);
    });
    if (!visible.length) {
      el.rows.appendChild(renderEmpty(el.search.value.trim()));
      return;
    }
    visible.forEach(function (row) {
      el.rows.appendChild(buildRow(row));
    });
  }

  function clearSearch() {
    el.search.value = '';
    el.clearSearch.hidden = true;
    renderRows();
    el.search.focus();
  }

  el.search.addEventListener('input', function () {
    el.clearSearch.hidden = !el.search.value;
    renderRows();
  });

  el.clearSearch.addEventListener('click', clearSearch);

  /* ---------- 调拨说明 ---------- */

  var saving = false;
  var feedbackTimer = null;

  function setNoteView() {
    var text = store.getNote();
    if (text) {
      el.noteView.textContent = text;
      el.noteView.classList.remove('is-empty');
    } else {
      el.noteView.textContent = '尚未填写调拨说明';
      el.noteView.classList.add('is-empty');
    }
  }

  function updateCounter() {
    el.noteCounter.textContent = el.noteInput.value.length + ' / 1000';
  }

  function autoGrow() {
    var max = 340;
    var input = el.noteInput;
    input.style.height = 'auto';
    var height = Math.min(input.scrollHeight, max);
    input.style.height = height + 'px';
    if (height < max && input.scrollHeight > input.clientHeight) {
      input.style.height = input.scrollHeight + 'px';
    }
  }

  function showFeedback(text) {
    el.noteFeedback.textContent = text;
    if (feedbackTimer) {
      window.clearTimeout(feedbackTimer);
    }
    feedbackTimer = window.setTimeout(function () {
      el.noteFeedback.textContent = '';
    }, 4000);
  }

  function openEditor() {
    el.noteView.hidden = true;
    el.noteForm.hidden = false;
    el.noteInput.value = store.getNote();
    el.noteError.hidden = true;
    el.noteError.textContent = '';
    el.saveNoteBtn.textContent = '保存说明';
    el.saveNoteBtn.disabled = false;
    el.cancelNoteBtn.disabled = false;
    updateCounter();
    autoGrow();
    el.noteInput.focus();
    el.noteInput.setSelectionRange(el.noteInput.value.length, el.noteInput.value.length);
  }

  function closeEditor() {
    el.noteForm.hidden = true;
    el.noteView.hidden = false;
    setNoteView();
    el.editNoteBtn.focus();
  }

  el.editNoteBtn.addEventListener('click', openEditor);

  el.cancelNoteBtn.addEventListener('click', function () {
    if (saving) return;
    closeEditor();
  });

  el.noteInput.addEventListener('input', function () {
    updateCounter();
    autoGrow();
  });

  el.noteForm.addEventListener('submit', function (event) {
    event.preventDefault();
    if (saving) return;

    saving = true;
    el.saveNoteBtn.disabled = true;
    el.cancelNoteBtn.disabled = true;
    el.saveNoteBtn.textContent = '保存中…';
    el.noteError.hidden = true;
    el.noteError.textContent = '';

    store.saveNote(el.noteInput.value).then(function () {
      saving = false;
      el.noteForm.hidden = true;
      el.noteView.hidden = false;
      setNoteView();
      el.saveNoteBtn.textContent = '保存说明';
      el.saveNoteBtn.disabled = false;
      el.cancelNoteBtn.disabled = false;
      var now = new Date();
      var time = ('0' + now.getHours()).slice(-2) + ':' + ('0' + now.getMinutes()).slice(-2);
      showFeedback('已保存 ' + time);
      el.editNoteBtn.focus();
    }, function () {
      saving = false;
      el.saveNoteBtn.disabled = false;
      el.cancelNoteBtn.disabled = false;
      el.saveNoteBtn.textContent = '重试保存';
      el.noteError.textContent = '保存失败，内容未丢失，请重试。';
      el.noteError.hidden = false;
      el.noteInput.focus();
    });
  });

  /* ---------- 初始化 ---------- */

  renderSummary();
  renderRows();
  setNoteView();
})();

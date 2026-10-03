(function () {
  'use strict';

  /* ---------- 本地模拟数据 ---------- */

  var state = {
    reader: { name: '林予宁', readerId: 'R2026032' },
    library: { name: '青禾学院图书馆', pickup: '主馆一层服务台' },
    books: [
      { id: 'B01', title: '界面设计的秩序', author: '程言', category: '设计', available: 3, location: '主馆三层设计书区A12排' },
      { id: 'B02', title: '信息架构实践：从用户任务到跨平台产品设计与维护', author: '许文川', category: '设计', available: 1, location: '东区分馆二层数字产品与信息技术专业书区B08排' },
      { id: 'B03', title: '城市与日常生活', author: '江映澄', category: '人文', available: 0, location: '主馆二层人文书区C05排' },
      { id: 'B04', title: '看见数据', author: '沈一衡', category: '技术', available: 2, location: '主馆三层技术书区A06排' },
      { id: 'B05', title: '纸上的建筑', author: '顾景行', category: '艺术', available: 4, location: '主馆三层艺术书区D02排' },
      { id: 'B06', title: '植物观察笔记', author: '赵雨禾', category: '自然', available: 1, location: '西区分馆一层自然科学书区A03排' }
    ],
    reservations: [
      { id: 'YY20261003001', bookId: 'B02', status: 'ready', created: '2026-10-03 08:30', pickupUntil: '2026-10-05 18:00' },
      { id: 'YY20261002006', bookId: 'B04', status: 'collected', created: '2026-10-02 10:20', pickupUntil: '2026-10-04 18:00' }
    ]
  };

  /* ---------- 派生读取 ---------- */

  function pad2(n) {
    return (n < 10 ? '0' : '') + n;
  }

  function formatDateTime(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }

  function parseDateTime(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(text);
    if (!m) return null;
    return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
  }

  function formatDeadline(text) {
    var d = parseDateTime(text);
    if (!d) return text;
    return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ' 前';
  }

  function deadlineFrom(now) {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 2, 18, 0, 0, 0);
  }

  function bookById(id) {
    for (var i = 0; i < state.books.length; i += 1) {
      if (state.books[i].id === id) return state.books[i];
    }
    return null;
  }

  function reservationById(id) {
    for (var i = 0; i < state.reservations.length; i += 1) {
      if (state.reservations[i].id === id) return state.reservations[i];
    }
    return null;
  }

  function readyReservationFor(bookId) {
    for (var i = 0; i < state.reservations.length; i += 1) {
      var r = state.reservations[i];
      if (r.bookId === bookId && r.status === 'ready') return r;
    }
    return null;
  }

  function canReserve(book) {
    return book.available > 0 && !readyReservationFor(book.id);
  }

  function blockedReason(book) {
    if (readyReservationFor(book.id)) {
      return '你已有这本书的待取预约，取书后可再次预约。';
    }
    if (book.available <= 0) {
      return '这本书当前没有可预约册数。';
    }
    return '';
  }

  var STATUS_TEXT = { ready: '待取书', collected: '已取书', cancelled: '已取消' };

  /* ---------- 本地模拟数据层 ---------- */

  function nextReservationId() {
    var now = new Date();
    var prefix = 'YY' + now.getFullYear() + pad2(now.getMonth() + 1) + pad2(now.getDate());
    var count = 0;
    state.reservations.forEach(function (r) {
      if (r.id.indexOf(prefix) === 0) count += 1;
    });
    return prefix + String(count + 1).padStart(3, '0');
  }

  var store = {
    getState: function () {
      return state;
    },
    reserve: function (bookId) {
      return new Promise(function (resolve, reject) {
        window.setTimeout(function () {
          var book = bookById(bookId);
          if (!book || !canReserve(book)) {
            reject(new Error('该书当前不可预约'));
            return;
          }
          var now = new Date();
          var reservation = {
            id: nextReservationId(),
            bookId: bookId,
            status: 'ready',
            created: formatDateTime(now),
            pickupUntil: formatDateTime(deadlineFrom(now))
          };
          book.available -= 1;
          state.reservations.push(reservation);
          resolve({ reservation: reservation });
        }, 400);
      });
    },
    cancel: function (reservationId) {
      return new Promise(function (resolve, reject) {
        window.setTimeout(function () {
          var reservation = reservationById(reservationId);
          if (!reservation || reservation.status !== 'ready') {
            reject(new Error('该预约当前不能取消'));
            return;
          }
          reservation.status = 'cancelled';
          var book = bookById(reservation.bookId);
          if (book) book.available += 1;
          resolve({ reservation: reservation });
        }, 300);
      });
    }
  };

  window.LibraryApp = { store: store };

  /* ---------- DOM ---------- */

  var el = {
    app: document.getElementById('app'),
    navBack: document.getElementById('navBack'),
    navTitle: document.getElementById('navTitle'),
    viewBooks: document.getElementById('viewBooks'),
    viewBook: document.getElementById('viewBook'),
    viewReservations: document.getElementById('viewReservations'),
    bookSearch: document.getElementById('bookSearch'),
    bookSearchClear: document.getElementById('bookSearchClear'),
    bookList: document.getElementById('bookList'),
    bookDetail: document.getElementById('bookDetail'),
    reservationGroups: document.getElementById('reservationGroups'),
    tabbar: document.getElementById('tabbar'),
    tabBooks: document.getElementById('tabBooks'),
    tabReservations: document.getElementById('tabReservations'),
    readyBadge: document.getElementById('readyBadge'),
    sheetLayer: document.getElementById('sheetLayer'),
    sheetMask: document.getElementById('sheetMask'),
    sheetTitle: document.getElementById('sheetTitle'),
    sheetBody: document.getElementById('sheetBody'),
    sheetError: document.getElementById('sheetError'),
    sheetSecondary: document.getElementById('sheetSecondary'),
    sheetPrimary: document.getElementById('sheetPrimary'),
    toast: document.getElementById('toast')
  };

  /* ---------- 小工具 ---------- */

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function icon(id, className) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon' + (className ? ' ' + className : ''));
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', 'assets/icons.svg#' + id);
    svg.appendChild(use);
    return svg;
  }

  var toastTimer = null;
  function showToast(text) {
    el.toast.textContent = text;
    el.toast.hidden = false;
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () {
      el.toast.hidden = true;
    }, 2200);
  }

  /* ---------- 路由 ---------- */

  var listScroll = 0;
  var lastReservedBookId = null;
  var prevRoute = null;

  function currentRoute() {
    var hash = window.location.hash || '#/books';
    if (hash.indexOf('#/book/') === 0) {
      return { name: 'book', bookId: hash.slice('#/book/'.length) };
    }
    if (hash === '#/reservations') {
      return { name: 'reservations' };
    }
    return { name: 'books' };
  }

  function go(hash) {
    if (window.location.hash === hash) {
      render();
      return;
    }
    window.location.hash = hash;
  }

  /* ---------- 找书列表 ---------- */

  function renderBookRows() {
    var query = el.bookSearch.value.trim().toLowerCase();
    el.bookList.textContent = '';

    var visible = state.books.filter(function (book) {
      if (!query) return true;
      return book.title.toLowerCase().indexOf(query) !== -1 || book.author.toLowerCase().indexOf(query) !== -1;
    });

    if (!visible.length) {
      var wrap = make('div', 'empty');
      wrap.appendChild(make('p', '', '没有找到与“' + el.bookSearch.value.trim() + '”相关的书目'));
      var clearButton = make('button', 'btn btn-secondary', '清除搜索');
      clearButton.type = 'button';
      clearButton.addEventListener('click', clearBookSearch);
      wrap.appendChild(clearButton);
      el.bookList.appendChild(wrap);
      return;
    }

    visible.forEach(function (book) {
      var li = document.createElement('li');
      var row = make('button', 'book-row');
      row.type = 'button';
      row.setAttribute('aria-label', book.title + '，' + book.author);

      var main = make('div', 'book-main');
      main.appendChild(make('h2', 'book-title', book.title));
      main.appendChild(make('p', 'book-author', book.author));
      main.appendChild(make('p', 'book-location', '馆藏 ' + book.location));
      row.appendChild(main);

      var side = make('div', 'book-side');
      if (book.available > 0) {
        var avail = make('span', 'avail');
        avail.appendChild(document.createTextNode('可约 '));
        avail.appendChild(make('strong', '', String(book.available)));
        avail.appendChild(document.createTextNode(' 册'));
        side.appendChild(avail);
      } else {
        side.appendChild(make('span', 'avail avail--no', '暂无可约'));
      }
      if (readyReservationFor(book.id)) {
        var stateLine = make('span', 'row-state');
        stateLine.appendChild(make('i', 'dot'));
        stateLine.appendChild(document.createTextNode('已有待取'));
        side.appendChild(stateLine);
      }
      side.appendChild(icon('chevron-right', 'book-chevron'));
      row.appendChild(side);

      row.addEventListener('click', function () {
        listScroll = window.scrollY;
        go('#/book/' + book.id);
      });

      li.appendChild(row);
      el.bookList.appendChild(li);
    });
  }

  function clearBookSearch() {
    el.bookSearch.value = '';
    el.bookSearchClear.hidden = true;
    renderBookRows();
    el.bookSearch.focus();
  }

  el.bookSearch.addEventListener('input', function () {
    el.bookSearchClear.hidden = !el.bookSearch.value;
    renderBookRows();
  });

  el.bookSearchClear.addEventListener('click', clearBookSearch);

  /* ---------- 书目详情 ---------- */

  function infoRow(label, value, valueClass) {
    var row = make('div', 'info-row');
    row.appendChild(make('dt', '', label));
    var dd = make('dd', valueClass || '', value);
    row.appendChild(dd);
    return row;
  }

  function renderBookDetail(bookId) {
    var book = bookById(bookId);
    el.bookDetail.textContent = '';
    if (!book) {
      go('#/books');
      return;
    }

    if (lastReservedBookId === book.id) {
      var banner = make('div', 'success-banner');
      banner.appendChild(icon('check-circle-2'));
      var bannerBody = make('div');
      var reservation = readyReservationFor(book.id);
      var message = '预约成功，请于 ' + formatDeadline(reservation.pickupUntil) + '到' + state.library.pickup + '取书。';
      bannerBody.appendChild(make('p', '', message));
      var link = make('button', 'link-btn', '查看我的预约');
      link.type = 'button';
      link.addEventListener('click', function () {
        go('#/reservations');
      });
      bannerBody.appendChild(link);
      banner.appendChild(bannerBody);
      el.bookDetail.appendChild(banner);
    }

    var card = make('div', 'detail-card');
    card.appendChild(make('h2', 'detail-title', book.title));
    card.appendChild(make('p', 'detail-author', book.author + ' 著'));

    var info = make('dl', 'detail-info');
    info.appendChild(infoRow('分类', book.category));
    if (book.available > 0) {
      info.appendChild(infoRow('可约册数', book.available + ' 册'));
    } else {
      info.appendChild(infoRow('可约册数', '暂无可约'));
    }
    info.appendChild(infoRow('馆藏位置', book.location));
    info.appendChild(infoRow('取书地点', state.library.pickup));
    info.appendChild(infoRow('取书期限', '预约提交后两天内，当日 18:00 前'));
    card.appendChild(info);

    var reason = blockedReason(book);
    if (reason) {
      var notice = make('p', 'notice');
      notice.appendChild(make('i', 'dot'));
      notice.appendChild(make('span', '', reason));
      card.appendChild(notice);
    }

    el.bookDetail.appendChild(card);

    var can = canReserve(book);
    var actionBar = make('div', 'action-bar');
    var button = make('button', 'btn btn-primary btn-block', can ? '预约这本书' : '暂不可预约');
    button.type = 'button';
    button.disabled = !can;
    if (can) {
      button.addEventListener('click', function () {
        openReserveSheet(book.id);
      });
    }
    actionBar.appendChild(button);
    el.bookDetail.appendChild(actionBar);
  }

  /* ---------- 我的预约 ---------- */

  function reservationCard(reservation) {
    var book = bookById(reservation.bookId);
    var card = make('div', 'res-card');

    var head = make('div', 'res-head');
    head.appendChild(make('h2', 'res-title', book ? book.title : reservation.bookId));
    var status = make('span', 'status status--' + reservation.status);
    status.appendChild(make('i', 'dot'));
    status.appendChild(document.createTextNode(STATUS_TEXT[reservation.status] || reservation.status));
    head.appendChild(status);
    card.appendChild(head);

    if (book) card.appendChild(make('p', 'res-author', book.author));

    var info = make('dl', 'res-info');
    info.appendChild(infoRow('预约编号', reservation.id));
    if (reservation.status === 'ready') {
      info.appendChild(infoRow('取书地点', state.library.pickup));
      info.appendChild(infoRow('取书期限', formatDeadline(reservation.pickupUntil), 'res-deadline'));
      info.appendChild(infoRow('预约时间', reservation.created));
    } else {
      info.appendChild(infoRow('预约时间', reservation.created));
      info.appendChild(infoRow('原取书期限', formatDeadline(reservation.pickupUntil)));
    }
    card.appendChild(info);

    if (reservation.status === 'ready') {
      var actions = make('div', 'res-actions');
      var cancelButton = make('button', 'btn btn-secondary', '取消预约');
      cancelButton.type = 'button';
      cancelButton.addEventListener('click', function () {
        openCancelSheet(reservation.id);
      });
      actions.appendChild(cancelButton);
      card.appendChild(actions);
    }

    return card;
  }

  function renderReservations() {
    el.reservationGroups.textContent = '';

    var ready = state.reservations.filter(function (r) { return r.status === 'ready'; });
    var ended = state.reservations.filter(function (r) { return r.status !== 'ready'; });
    ready.sort(function (a, b) { return b.created.localeCompare(a.created); });
    ended.sort(function (a, b) { return b.created.localeCompare(a.created); });

    el.reservationGroups.appendChild(make('p', 'group-title', '待取书'));
    if (ready.length) {
      ready.forEach(function (r) {
        el.reservationGroups.appendChild(reservationCard(r));
      });
    } else {
      el.reservationGroups.appendChild(make('p', 'res-empty', '当前没有待取书的预约。'));
    }

    if (ended.length) {
      el.reservationGroups.appendChild(make('p', 'group-title', '已结束'));
      ended.forEach(function (r) {
        el.reservationGroups.appendChild(reservationCard(r));
      });
    }
  }

  /* ---------- 底部确认面板 ---------- */

  var submitting = false;
  var sheetAction = null;

  function openSheet() {
    el.sheetLayer.hidden = false;
  }

  function closeSheet() {
    if (submitting) return;
    el.sheetLayer.hidden = true;
    sheetAction = null;
  }

  function openReserveSheet(bookId) {
    var book = bookById(bookId);
    if (!book) return;
    submitting = false;
    el.sheetTitle.textContent = '确认预约';
    el.sheetBody.textContent = '';
    el.sheetError.hidden = true;
    el.sheetError.textContent = '';
    el.sheetPrimary.textContent = '提交预约';
    el.sheetPrimary.className = 'btn btn-primary';
    el.sheetPrimary.disabled = false;
    el.sheetSecondary.textContent = '再想想';
    el.sheetSecondary.disabled = false;

    el.sheetBody.appendChild(make('p', 'sheet-book', '《' + book.title + '》'));
    var info = make('dl', 'sheet-info');
    info.appendChild(infoRow('取书地点', state.library.pickup));
    info.appendChild(infoRow('取书期限', formatDeadline(formatDateTime(deadlineFrom(new Date()))) ));
    el.sheetBody.appendChild(info);
    el.sheetBody.appendChild(make('p', 'sheet-note', '提交后为你保留该册，请在取书期限内到服务台取书。'));

    sheetAction = function () {
      if (submitting) return;
      submitting = true;
      el.sheetPrimary.disabled = true;
      el.sheetSecondary.disabled = true;
      el.sheetPrimary.textContent = '提交中…';
      el.sheetError.hidden = true;

      store.reserve(bookId).then(function () {
        submitting = false;
        lastReservedBookId = bookId;
        el.sheetLayer.hidden = true;
        sheetAction = null;
        render();
        showToast('预约成功');
      }, function () {
        submitting = false;
        el.sheetPrimary.disabled = false;
        el.sheetSecondary.disabled = false;
        el.sheetPrimary.textContent = '重试预约';
        el.sheetError.textContent = '提交失败，请重试。';
        el.sheetError.hidden = false;
      });
    };

    openSheet();
  }

  function openCancelSheet(reservationId) {
    var reservation = reservationById(reservationId);
    if (!reservation) return;
    var book = bookById(reservation.bookId);
    submitting = false;
    el.sheetTitle.textContent = '取消预约';
    el.sheetBody.textContent = '';
    el.sheetError.hidden = true;
    el.sheetError.textContent = '';
    el.sheetPrimary.textContent = '确认取消预约';
    el.sheetPrimary.className = 'btn btn-danger';
    el.sheetPrimary.disabled = false;
    el.sheetSecondary.textContent = '再想想';
    el.sheetSecondary.disabled = false;

    el.sheetBody.appendChild(make('p', 'sheet-book', book ? '《' + book.title + '》' : reservation.id));
    var info = make('dl', 'sheet-info');
    info.appendChild(infoRow('预约编号', reservation.id));
    info.appendChild(infoRow('取书期限', formatDeadline(reservation.pickupUntil)));
    el.sheetBody.appendChild(info);
    el.sheetBody.appendChild(make('p', 'sheet-note', '取消后该预约结束，该册释放回可预约数量。'));

    sheetAction = function () {
      if (submitting) return;
      submitting = true;
      el.sheetPrimary.disabled = true;
      el.sheetSecondary.disabled = true;
      el.sheetPrimary.textContent = '处理中…';
      el.sheetError.hidden = true;

      store.cancel(reservationId).then(function () {
        submitting = false;
        el.sheetLayer.hidden = true;
        sheetAction = null;
        render();
        showToast('已取消预约');
      }, function () {
        submitting = false;
        el.sheetPrimary.disabled = false;
        el.sheetSecondary.disabled = false;
        el.sheetPrimary.textContent = '重试取消';
        el.sheetError.textContent = '取消失败，请重试。';
        el.sheetError.hidden = false;
      });
    };

    openSheet();
  }

  el.sheetPrimary.addEventListener('click', function () {
    if (sheetAction) sheetAction();
  });

  el.sheetSecondary.addEventListener('click', closeSheet);
  el.sheetMask.addEventListener('click', closeSheet);

  /* ---------- 底部导航与徽标 ---------- */

  function renderBadge() {
    var count = state.reservations.filter(function (r) { return r.status === 'ready'; }).length;
    if (count > 0) {
      el.readyBadge.textContent = String(count);
      el.readyBadge.hidden = false;
    } else {
      el.readyBadge.hidden = true;
    }
  }

  el.tabBooks.addEventListener('click', function () {
    go('#/books');
  });

  el.tabReservations.addEventListener('click', function () {
    go('#/reservations');
  });

  el.navBack.addEventListener('click', function () {
    if (window.history.length > 1) {
      window.history.back();
    } else {
      go('#/books');
    }
  });

  /* ---------- 渲染与路由 ---------- */

  function render() {
    var route = currentRoute();

    el.viewBooks.hidden = route.name !== 'books';
    el.viewBook.hidden = route.name !== 'book';
    el.viewReservations.hidden = route.name !== 'reservations';

    var isTab = route.name !== 'book';
    el.tabbar.hidden = !isTab;
    el.app.classList.toggle('has-tabbar', isTab);
    el.app.classList.toggle('has-detail', !isTab);
    el.navBack.hidden = route.name !== 'book';
    el.tabBooks.classList.toggle('is-active', route.name === 'books');
    el.tabReservations.classList.toggle('is-active', route.name === 'reservations');

    if (route.name === 'book') {
      el.navTitle.textContent = '书目详情';
      renderBookDetail(route.bookId);
    } else {
      if (route.name === 'reservations') {
        el.navTitle.textContent = '我的预约';
        renderReservations();
      } else {
        el.navTitle.textContent = state.library.name;
        renderBookRows();
      }
    }

    if (route.name === 'books') {
      if (prevRoute && prevRoute.name === 'book') {
        window.scrollTo(0, listScroll);
      } else if (!prevRoute) {
        window.scrollTo(0, 0);
      }
    } else {
      window.scrollTo(0, 0);
    }

    if (route.name !== 'book') lastReservedBookId = null;
    renderBadge();
    prevRoute = route;
  }

  window.addEventListener('hashchange', render);

  render();
})();

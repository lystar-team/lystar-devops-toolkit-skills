/* 测试适配器（不参与产品页面）
 * 仅在验收时由测试命令加载，用于模拟提交失败与延迟。
 * URL 参数：?reserve=fail-once|fail-always|slow&cancel=fail-once|slow
 */
(function () {
  'use strict';

  var app = window.LibraryApp;
  if (!app || !app.store) {
    return;
  }

  var params = new URLSearchParams(window.location.search);
  var reserveMode = params.get('reserve') || 'off';
  var cancelMode = params.get('cancel') || 'off';
  var stats = { reserveMode: reserveMode, cancelMode: cancelMode, reserveCalls: 0, cancelCalls: 0 };
  window.__testAdapter = stats;

  var realReserve = app.store.reserve.bind(app.store);
  var realCancel = app.store.cancel.bind(app.store);

  function rejectAfter(delay) {
    return new Promise(function (resolve, reject) {
      window.setTimeout(function () {
        reject(new Error('模拟网络异常（测试适配器）'));
      }, delay);
    });
  }

  app.store.reserve = function (bookId) {
    stats.reserveCalls += 1;
    if (reserveMode === 'fail-once' && stats.reserveCalls === 1) {
      return rejectAfter(300);
    }
    if (reserveMode === 'fail-always') {
      return rejectAfter(300);
    }
    if (reserveMode === 'slow') {
      return new Promise(function (resolve) {
        window.setTimeout(function () {
          resolve(realReserve(bookId));
        }, 1500);
      });
    }
    return realReserve(bookId);
  };

  app.store.cancel = function (reservationId) {
    stats.cancelCalls += 1;
    if (cancelMode === 'fail-once' && stats.cancelCalls === 1) {
      return rejectAfter(300);
    }
    if (cancelMode === 'slow') {
      return new Promise(function (resolve) {
        window.setTimeout(function () {
          resolve(realCancel(reservationId));
        }, 1200);
      });
    }
    return realCancel(reservationId);
  };
})();

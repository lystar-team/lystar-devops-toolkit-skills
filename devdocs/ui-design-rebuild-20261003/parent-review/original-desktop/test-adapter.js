/* 测试适配器（不参与产品页面）
 * 仅在验收时由测试命令加载，用于模拟保存失败与延迟。
 * URL 参数：?save=fail-once | fail-always | slow
 */
(function () {
  'use strict';

  var app = window.TransferCheck;
  if (!app || !app.store) {
    return;
  }

  var params = new URLSearchParams(window.location.search);
  var mode = params.get('save') || 'off';
  var stats = { mode: mode, saveCalls: 0 };
  window.__testAdapter = stats;

  var realSave = app.store.saveNote.bind(app.store);

  function rejectAfter(delay) {
    return new Promise(function (resolve, reject) {
      window.setTimeout(function () {
        reject(new Error('模拟保存失败（测试适配器）'));
      }, delay);
    });
  }

  app.store.saveNote = function (text) {
    stats.saveCalls += 1;
    if (mode === 'fail-once' && stats.saveCalls === 1) {
      return rejectAfter(250);
    }
    if (mode === 'fail-always') {
      return rejectAfter(250);
    }
    if (mode === 'slow') {
      return new Promise(function (resolve) {
        window.setTimeout(function () {
          resolve(realSave(text));
        }, 1500);
      });
    }
    return realSave(text);
  };
})();

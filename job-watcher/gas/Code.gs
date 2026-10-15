/**
 * 案件ウォッチャー：スプレッドシート側（Google Apps Script）
 *
 * Python から POST された案件をシートに追記する。
 * URL で重複判定し、「新しく追加された案件の URL」だけを返す。
 * → Python 側はそれだけを Slack に通知する（＝スプシが既読管理を兼ねる）。
 *
 * セットアップ：
 *   1. スプレッドシートを作り、拡張機能 → Apps Script にこのファイルを貼る
 *   2. プロジェクトの設定 → スクリプト プロパティ に TOKEN（任意のランダム文字列）を追加
 *   3. デプロイ → 新しいデプロイ → 種類「ウェブアプリ」
 *        実行ユーザー: 自分 / アクセスできるユーザー: 全員
 *   4. 発行された URL を .env の GAS_WEBAPP_URL に入れる
 */

const SHEET_NAME = '案件';
const HEADERS = ['取得日時', 'サイト', 'キーワード', 'タイトル', '報酬', '締切', 'URL', 'ステータス', 'メモ'];
const URL_COL = HEADERS.indexOf('URL') + 1;

function doPost(e) {
  const body = JSON.parse(e.postData.contents);
  const token = PropertiesService.getScriptProperties().getProperty('TOKEN');
  if (!token || body.token !== token) {
    return json_({ ok: false, error: 'unauthorized' });
  }

  const lock = LockService.getScriptLock();
  lock.waitLock(30 * 1000);
  try {
    const sheet = getSheet_();
    const lastRow = sheet.getLastRow();
    const wasEmpty = lastRow <= 1;
    const seen = new Set(
      wasEmpty ? [] : sheet.getRange(2, URL_COL, lastRow - 1, 1).getValues().map(r => r[0])
    );

    const now = new Date();
    const added = [];
    (body.jobs || []).forEach(job => {
      if (!job.url || seen.has(job.url)) return;
      seen.add(job.url);
      added.push(job);
    });

    if (added.length) {
      const rows = added.map(j => [now, j.site, j.keyword, j.title, j.price, j.deadline, j.url, '未確認', '']);
      sheet.getRange(lastRow + 1, 1, rows.length, HEADERS.length).setValues(rows);
    }
    return json_({ ok: true, was_empty: wasEmpty, added: added.map(j => j.url) });
  } finally {
    lock.releaseLock();
  }
}

function getSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(SHEET_NAME);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_NAME);
  }
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight('bold');
    // ステータス列をプルダウンにしておく（応募管理に使える）
    const rule = SpreadsheetApp.newDataValidation()
      .requireValueInList(['未確認', '検討中', '応募済', '見送り'], true)
      .build();
    sheet.getRange(2, HEADERS.indexOf('ステータス') + 1, sheet.getMaxRows() - 1, 1).setDataValidation(rule);
  }
  return sheet;
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/**
 * momo_bot_logs 기록용 웹앱
 *
 * 설치: momo_bot_logs 스프레드시트 > 확장 프로그램 > Apps Script 에 이 코드를 붙여넣고
 *       배포 > 새 배포 > 웹 앱 (실행 사용자: 나 / 액세스 권한: 모든 사용자)
 *
 * 이 스크립트는 컬럼 순서를 하드코딩하지 않는다. 각 탭의 1행을 읽어 키로 매핑하므로,
 * 시트에 컬럼을 추가하거나 순서를 바꿔도 여기를 고칠 필요가 없다.
 * 설계서의 "컬럼을 하드코딩하지 말 것" 원칙을 로그 쓰기에도 적용한 것이다.
 */

// 배포 후 이 값을 바꾸고, 같은 값을 Streamlit Secrets 에도 넣는다.
// 웹앱이 '모든 사용자' 로 열려 있으므로 이것이 유일한 차단 수단이다.
var SECRET_TOKEN = 'CHANGE_ME';

// 구글 시트 셀 한도는 5만 자다. llm_raw_json 같은 컬럼이 넘칠 수 있어 여유를 두고 자른다.
var MAX_CELL = 45000;

// 값이 비어 있으면 서버 시각(KST)으로 채우는 컬럼. 테스터 PC 시계에 의존하지 않기 위한 것이다.
var TIME_COLUMNS = ['logged_at', 'judged_at', 'created_at', 'timestamp'];


function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    // 테스터가 2명이라 동시 append 가 겹칠 수 있다
    lock.waitLock(30000);
  } catch (err) {
    return json({ ok: false, error: 'LOCK_TIMEOUT' });
  }

  try {
    if (!e || !e.postData || !e.postData.contents) {
      return json({ ok: false, error: 'NO_BODY' });
    }

    var req = JSON.parse(e.postData.contents);

    if (req.token !== SECRET_TOKEN) {
      return json({ ok: false, error: 'BAD_TOKEN' });
    }

    var tabName = req.tab;
    var rows = req.rows;
    if (!tabName) return json({ ok: false, error: 'NO_TAB' });
    if (!rows || !rows.length) return json({ ok: false, error: 'NO_ROWS' });

    var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(tabName);
    if (!sheet) {
      // 탭 이름 오타를 조용히 넘기지 않는다. 어떤 탭이 있는지 함께 돌려준다.
      return json({ ok: false, error: 'TAB_NOT_FOUND', tab: tabName, available: tabNames() });
    }

    var header = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
    var index = {};
    for (var i = 0; i < header.length; i++) {
      var name = String(header[i]).trim();
      if (name) index[name] = i;
    }

    var now = kstNow();
    var unknown = {};
    var out = [];

    for (var r = 0; r < rows.length; r++) {
      var obj = rows[r];
      var line = new Array(header.length).fill('');

      for (var key in obj) {
        if (!obj.hasOwnProperty(key)) continue;
        if (!(key in index)) {
          // 시트에 없는 키. 버리되 무엇이 버려졌는지 응답에 담아 스키마 어긋남을 드러낸다.
          unknown[key] = true;
          continue;
        }
        line[index[key]] = clip(obj[key]);
      }

      // 비어 있는 시각 컬럼을 서버 시각으로 채운다
      for (var t = 0; t < TIME_COLUMNS.length; t++) {
        var col = TIME_COLUMNS[t];
        if (col in index && line[index[col]] === '') line[index[col]] = now;
      }

      out.push(line);
    }

    // 행 단위 appendRow 를 반복하면 느리다. 한 번에 쓴다.
    sheet.getRange(sheet.getLastRow() + 1, 1, out.length, header.length).setValues(out);

    return json({
      ok: true,
      tab: tabName,
      appended: out.length,
      unknown_keys: Object.keys(unknown),
      server_time: now
    });

  } catch (err) {
    return json({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}


/**
 * ?token=...            배포 상태와 각 탭의 헤더를 돌려준다
 * ?token=...&tab=이름   그 탭의 행을 헤더 기준 객체 배열로 돌려준다
 *
 * 로그 시트를 비공개로 유지하면서도 보고서 화면이 집계할 수 있게 하는 통로다.
 * 시트를 공개로 바꾸면 테스트 대화의 주소·전화번호가 링크만으로 열린다.
 */
function doGet(e) {
  if (!e || !e.parameter || e.parameter.token !== SECRET_TOKEN) {
    return json({ ok: false, error: 'BAD_TOKEN' });
  }

  var want = e.parameter.tab;
  if (want) {
    var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(want);
    if (!sh) return json({ ok: false, error: 'TAB_NOT_FOUND', available: tabNames() });

    var lastRow = sh.getLastRow();
    var lastCol = sh.getLastColumn();
    if (lastRow < 2 || lastCol < 1) return json({ ok: true, tab: want, rows: [] });

    var values = sh.getRange(1, 1, lastRow, lastCol).getValues();
    var head = values[0].map(function (h) { return String(h).trim(); });

    var rows = [];
    for (var r = 1; r < values.length; r++) {
      var obj = {};
      for (var c = 0; c < head.length; c++) {
        if (head[c]) obj[head[c]] = values[r][c];
      }
      rows.push(obj);
    }
    return json({ ok: true, tab: want, rows: rows });
  }

  var tabs = {};
  var sheets = SpreadsheetApp.getActiveSpreadsheet().getSheets();
  for (var i = 0; i < sheets.length; i++) {
    var s = sheets[i];
    var lastCol = s.getLastColumn();
    tabs[s.getName()] = {
      columns: lastCol ? s.getRange(1, 1, 1, lastCol).getValues()[0].filter(String) : [],
      rows: Math.max(0, s.getLastRow() - 1)
    };
  }
  return json({ ok: true, tabs: tabs, server_time: kstNow() });
}


// ---------------------------------------------------------------- 보조

function clip(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') v = JSON.stringify(v);
  v = String(v);
  return v.length > MAX_CELL ? v.substring(0, MAX_CELL) + '…[truncated]' : v;
}

function kstNow() {
  return Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd HH:mm:ss');
}

function tabNames() {
  return SpreadsheetApp.getActiveSpreadsheet().getSheets().map(function (s) {
    return s.getName();
  });
}

function json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

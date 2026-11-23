// The old Apps Script dashboard's getData() from Code.gs, unchanged, run on the sheet as the Apps Script saw it (typed cells).
// Usage: TZ=Asia/Kolkata node ref.js pm.json bt.json > ref.json
const fs = require('fs');
function toCell(c) {
  if (c && typeof c === 'object' && 'd' in c) { const days = Math.floor(c.d), secs = Math.round((c.d - days) * 86400); return new Date(1899, 11, 30 + days, 0, 0, secs); }
  return c === undefined || c === null ? '' : c;
}
function grid(f) { // formula errors: Apps Script's getValues gives the error as shown (#N/A), not the API's long message
  const j = JSON.parse(fs.readFileSync(f)); const t = j.typed, F = j.formatted || []; const w = Math.max(...t.map(r => r.length));
  return t.map((r, ri) => { const o = []; for (let i = 0; i < w; i++) { let c = i < r.length ? toCell(r[i]) : '';
    const fv = (F[ri] || [])[i]; if (typeof c === 'string' && typeof fv === 'string' && fv.startsWith('#') && c !== fv && c.startsWith(fv)) c = fv; o.push(c); } return o; });
}
const TABS = { 'Program-Main': grid(process.argv[2]), 'Blood Test': grid(process.argv[3]) };
const SpreadsheetApp = { openById: () => ({ getSheetByName: n => TABS[n] ? { getDataRange: () => ({ getValues: () => TABS[n] }) } : null }) };

function colLetterToIndex(letter) {
  var col = 0;
  letter = letter.toString().toUpperCase();
  for (var i = 0; i < letter.length; i++) { col = col * 26 + (letter.charCodeAt(i) - 64); }
  return col - 1;
}

function getData() {
  try {
    var ss = SpreadsheetApp.openById("10AVYW90pbRe7BSlahszOQuxhk1bFkVAyOMA8UxOLoFE");
    var sheet = ss.getSheetByName("Program-Main");
    if (!sheet) return { error: "Sheet 'Program-Main' not found." };
    var data = sheet.getDataRange().getValues();
    if (data.length < 2) return { rows: [], bt_rows: [], months: [] };
    var headers = data[0];
    var hMap = {};
    headers.forEach(function(h, i) { var clean = h.toString().replace(/\n/g,' ').trim(); hMap[clean] = i; });
    function colByExact(name) {
      if (hMap[name] !== undefined) return hMap[name];
      var nl = name.toLowerCase();
      for (var k in hMap) { if (k.toLowerCase() === nl) return hMap[k]; }
      return -1;
    }
    function colByKeyword(keyword) {
      var kw = keyword.toLowerCase();
      for (var k in hMap) { if (k.toLowerCase().indexOf(kw) !== -1) return hMap[k]; }
      return -1;
    }
    var CI = {
      month : colByKeyword('Month'), plan : colByExact('Plan'), orderId : colByExact('Order ID'), paidAt : colByExact('Paid at'),
      lineitem : colByKeyword('Lineitem name'), duration : colByExact('Duration'), billName : colByExact('Billing Name'), billPhone : colByExact('Billing Phone'),
      status : colByExact('Program Status'), coach : colByExact('Coach Name'), startDate : colByKeyword('Program Start Date'), endDate : colByKeyword('End Date'),
      daysStart : colByKeyword('Program Started'), dc1 : colByExact('DC1'), dc1Stat : colByExact('DC1 Status'), dc2 : colByKeyword('Dr. Consult 2'),
      dc2Stat : colByExact('DC2 Status'), dc2Eligible: colByExact('DC2 Eligible or not?'),
      generalRemark : colLetterToIndex('M'), notStartedReason : colLetterToIndex('V'), dc1Remark : colLetterToIndex('Z'), dc2Remark : colLetterToIndex('AD')
    };
    var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    function toDateObj(v) {
      if (!v || v === '') return null;
      if (v instanceof Date) return isNaN(v.getTime()) ? null : v;
      if (typeof v === 'string') return null;
      var d = new Date(v);
      return isNaN(d.getTime()) ? null : d;
    }
    function fmtDate(v) { var d = toDateObj(v); if (!d) return ''; if (d.getFullYear() < 1970) return ''; return d.getDate() + ' ' + MON[d.getMonth()] + ' ' + String(d.getFullYear()).slice(2); }
    function getYM(v) { var d = toDateObj(v); if (!d) return ''; if (d.getFullYear() < 1970) return ''; return MON[d.getMonth()] + ' ' + d.getFullYear(); }
    function getISO(v) {
      var d = toDateObj(v); if (!d) return ''; if (d.getFullYear() < 1970) return '';
      var mm = String(d.getMonth() + 1).padStart(2, '0'); var dd = String(d.getDate()).padStart(2, '0');
      return d.getFullYear() + '-' + mm + '-' + dd;
    }
    function addDays(v, n) { var d = toDateObj(v); if (!d) return null; var r = new Date(d.getTime()); r.setDate(r.getDate() + n); return r; }
    var rows = [];
    var mthSet = {};
    var cutoff = new Date(2025, 4, 1);
    for (var i = 1; i < data.length; i++) {
      var r = data[i];
      if (!r[CI.month] && !r[CI.orderId] && !r[CI.billName]) continue;
      var paidRaw = r[CI.paidAt] || null;
      var paidD = toDateObj(paidRaw);
      if (paidD && paidD < cutoff) continue;
      var month = getYM(r[CI.month]);
      var plan = (r[CI.plan] || '').toString().trim();
      var duration = parseInt(r[CI.duration]) || 0;
      var progStatus = (r[CI.status] || '').toString().trim();
      var endRaw = r[CI.endDate] || null;
      var startRaw = null;
      var startRawVal = r[CI.startDate];
      if (startRawVal instanceof Date && !isNaN(startRawVal.getTime()) && startRawVal.getFullYear() > 1970) { startRaw = startRawVal; }
      var startDateFormatted = startRaw ? fmtDate(startRaw) : '';
      var daysToStart = null;
      if (startRaw && paidRaw) {
        var paidD2 = toDateObj(paidRaw);
        if (paidD2) { var diff = Math.round((startRaw - paidD2) / 86400000); if (diff >= 0 && diff < 3000) daysToStart = diff; }
      }
      var dc1Eligible = duration >= 90 && ['Fresh','Fresh-B2B','Fresh-VIP','Repeat'].indexOf(plan) !== -1;
      var dc2EligRaw = (CI.dc2Eligible >= 0) ? (r[CI.dc2Eligible] || '').toString().trim().toLowerCase() : '';
      var dc2Eligible;
      if (dc2EligRaw === 'eligible') { dc2Eligible = true; } else if (dc2EligRaw === 'not eligible') { dc2Eligible = false; } else { dc2Eligible = dc1Eligible; }
      var dc1Raw = r[CI.dc1] || null;
      var dc1Stat = (r[CI.dc1Stat] || '').toString().trim();
      var dc1DateObj = toDateObj(dc1Raw);
      var dc2Raw = (CI.dc2 >= 0) ? (r[CI.dc2] || null) : null;
      var dc2Stat = (CI.dc2Stat >= 0) ? (r[CI.dc2Stat] || '').toString().trim() : '';
      var dc2DateObj = toDateObj(dc2Raw);
      var dc1DueObj = addDays(startRaw, 30);
      var dc2DueObj = addDays(endRaw, -15);
      var paidYM = getYM(paidRaw) || month;
      if (paidYM) mthSet[paidYM] = true;
      if (month) mthSet[month] = true;
      rows.push({
        month : month, plan : plan, order_id : (r[CI.orderId] || '').toString().trim(), name : (r[CI.billName] || '').toString().trim(),
        phone : (r[CI.billPhone] || '').toString().trim(), lineitem : (r[CI.lineitem] || '').toString().trim(), coach : (r[CI.coach] || '').toString().trim(),
        duration : duration, prog_status : progStatus, start_date : startDateFormatted, start_iso : startRaw ? getISO(startRaw) : '',
        end_date : fmtDate(endRaw), end_iso : getISO(endRaw), renewal_iso : getISO(addDays(endRaw, -10)), paid_at : fmtDate(paidRaw), paid_iso : getISO(paidRaw),
        days_to_start: daysToStart, dc_eligible : dc1Eligible, dc2_eligible : dc2Eligible, dc1_date : fmtDate(dc1DateObj), dc1_iso : getISO(dc1DateObj),
        dc1_status : dc1Stat, dc1_remark : (r[CI.dc1Remark] || '').toString().trim(), dc2_date : fmtDate(dc2DateObj), dc2_iso : getISO(dc2DateObj),
        dc2_status : dc2Stat, dc2_remark : (r[CI.dc2Remark] || '').toString().trim(), dc1_due_iso : dc1DueObj ? getISO(dc1DueObj) : '',
        dc1_due_date : dc1DueObj ? fmtDate(dc1DueObj) : '', dc2_due_iso : dc2DueObj ? getISO(dc2DueObj) : '', dc2_due_date : dc2DueObj ? fmtDate(dc2DueObj) : '',
        paid_ym : paidYM, remark : (r[CI.generalRemark] || '').toString().trim(), not_started_reason : (r[CI.notStartedReason] || '').toString().trim()
      });
    }
    var btSheet = ss.getSheetByName("Blood Test");
    var bt_rows = [];
    if (btSheet) {
      var btData = btSheet.getDataRange().getValues();
      if (btData.length >= 2) {
        var btHeaders = btData[0];
        var btHMap = {};
        btHeaders.forEach(function(h, i) { var clean = h.toString().replace(/\n/g,' ').trim(); btHMap[clean] = i; });
        function btCol(name) {
          if (btHMap[name] !== undefined) return btHMap[name];
          var nl = name.toLowerCase();
          for (var k in btHMap) { if (k.toLowerCase() === nl) return btHMap[k]; }
          return -1;
        }
        var BT = { month : btCol('Month'), plan : btCol('Plan'), orderId : btCol('Order ID'), duration : btCol('Program Duration'), name : btCol('Billing Name'),
          phone : btCol('BillingPhone'), bt1Stat : btCol('Test 1 Status'), bt1Remark : btCol('Remark'), bt2Stat : btCol('Test 2 Status'), bt2Remark : btCol('Remark of Test 2'),
          coach : btCol('Coach Name'), progStatus : btCol('Program Status'), startDate : btCol('Program Start Date'), startMonth : btCol('Program Start Month'),
          endDate : btCol('End Date'), prevPlan : btCol('previous plan'), prevBt2Done: btCol('2nd done or not') };
        var bt1LabIdx = -1, bt2LabIdx = -1; var foundFirst = false;
        btHeaders.forEach(function(h, i) { var clean = h.toString().replace(/\n/g,' ').trim().toLowerCase(); if (clean === 'lab name') { if (!foundFirst) { foundFirst = true; bt1LabIdx = i; } else { bt2LabIdx = i; } } });
        BT.bt1Lab = bt1LabIdx; BT.bt2Lab = bt2LabIdx;
        var BT_EXCLUDED = ['not started yet','dropout','dropout- not started yet','refunded','cancelled','past client- no data','onbreak'];
        var BT_DONE_STATUSES = ['report shared/uploaded','report shared/ uploaded','refunded and reports uploaded'];
        for (var j = 1; j < btData.length; j++) {
          var br = btData[j];
          if (!br[BT.name] && !br[BT.orderId] && !br[BT.phone]) continue;
          var btPlan = (br[BT.plan] || '').toString().trim();
          var btDuration = parseInt(br[BT.duration]) || 0;
          var btProgStatus = (br[BT.progStatus] || '').toString().trim();
          var btStartRaw = (BT.startDate >= 0) ? br[BT.startDate] : null;
          var btEndRaw = (BT.endDate >= 0) ? br[BT.endDate] : null;
          var btPrevPlan = (BT.prevPlan >= 0) ? (br[BT.prevPlan] || '').toString().trim() : '';
          var btPrevBt2 = (BT.prevBt2Done >= 0) ? (br[BT.prevBt2Done] || '').toString().trim().toLowerCase() : '';
          var btBt1Stat = (BT.bt1Stat >= 0) ? (br[BT.bt1Stat] || '').toString().trim() : '';
          var btBt2Stat = (BT.bt2Stat >= 0) ? (br[BT.bt2Stat] || '').toString().trim() : '';
          var btProgLower = btProgStatus.toLowerCase();
          var excluded = false;
          for (var x = 0; x < BT_EXCLUDED.length; x++) { if (btProgLower === BT_EXCLUDED[x]) { excluded = true; break; } }
          if (excluded) continue;
          var bt1Eligible = false;
          if (btDuration >= 90) {
            var planLower = btPlan.toLowerCase();
            var isFresh = planLower === 'fresh' || planLower === 'fresh-b2b' || planLower === 'fresh-vip';
            var isRepeat = planLower === 'repeat';
            if (isFresh) { bt1Eligible = true; }
            else if (isRepeat) { var prevMatch = btPrevPlan.match(/(\d+)/); var prevDur = prevMatch ? parseInt(prevMatch[1]) : 0; if (prevDur < 90) { bt1Eligible = true; } else { bt1Eligible = (btPrevBt2 !== 'done'); } }
          }
          var bt1DueObj = toDateObj(btStartRaw);
          var bt1DueISO = bt1DueObj ? getISO(bt1DueObj) : '';
          var bt1DueFmt = bt1DueObj ? fmtDate(bt1DueObj) : '';
          var bt1IsDone = BT_DONE_STATUSES.some(function(s) { return btBt1Stat.toLowerCase() === s.toLowerCase(); });
          var bt2IsDone = BT_DONE_STATUSES.some(function(s) { return btBt2Stat.toLowerCase() === s.toLowerCase(); });
          var bt2Eligible = (btDuration >= 90);
          var bt2DueObj = toDateObj(btEndRaw);
          var bt2DueISO = bt2DueObj ? getISO(bt2DueObj) : '';
          var bt2DueFmt = bt2DueObj ? fmtDate(bt2DueObj) : '';
          bt_rows.push({
            month : (BT.month >= 0) ? (br[BT.month] || '').toString().trim() : '', plan : btPlan, order_id : (BT.orderId >= 0) ? (br[BT.orderId] || '').toString().trim() : '',
            name : (BT.name >= 0) ? (br[BT.name] || '').toString().trim() : '', phone : (BT.phone >= 0) ? (br[BT.phone] || '').toString().trim() : '',
            duration : btDuration, coach : (BT.coach >= 0) ? (br[BT.coach] || '').toString().trim() : '', prog_status : btProgStatus, prev_plan : btPrevPlan,
            prev_bt2_done: btPrevBt2 || 'not done', start_date : bt1DueFmt, end_date : fmtDate(btEndRaw), bt1_eligible : bt1Eligible, bt1_done : bt1IsDone,
            bt1_due : bt1DueFmt, bt1_due_iso : bt1DueISO, bt1_status : btBt1Stat, bt1_remark : (BT.bt1Remark >= 0) ? (br[BT.bt1Remark] || '').toString().trim() : '',
            bt1_lab : (BT.bt1Lab >= 0) ? (br[BT.bt1Lab] || '').toString().trim() : '', bt2_eligible : bt2Eligible, bt2_done : bt2IsDone, bt2_due : bt2DueFmt,
            bt2_due_iso : bt2DueISO, bt2_status : btBt2Stat, bt2_remark : (BT.bt2Remark >= 0) ? (br[BT.bt2Remark] || '').toString().trim() : '',
            bt2_lab : (BT.bt2Lab >= 0) ? (br[BT.bt2Lab] || '').toString().trim() : ''
          });
        }
      }
    }
    var months = Object.keys(mthSet).filter(function(m) { return /^[A-Za-z]{3} \d{4}$/.test(m); }).sort(function(a, b) {
      var ma = MON.indexOf(a.slice(0,3)), ya = parseInt(a.slice(4)); var mb = MON.indexOf(b.slice(0,3)), yb = parseInt(b.slice(4)); return (ya - yb) || (ma - mb); });
    return { rows: rows, bt_rows: bt_rows, months: months, col_map: CI };
  } catch(err) { return { error: err.toString() + ' | Stack: ' + err.stack }; }
}
process.stdout.write(JSON.stringify(getData()));

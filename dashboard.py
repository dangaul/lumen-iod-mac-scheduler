#!/usr/bin/env python3
"""Local web dashboard for Lumen scheduler status."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from collections import deque
from http.cookies import SimpleCookie
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import available_timezones

import lumen_scheduler as ls


HTML = """<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Lumen Scheduler Dashboard</title>
  <style>
    :root { --bg:#0f172a; --panel:#111827; --muted:#94a3b8; --text:#e5e7eb; --ok:#22c55e; --warn:#f59e0b; --bad:#ef4444; }
    body{margin:0;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto;background:linear-gradient(135deg,#0b1020,#111827);color:var(--text)}
    .wrap{max-width:1120px;margin:20px auto;padding:0 14px}
    .head{display:flex;justify-content:space-between;align-items:center;gap:10px}
    .brand{display:flex;align-items:center;gap:12px}
    .logo{height:34px;max-width:220px;object-fit:contain}
    .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin-top:12px}
    .card{background:rgba(17,24,39,.92);border:1px solid #1f2937;border-radius:12px;padding:12px}
    .card.live{grid-column:1 / -1;border-color:#1d4ed8;background:linear-gradient(180deg,rgba(17,24,39,.98),rgba(10,20,44,.98))}
    .label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
    .value{font-size:21px;font-weight:700;margin-top:3px;word-break:break-word}
    .small{font-size:12px;color:var(--muted)}
    pre{background:#020617;border:1px solid #1f2937;border-radius:10px;padding:10px;overflow:auto;max-height:260px}
    .ok{color:var(--ok)} .warn{color:var(--warn)} .bad{color:var(--bad)}
    .row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:10px}
    .actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
    button{border:1px solid #334155;background:#111827;color:#e5e7eb;border-radius:10px;padding:8px 12px;cursor:pointer}
    button.primary{background:#0b3b75;border-color:#1d4ed8}
    button.secondary{background:#4a1d1d;border-color:#991b1b}
    button.accent{background:#0b3b75;border-color:#1d4ed8}
    button.destructive{background:#4a1d1d;border-color:#dc2626}
    button:disabled{opacity:.45;cursor:not-allowed}
    input,select{background:#020617;color:#e5e7eb;border:1px solid #334155;border-radius:8px;padding:6px 8px}
    input{width:80px}
    input[type="checkbox"]{width:auto;margin:0}
    .check-label{display:flex;align-items:center;gap:6px}
    #cost_year{width:130px}
    .busy{color:#60a5fa}
    .activity{min-height:16px}
    .hidden{display:none}
    .nav{display:flex;gap:8px;justify-content:flex-end;margin-bottom:6px}
    .chart{width:100%;height:260px;border:1px solid #1f2937;border-radius:10px;background:#020617}
    .chart-note{margin-top:8px}
    .cost-table{max-height:300px}
    .rule-list{display:flex;flex-direction:column;gap:8px;margin-top:8px}
    .rule-row{border:1px solid #334155;border-radius:10px;padding:10px;background:#0b1220}
    .day-grid{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
    .day-chip{display:flex;align-items:center;gap:4px;border:1px solid #334155;border-radius:6px;padding:2px 6px}
    .login-overlay{position:fixed;inset:0;background:rgba(2,6,23,.9);display:flex;align-items:center;justify-content:center;z-index:9999}
    .login-box{width:min(420px,94vw);background:#0b1220;border:1px solid #334155;border-radius:12px;padding:16px}
    .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
    @media (max-width:900px){.row{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"head\">
	      <div class=\"brand\">
	        <img id=\"logo\" class=\"logo\" src=\"/logo\" alt=\"Company Logo\" onerror=\"this.style.display='none'\" />
	        <h2>Lumen Scheduler Dashboard</h2>
	      </div>
	      <div>
        <div class="nav">
          <button id="nav_dash" onclick="showView('dashboard')" disabled>Dashboard</button>
          <button id="nav_cost" onclick="showView('cost')">Cost Analytics</button>
          <button id="nav_cfg" onclick="showView('config')">Configuration</button>
        </div>
	        <div class=\"small\" id=\"ref\">refreshing...</div>
	        <div class=\"small activity busy\" id=\"activity\">Loading Live Status</div>
	      </div>
	    </div>

	    <div id="view_dashboard">
		    <div class=\"cards\">
		      <div class=\"card live\"><div class=\"label\">Live Lumen Status</div><div class=\"value\" id=\"live_status\">-</div><div class=\"small\" id=\"live_bw\"></div><div class=\"small\" id=\"live_hint\"></div><div class=\"small\" id=\"mtd_cost\" style=\"margin-top:8px\"></div></div>
		      <div class=\"card\"><div class=\"label\">Schedule Profile</div><div class=\"value\" id=\"profile\">-</div><div class=\"small\" id=\"rule\"></div></div>
		      <div class=\"card\"><div class=\"label\">Scheduled Bandwidth</div><div class=\"value\" id=\"sched_bw\">-</div><div class=\"small\">from local rules</div></div>
		      <div class=\"card\"><div class=\"label\">Scheduler Activity</div><div class=\"value\" id=\"last_run\">-</div><div class=\"small\" id=\"last_result\"></div><div class=\"small\" id=\"last_applied\"></div><div class=\"small\" id=\"cron\"></div></div>
		    </div>

      <div id="override_banner" class="card" style="margin-top:10px;display:none;border-color:#f59e0b;background:linear-gradient(180deg,rgba(71,35,8,.35),rgba(30,20,6,.25));">
        <div class="label">Override Active</div>
        <div class="value" id="override_banner_text" style="font-size:18px">-</div>
      </div>

      <div class=\"card\" style=\"margin-top:10px\">
        <div class=\"label\">Schedule</div>
        <pre id=\"schedule_readable\">loading...</pre>
      </div>

	    <div class=\"card\" style=\"margin-top:10px\">
	      <div class=\"label\">Quick Actions</div>
	      <div class=\"actions\" style=\"margin-top:8px\">
        <label class=\"small\">Override hours:</label>
        <input id=\"hours\" type=\"number\" min=\"0.25\" step=\"0.25\" value=\"1\" />
        <button id=\"btn_peak\" class=\"accent\" onclick=\"switchProfile('peak')\" disabled>Switch to On Peak (500 Mbps)</button>
        <button id=\"btn_off\" class=\"secondary\" onclick=\"switchProfile('off_peak')\" disabled>Switch to Off Peak (100 Mbps)</button>
        <button id=\"btn_clear\" onclick=\"clearOverride()\" disabled>Clear Override</button>
	      </div>
        <div class=\"actions\" style=\"margin-top:8px\">
          <label class=\"small\">Long Off-Peak Until (local):</label>
          <input id=\"off_until\" type=\"datetime-local\" style=\"width:220px\" />
          <button id=\"btn_off_until\" onclick=\"setOffPeakUntil()\" class=\"secondary\" disabled>Set Off-Peak Until</button>
        </div>
	      <div class=\"small\" id=\"action_msg\" style=\"margin-top:8px\"></div>
		      <div style=\"margin-top:10px\">
		        <button id=\"debug_toggle\" onclick=\"toggleDebug()\">Show Debug</button>
		      </div>
	    </div>

	    <div id=\"debug_panel\" style=\"display:none\">
      <div class=\"row\">
        <div class=\"card\">
          <div class=\"label\">Override</div>
          <pre id=\"override\">none</pre>
        </div>
        <div class=\"card\">
          <div class=\"label\">Last Error</div>
          <pre id=\"error\">none</pre>
        </div>
      </div>

      <div class=\"card\" style=\"margin-top:10px\">
        <div class=\"label\">Debug Actions</div>
        <div class=\"actions\" style=\"margin-top:8px\">
          <button id=\"btn_test\" onclick=\"testConnection()\">Test API Connection</button>
        </div>
      </div>

      <div class=\"card\" style=\"margin-top:10px\">
        <div class=\"label\">API Response Stream</div>
        <pre id=\"api_stream\">loading...</pre>
      </div>

	      <div class=\"card\" style=\"margin-top:10px\">
	        <div class=\"label\">Recent Logs</div>
	        <pre id=\"logs\">loading...</pre>
	      </div>
	    </div>
    </div>

    <div id="view_cost" class="hidden">
      <div class="card" style="margin-top:10px">
        <div class="label">Cost Analytics</div>
        <div class="actions" style="margin-top:8px">
          <label class="small">Year</label>
          <select id="cost_year"></select>
          <button id="btn_refresh_cost" class="primary" onclick="loadCostAnalytics(true)">Refresh Cost Data</button>
          <button id="btn_clear_cost_cache" class="destructive" onclick="clearCostCache()">Clear Cost Cache</button>
        </div>
        <div class="small chart-note" id="cost_meta">Loading cost analytics...</div>
      </div>

      <div class="card" style="margin-top:10px">
        <div class="label" id="cost_chart_title">Month-to-Month Cost</div>
        <svg id="cost_chart" class="chart" viewBox="0 0 900 260" preserveAspectRatio="none"></svg>
      </div>

      <div class="card" style="margin-top:10px">
        <div class="label">Monthly Cost Detail</div>
        <pre id="cost_rows" class="cost-table">loading...</pre>
      </div>
    </div>

    <div id="view_config" class="hidden">
      <div class="card" style="margin-top:10px">
        <div class="label">Configuration</div>
        <div class="row" style="margin-top:8px">
          <div><label class="small">Timezone</label><select id="cfg_timezone" style="width:100%"></select></div>
          <div><label class="small">Service ID</label><input id="cfg_service_id" type="text" style="width:100%" placeholder="77133831778" /></div>
          <div><label class="small">Log File</label><input id="cfg_log_file" type="text" style="width:100%" placeholder="./lumen-scheduler.log" /></div>
        </div>
        <div class="actions" style="margin-top:10px">
          <label class="small check-label"><input id="cfg_logging_enabled" type="checkbox" /> Logging Enabled</label>
          <label class="small check-label"><input id="cfg_sensitive_logs" type="checkbox" /> Include Sensitive Logs</label>
          <label class="small check-label"><input id="cfg_debug_enabled" type="checkbox" /> Show Debug Button</label>
          <button id="btn_save_config" class="primary" onclick="saveConfig()" disabled>Save Configuration</button>
        </div>
        <div class="small" id="cfg_msg" style="margin-top:8px"></div>
      </div>

      <div class="card" style="margin-top:10px">
        <div class="label">Schedule Rules</div>
        <div class="small">Rules are evaluated top-to-bottom. First matching rule wins. Fallback uses Default Profile.</div>
        <div class="actions" style="margin-top:8px">
          <label class="small">Default Profile</label>
          <select id="cfg_default_profile">
            <option value="off_peak">Off Peak</option>
            <option value="peak">On Peak</option>
          </select>
          <button id="btn_add_rule" onclick="addRuleRow()">Add Rule</button>
        </div>
        <div id="rule_list" class="rule-list"></div>
        <div class="actions" style="margin-top:8px">
          <button id="btn_save_rules" class="primary" onclick="saveConfig()" disabled>Save Schedule + Configuration</button>
        </div>
        <div class="small" id="rules_msg" style="margin-top:8px"></div>
      </div>

      <div class="card" style="margin-top:10px">
        <div class="label">Bandwidth Profiles</div>
        <div class="small">Current choices are loaded from config. Use Fetch to refresh from live API.</div>
        <div class="actions" style="margin-top:8px">
          <label class="small">On Peak:</label>
          <select id="peak_bw"></select>
          <label class="small">Off Peak:</label>
          <select id="off_bw"></select>
          <button id="btn_save_bw2" class="primary" onclick="saveBandwidthProfiles()" disabled>Save Bandwidth Profiles</button>
        </div>
        <div class="actions" style="margin-top:8px">
          <button id="btn_fetch_bw" class="accent" onclick="loadBandwidthOptions()">Fetch Bandwidth Profiles</button>
        </div>
        <div class="small" id="bw_msg" style="margin-top:8px"></div>
      </div>

      <div class="card" style="margin-top:10px">
        <div class="label">Cron Management</div>
        <div class="actions" style="margin-top:8px">
          <button id="btn_refresh_cron" class="accent" onclick="refreshCron()">Refresh Cron</button>
          <button id="btn_install_managed_cron" onclick="installManagedCron()">Install Managed Cron</button>
          <button id="btn_remove_managed_cron" class="destructive" onclick="removeManagedCron()">Remove Managed Cron</button>
        </div>
        <div id="cron_install_fields" class="actions" style="margin-top:8px">
          <label class="small">Interval (minutes)</label>
          <input id="cron_interval" type="number" min="1" max="60" step="1" value="5" />
          <label class="small">Python Bin</label>
          <input id="cron_python_bin" type="text" style="width:220px" value="/usr/bin/env python3" />
        </div>
        <div class="small" id="cron_msg" style="margin-top:8px"></div>
        <pre id="cron_jobs" style="margin-top:10px">loading...</pre>
      </div>
    </div>
	  </div>

  <script>
  window.__dash_preflight = "ok";
  </script>
  <script>
	  let pollTimer = null;
	  let burstUntil = 0;
	  let trackingChange = false;
	  let debugOpen = false;
	  let bandwidthOptions = [];
  let loadingCount = 0;
  let spinnerTimer = null;
  let spinnerIdx = 0;
  let activityStartTs = 0;
  let currentTimezone = 'America/Los_Angeles';
  let currentView = 'dashboard';
  let selectedCostYear = '';
  let lastBandwidthConfig = { peak: '', off_peak: '' };
  let configSnapshot = null;
  let bootstrapped = false;
  let startupRan = false;
  const spinnerFrames = ['|','/','-','\\\\'];
  const fallbackTimezoneOptions = [
    'America/Los_Angeles',
    'America/Denver',
    'America/Chicago',
    'America/New_York',
    'America/Phoenix',
    'America/Anchorage',
    'Pacific/Honolulu',
    'UTC'
  ];
  const dayOptions = ['mon','tue','wed','thu','fri','sat','sun'];
  function byId(id){ return document.getElementById(id); }
  function setDisabled(id, disabled){
    var el = byId(id);
    if(el){ el.disabled = disabled; }
  }
  window.addEventListener('error', function(ev){
    var act = byId('activity');
    if(act){
      act.textContent = 'UI error: ' + (ev && ev.message ? ev.message : 'Unknown script error');
      act.className = 'small activity bad';
    }
  });
  function fmt(v){ return v || '-'; }
  function currentYearNumber(){
    const d = new Date();
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: currentTimezone || 'America/Los_Angeles',
      year: 'numeric'
    }).formatToParts(d);
    const y = (parts.find(p => p.type === 'year') || {}).value || String(d.getFullYear());
    return Number(y);
  }
  function populateYearSelect(){
    const sel = byId('cost_year');
    if(!sel){ return; }
    const y = currentYearNumber();
    let html = '<option value=\"\">Last 12 Months</option>';
    for(let n = y; n >= (y - 10); n--){
      html += `<option value=\"${n}\">${n}</option>`;
    }
    sel.innerHTML = html;
    sel.value = selectedCostYear || '';
  }
  function escHtml(v){
    return String(v || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
  function ruleDaysHtml(idx, selected){
    const set = new Set((selected || []).map((d) => String(d).toLowerCase()));
    return dayOptions.map((d) => {
      const checked = set.has(d) ? 'checked' : '';
      return `<label class="day-chip"><input type="checkbox" data-rule-day="${idx}" value="${d}" ${checked}/> ${d.toUpperCase()}</label>`;
    }).join('');
  }
  function renderRuleList(rules){
    const list = byId('rule_list');
    if(!list){ return; }
    const items = Array.isArray(rules) ? rules : [];
    if(items.length === 0){
      list.innerHTML = '<div class="small">No rules configured. Add a rule or rely on default profile.</div>';
      return;
    }
    list.innerHTML = items.map((r, idx) => {
      const ranges = Array.isArray(r.time_ranges) && r.time_ranges.length ? r.time_ranges : [{start:'08:00', end:'17:00'}];
      const tr = ranges[0] || {start:'08:00', end:'17:00'};
      const profile = String(r.profile || 'peak');
      const name = String(r.name || `Rule ${idx + 1}`);
      return `
        <div class="rule-row" data-rule-row="${idx}">
          <div class="actions">
            <label class="small">Name</label>
            <input data-rule-name="${idx}" type="text" style="width:180px" value="${escHtml(name)}" />
            <label class="small">Profile</label>
            <select data-rule-profile="${idx}">
              <option value="peak" ${profile === 'peak' ? 'selected' : ''}>On Peak</option>
              <option value="off_peak" ${profile === 'off_peak' ? 'selected' : ''}>Off Peak</option>
            </select>
            <label class="small">Start (24h)</label>
            <input data-rule-start="${idx}" type="text" style="width:72px" value="${escHtml(String(tr.start || '08:00'))}" placeholder="08:00" />
            <label class="small">End (24h)</label>
            <input data-rule-end="${idx}" type="text" style="width:72px" value="${escHtml(String(tr.end || '17:00'))}" placeholder="17:00" />
            <button class="destructive" onclick="removeRuleRow(${idx})">Remove</button>
          </div>
          <div class="day-grid">${ruleDaysHtml(idx, Array.isArray(r.days) ? r.days : [])}</div>
        </div>
      `;
    }).join('');
  }
  function collectRulesFromEditor(){
    const rows = Array.from(document.querySelectorAll('[data-rule-row]'));
    const out = [];
    for(const row of rows){
      const idx = row.getAttribute('data-rule-row');
      const nameEl = row.querySelector(`[data-rule-name="${idx}"]`);
      const profileEl = row.querySelector(`[data-rule-profile="${idx}"]`);
      const startEl = row.querySelector(`[data-rule-start="${idx}"]`);
      const endEl = row.querySelector(`[data-rule-end="${idx}"]`);
      const dayEls = Array.from(row.querySelectorAll(`[data-rule-day="${idx}"]`));
      const days = dayEls.filter((d) => d.checked).map((d) => d.value);
      const rule = {
        name: (nameEl && nameEl.value ? nameEl.value.trim() : `Rule ${Number(idx) + 1}`),
        profile: (profileEl && profileEl.value) ? profileEl.value : 'peak',
        days: days,
        time_ranges: [{start: (startEl && startEl.value) ? startEl.value : '08:00', end: (endEl && endEl.value) ? endEl.value : '17:00'}]
      };
      out.push(rule);
    }
    return out;
  }
  function normalizeRules(rules){
    const list = Array.isArray(rules) ? rules : [];
    return list.map((r, idx) => {
      const rr = r || {};
      const tr = (Array.isArray(rr.time_ranges) && rr.time_ranges[0]) ? rr.time_ranges[0] : {};
      const days = Array.isArray(rr.days) ? rr.days.map((d) => String(d).toLowerCase()).filter((d) => dayOptions.includes(d)) : [];
      return {
        name: String(rr.name || `Rule ${idx + 1}`).trim(),
        profile: String(rr.profile || 'peak'),
        days: Array.from(new Set(days)),
        time_ranges: [{start: String(tr.start || '08:00'), end: String(tr.end || '17:00')}]
      };
    });
  }
  function getConfigDraft(){
    return {
      timezone: byId('cfg_timezone') ? String(byId('cfg_timezone').value || '').trim() : '',
      service_id: byId('cfg_service_id') ? String(byId('cfg_service_id').value || '').trim() : '',
      log_file: byId('cfg_log_file') ? String(byId('cfg_log_file').value || '').trim() : '',
      logging_enabled: byId('cfg_logging_enabled') ? Boolean(byId('cfg_logging_enabled').checked) : false,
      include_sensitive_logs: byId('cfg_sensitive_logs') ? Boolean(byId('cfg_sensitive_logs').checked) : false,
      debug_enabled: byId('cfg_debug_enabled') ? Boolean(byId('cfg_debug_enabled').checked) : false,
      default_profile: byId('cfg_default_profile') ? String(byId('cfg_default_profile').value || 'off_peak') : 'off_peak',
      rules: normalizeRules(collectRulesFromEditor())
    };
  }
  function sameJson(a,b){
    return JSON.stringify(a) === JSON.stringify(b);
  }
  function syncConfigButtons(){
    const draft = getConfigDraft();
    const snap = configSnapshot || draft;
    const rulesChanged = !sameJson(draft.rules, snap.rules || []);
    const configChanged = (
      draft.timezone !== (snap.timezone || '') ||
      draft.service_id !== (snap.service_id || '') ||
      draft.log_file !== (snap.log_file || '') ||
      draft.logging_enabled !== Boolean(snap.logging_enabled) ||
      draft.include_sensitive_logs !== Boolean(snap.include_sensitive_logs) ||
      draft.debug_enabled !== Boolean(snap.debug_enabled) ||
      draft.default_profile !== (snap.default_profile || 'off_peak') ||
      rulesChanged
    );
    const saveConfigBtn = byId('btn_save_config');
    if(saveConfigBtn){ saveConfigBtn.disabled = !configChanged; }
    const saveRulesBtn = byId('btn_save_rules');
    if(saveRulesBtn){
      const hasRules = draft.rules.length > 0;
      saveRulesBtn.disabled = !hasRules || !rulesChanged;
    }
  }
  function addRuleRow(){
    const existing = collectRulesFromEditor();
    existing.push({
      name: `Rule ${existing.length + 1}`,
      profile: 'peak',
      days: ['mon','tue','wed','thu','fri'],
      time_ranges: [{start:'08:00', end:'17:00'}]
    });
    renderRuleList(existing);
    syncConfigButtons();
  }
  function removeRuleRow(idx){
    const existing = collectRulesFromEditor().filter((_r, i) => i !== idx);
    renderRuleList(existing);
    syncConfigButtons();
  }
	  function bandwidthToMbps(v){
	    const raw = String(v || '').trim();
	    const m = raw.match(/(\\d+(?:\\.\\d+)?)\\s*(mbps|gbps)\\b/i);
	    if(!m){ return NaN; }
	    const n = Number(m[1]);
	    const u = m[2].toLowerCase();
	    return u === 'gbps' ? (n * 1000) : n;
	  }
  function fmtPST(v){
    if(!v){ return '-'; }
    const d = new Date(v);
    if(Number.isNaN(d.getTime())){ return v; }
    const formatter = new Intl.DateTimeFormat('en-US', {
      timeZone: currentTimezone || 'America/Los_Angeles',
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: true
    });
    const parts = formatter.formatToParts(d);
    const get = (t) => (parts.find(p => p.type === t) || {}).value || '';
    return `${get('month')} ${get('day')} ${get('hour')}:${get('minute')} ${get('dayPeriod')}`;
  }
  function friendlyProfile(v){
    const x = (v || '').toLowerCase();
    if(x === 'peak' || x === 'on_peak' || x === 'on-peak') return 'On Peak';
    if(x === 'off_peak' || x === 'off-peak') return 'Off Peak';
    return v || '-';
  }
  function toLiveProfile(v){
    const x = (v || '').toLowerCase();
    if(x === 'peak' || x === 'on_peak' || x === 'on-peak') return 'on_peak';
    if(x === 'off_peak' || x === 'off-peak') return 'off_peak';
    return x;
  }
  function colorResult(result){
    if(result === 'success') return 'ok';
    if(result === 'skip') return 'warn';
    if(result === 'error') return 'bad';
    return '';
  }
  function cronSummary(line){
    const raw = String(line || '').trim();
    if(!raw){ return 'cron: installed'; }
    const m = raw.match(/\*\/(\d+)\s+\*\s+\*\s+\*\s+\*/);
    if(m){ return `cron: installed (every ${m[1]} min)`; }
    const m2 = raw.match(/(\d+)\s+\*\s+\*\s+\*\s+\*/);
    if(m2){ return `cron: installed (minute ${m2[1]} hourly)`; }
    return 'cron: installed (custom schedule)';
  }
  function updateCronCard(available, hasAnyJobs, line){
    const cronEl = document.getElementById('cron');
    if(!cronEl){ return; }
    if(!available){
      cronEl.textContent = 'cron: unavailable';
      cronEl.title = '';
      return;
    }
    if(hasAnyJobs){
      cronEl.textContent = cronSummary(line || '');
      cronEl.title = String(line || '');
      return;
    }
    cronEl.textContent = 'cron: not installed';
    cronEl.title = '';
  }
  function schedulePoll(ms){
    if(pollTimer){ clearTimeout(pollTimer); }
    pollTimer = setTimeout(runPoll, ms);
  }
  function setIdleActivity(text){
    const el = document.getElementById('activity');
    if(loadingCount > 0){ return; }
    el.textContent = text || 'Idle. No active change pending.';
    el.className = 'small activity';
  }
  function toggleDebug(){
    debugOpen = !debugOpen;
    document.getElementById('debug_panel').style.display = debugOpen ? 'block' : 'none';
    document.getElementById('debug_toggle').textContent = debugOpen ? 'Hide Debug' : 'Show Debug';
    if(debugOpen){ refreshDebug(); }
  }
  function showView(which){
    const dash = document.getElementById('view_dashboard');
    const cost = document.getElementById('view_cost');
    const cfg = document.getElementById('view_config');
    const navDash = document.getElementById('nav_dash');
    const navCost = document.getElementById('nav_cost');
    const navCfg = document.getElementById('nav_cfg');
    const isDash = which === 'dashboard';
    const isCost = which === 'cost';
    const isCfg = which === 'config';
    currentView = which;
    dash.classList.toggle('hidden', !isDash);
    cost.classList.toggle('hidden', !isCost);
    cfg.classList.toggle('hidden', !isCfg);
    navDash.disabled = isDash;
    navCost.disabled = isCost;
    navCfg.disabled = isCfg;
    if(isCfg){
      loadConfigPage().catch((e) => {
        const el = document.getElementById('cfg_msg');
        el.textContent = `Failed loading configuration: ${e}`;
        el.className = 'small bad';
      });
      refreshCron().catch(() => {});
    }
    if(isCost){
      populateYearSelect();
      loadCostAnalytics(false).catch((e) => {
        const el = byId('cost_meta');
        el.textContent = `Failed loading cost analytics: ${e}`;
        el.className = 'small bad chart-note';
      });
    }
  }
  function setDebugEnabled(enabled){
    const btn = document.getElementById('debug_toggle');
    if(btn){
      btn.classList.toggle('hidden', !enabled);
      if(!enabled){
        debugOpen = false;
        const panel = document.getElementById('debug_panel');
        if(panel){ panel.style.display = 'none'; }
      }
    }
  }
  function initTimezoneSelect(selected, options){
    var sel = byId('cfg_timezone');
    if(!sel){ return; }
    var list = (options && options.length) ? options : fallbackTimezoneOptions;
    sel.innerHTML = list.map(function(t){ return '<option value=\"' + escHtml(t) + '\">' + escHtml(t) + '</option>'; }).join('');
    if(selected && list.indexOf(selected) === -1){
      sel.innerHTML += '<option value=\"' + escHtml(selected) + '\">' + escHtml(selected) + '</option>';
    }
    sel.value = selected || 'America/Los_Angeles';
  }
  async function refreshDebug(){
    if(!debugOpen){ return; }
    try{
      const r = await fetch('/api/events');
      const payload = await r.json();
      const events = payload.events || [];
      document.getElementById('api_stream').textContent = JSON.stringify(events, null, 2);
    } catch (e){
      document.getElementById('api_stream').textContent = 'Failed to load API stream: ' + e;
    }
  }
  function startActivity(text){
    const el = document.getElementById('activity');
    loadingCount += 1;
    el.dataset.base = text;
    if(activityStartTs === 0){
      activityStartTs = Date.now();
    }
    if(!spinnerTimer){
      spinnerTimer = setInterval(() => {
        spinnerIdx = (spinnerIdx + 1) % spinnerFrames.length;
        const base = el.dataset.base || 'Working';
        const sec = Math.max(0, Math.floor((Date.now() - activityStartTs) / 1000));
        el.textContent = base + ' ' + spinnerFrames[spinnerIdx] + ' ' + sec + 's';
      }, 120);
    }
    el.className = 'small activity busy';
    const sec = Math.max(0, Math.floor((Date.now() - activityStartTs) / 1000));
    el.textContent = text + ' ' + spinnerFrames[spinnerIdx] + ' ' + sec + 's';
  }
  function stopActivity(){
    const el = document.getElementById('activity');
    loadingCount = Math.max(0, loadingCount - 1);
    if(loadingCount === 0){
      if(spinnerTimer){
        clearInterval(spinnerTimer);
        spinnerTimer = null;
      }
      activityStartTs = 0;
      el.textContent = '';
      el.className = 'small activity';
    }
  }
  async function runPoll(){
    try{
      const d = await refresh(true);
      await refreshDebug();
      const pending = ((d && d.live_status ? d.live_status : '').toLowerCase() === 'change pending');
      trackingChange = Boolean(Date.now() < burstUntil || pending);
      if(trackingChange){
        const reason = pending ? 'Waiting for Lumen to finish applying change...' : 'Verifying recent action...';
        setIdleActivity(reason);
        if(currentView === 'cost'){
          loadCostAnalytics(false).catch(() => {});
        }
        schedulePoll(pending ? 3500 : 2500);
      } else {
        setIdleActivity('Idle. No active change pending.');
      }
    } catch (e){
      const act = document.getElementById('activity');
      act.textContent = 'Polling paused: ' + ((e && e.message) ? e.message : e);
      act.className = 'small activity warn';
    }
  }
  function startBurstPolling(seconds=120){
    burstUntil = Date.now() + (seconds * 1000);
    trackingChange = true;
    schedulePoll(1000);
  }
  function setBusy(isBusy, text='', keepTestEnabled=true){
    for(const id of ['btn_peak','btn_off','btn_clear','btn_off_until','btn_save_config','btn_fetch_bw','btn_save_bw2','btn_install_managed_cron','btn_remove_managed_cron','btn_refresh_cron','btn_refresh_cost','btn_clear_cost_cache','btn_add_rule','btn_save_rules']){
      const el = document.getElementById(id);
      if(el){ el.disabled = isBusy; }
    }
    const yearSel = byId('cost_year');
    if(yearSel){ yearSel.disabled = isBusy; }
    const testBtn = document.getElementById('btn_test');
    if(testBtn){ testBtn.disabled = isBusy && !keepTestEnabled; }
    const msg = document.getElementById('action_msg');
    if(text){
      msg.textContent = text;
      msg.className = 'small busy';
    }
  }
  function setActionMessage(text, isError=false){
    const msg = document.getElementById('action_msg');
    msg.textContent = text;
    msg.className = 'small ' + (isError ? 'bad' : 'ok');
  }
  function applyButtonAvailability(d){
    const live = (d.live_profile || '').toLowerCase();
    const base = (d.base_profile || '').toLowerCase();
    const expected = toLiveProfile(d.current_profile || '');
    const busy = (document.getElementById('action_msg').className || '').includes('busy');
    const hasLive = Boolean(d.live_status || d.live_bandwidth || d.live_profile);
    const hasOverride = Boolean(d.override);
    const liveScheduleMismatch = Boolean(hasLive && expected && live && expected !== live);
    const btnPeak = document.getElementById('btn_peak');
    const btnOff = document.getElementById('btn_off');
    const btnClear = document.getElementById('btn_clear');
    const btnOffUntil = document.getElementById('btn_off_until');
    const btnTest = document.getElementById('btn_test');
    if(liveScheduleMismatch){
      if(btnPeak){ btnPeak.disabled = true; }
      if(btnOff){ btnOff.disabled = true; }
      if(btnClear){ btnClear.disabled = busy || !hasLive; }
      if(btnOffUntil){ btnOffUntil.disabled = busy || !hasLive; }
      if(btnTest){ btnTest.disabled = false; }
      return;
    }
    if(btnPeak){
      btnPeak.disabled = busy || !hasLive || (live === 'on_peak') || (hasOverride && base === 'peak');
    }
    if(btnOff){
      btnOff.disabled = busy || !hasLive || (live === 'off_peak') || (hasOverride && base === 'off_peak');
    }
    if(btnClear){
      btnClear.disabled = busy || !hasLive || !hasOverride;
    }
    if(btnOffUntil){
      btnOffUntil.disabled = busy || !hasLive;
    }
	    if(btnTest){ btnTest.disabled = false; }
    const saveBw2 = document.getElementById('btn_save_bw2');
    const disableSave = busy || bandwidthOptions.length === 0 || !hasBandwidthChanges();
    if(saveBw2){ saveBw2.disabled = disableSave; }
  }
  function hasBandwidthChanges(){
    const peakSel = document.getElementById('peak_bw');
    const offSel = document.getElementById('off_bw');
    if(!peakSel || !offSel){ return false; }
    const peak = peakSel.value || '';
    const off = offSel.value || '';
    return peak !== (lastBandwidthConfig.peak || '') || off !== (lastBandwidthConfig.off_peak || '');
  }
  function syncBandwidthSaveButton(){
    const saveBw2 = document.getElementById('btn_save_bw2');
    const busy = (document.getElementById('action_msg').className || '').includes('busy');
    const disableSave = busy || bandwidthOptions.length === 0 || !hasBandwidthChanges();
    if(saveBw2){ saveBw2.disabled = disableSave; }
  }
  function renderBandwidthControls(payload){
	    const peakSel = document.getElementById('peak_bw');
	    const offSel = document.getElementById('off_bw');
	    bandwidthOptions = ((payload && payload.options) ? payload.options : []).slice();
	    const optionsHtml = bandwidthOptions.map(v => `<option value=\"${escHtml(v)}\">${escHtml(v)}</option>`).join('');
	    peakSel.innerHTML = optionsHtml;
	    offSel.innerHTML = optionsHtml;
    if(payload && payload.peak_bandwidth){ peakSel.value = payload.peak_bandwidth; }
    if(payload && payload.off_peak_bandwidth){ offSel.value = payload.off_peak_bandwidth; }
	    if(!peakSel.value && bandwidthOptions.length > 0){ peakSel.value = bandwidthOptions[0]; }
	    if(!offSel.value && bandwidthOptions.length > 0){ offSel.value = bandwidthOptions[0]; }
    lastBandwidthConfig = {
      peak: (payload && payload.peak_bandwidth) || peakSel.value || '',
      off_peak: (payload && payload.off_peak_bandwidth) || offSel.value || ''
    };
    const msg = document.getElementById('bw_msg');
    msg.textContent = (payload && payload.source_note) || '';
    msg.className = 'small';
    syncBandwidthSaveButton();
  }
  async function api(path, method='GET', body=null, loadingText='Working', silent=false){
    if(!silent){ startActivity(loadingText); }
    const opts = { method, headers: {'Content-Type':'application/json'}, credentials: 'same-origin' };
    if(body){ opts.body = JSON.stringify(body); }
    try{
      const r = await fetch(path, opts);
      let payload = {};
      try{
        payload = await r.json();
      } catch (_e){
        payload = {};
      }
      if(!r.ok){
        const err = new Error((payload && payload.message) || `HTTP ${r.status}`);
        err.status = r.status;
        err.payload = payload;
        throw err;
      }
      return payload;
    } catch (e){
      throw e;
    } finally {
      if(!silent){ stopActivity(); }
    }
  }
  function renderStatus(d){
    currentTimezone = d.timezone || currentTimezone;
    document.getElementById('ref').textContent = 'updated ' + fmtPST(new Date().toISOString()) + ' (' + currentTimezone + ')';
    document.getElementById('profile').textContent = friendlyProfile(d.current_profile);
    document.getElementById('rule').textContent = 'rule: ' + fmt(d.current_rule) + ' (base: ' + friendlyProfile(d.base_profile) + ')';
    document.getElementById('sched_bw').textContent = fmt(d.current_bandwidth);
    document.getElementById('live_status').textContent = fmt(d.live_status || d.live_error || '-');
    document.getElementById('live_bw').innerHTML = 'Current Bandwidth: <span class="ok">' + escHtml(fmt(d.live_bandwidth)) + '</span> (mapped: ' + escHtml(friendlyProfile(d.live_profile)) + ')';
    const hint = document.getElementById('live_hint');
    if((d.live_status || '').toLowerCase() === 'change pending'){
      hint.textContent = 'Lumen accepted the change and is still applying it. This can take several minutes.';
      hint.className = 'small warn';
    } else {
      hint.textContent = '';
      hint.className = 'small';
    }
    const banner = byId('override_banner');
    const bannerText = byId('override_banner_text');
    if(d.override && d.override.until_utc){
      const profile = friendlyProfile((d.override.profile || '').toLowerCase());
      const until = fmtPST(d.override.until_utc);
      banner.style.display = 'block';
      bannerText.textContent = `${profile} until ${until} (${currentTimezone})`;
    } else {
      banner.style.display = 'none';
      bannerText.textContent = '-';
    }
    document.getElementById('last_run').textContent = 'Last check: ' + fmtPST(d.last_run_at);
    const lastResult = document.getElementById('last_result');
    lastResult.textContent = 'result: ' + fmt(d.last_run_result);
    lastResult.className = 'small ' + colorResult(d.last_run_result);
    document.getElementById('last_applied').textContent = 'Last change applied: ' + fmtPST(d.last_applied_at);
    const cronEl = document.getElementById('cron');
    cronEl.textContent = d.cron_installed ? cronSummary(d.cron_line) : 'cron: not installed';
    cronEl.title = d.cron_installed ? String(d.cron_line || '') : '';
    if(d.month_to_date_cost && typeof d.month_to_date_cost === 'object'){
      var cost = d.month_to_date_cost || {};
      var total = (typeof cost.total_cost_usd === 'number') ? ('$' + cost.total_cost_usd.toFixed(2)) : 'n/a';
      var details = (cost.by_bandwidth || []).map(function(x){
        var c = (typeof x.cost_usd === 'number') ? ('$' + x.cost_usd.toFixed(2)) : 'n/a';
        var hasHours = (typeof x.hours === 'number' && x.hours > 0);
        return hasHours ? (x.bandwidth + ': ' + Number(x.hours).toFixed(1) + 'h, ' + c) : (x.bandwidth + ': ' + c);
      }).join(' | ');
      var source = cost.source ? (' [' + cost.source + ']') : '';
      var note = d.month_to_date_cost_note ? (' - ' + d.month_to_date_cost_note) : '';
      document.getElementById('mtd_cost').textContent = 'Month-to-date cost: ' + total + source + (details ? (' (' + details + ')') : '') + note;
    } else {
      document.getElementById('mtd_cost').textContent = d.month_to_date_cost_note || 'Month-to-date cost unavailable via API.';
    }
    document.getElementById('override').textContent = d.override ? JSON.stringify(d.override, null, 2) : 'none';
    document.getElementById('error').textContent = d.last_error || d.live_warning || d.live_error || 'none';
    document.getElementById('logs').textContent = d.log_tail || 'no logs yet';
    document.getElementById('schedule_readable').textContent = Array.isArray(d.schedule_lines) && d.schedule_lines.length
      ? d.schedule_lines.join('\\n')
      : 'No schedule rules configured.';
    applyButtonAvailability(d);
  }
  function renderCostAnalytics(payload){
    const meta = byId('cost_meta');
    const rows = byId('cost_rows');
    const chart = byId('cost_chart');
    const points = (payload && payload.monthly_series) ? payload.monthly_series : [];
    const total = (payload && typeof payload.series_total_usd === 'number')
      ? payload.series_total_usd
      : ((payload && typeof payload.total_cost_usd === 'number') ? payload.total_cost_usd : 0);
    const source = (payload && payload.source) ? payload.source : 'Customer Bill API';
    const note = (payload && payload.note) ? payload.note : '';
    const scopeLabel = (payload && payload.scope_label) ? payload.scope_label : 'Last 12 months';
    const pointsCount = points.length;
    byId('cost_chart_title').textContent = `Month-to-Month Cost (${scopeLabel})`;
    meta.textContent = `Source: ${source} | Scope total: $${total.toFixed(2)} | Points: ${pointsCount}${note ? (' | ' + note) : ''}`;
    meta.className = 'small chart-note';
    if(!points.length){
      chart.innerHTML = '<text x=\"20\" y=\"130\" fill=\"#94a3b8\" font-size=\"14\">No monthly data available.</text>';
      rows.textContent = 'No monthly rows returned.';
      return;
    }
    const maxCost = Math.max(0.01, ...points.map((d) => Number(d.total_cost_usd || 0)));
    const w = 900;
    const h = 260;
    const left = 40;
    const right = 16;
    const top = 14;
    const bottom = 30;
    const plotW = w - left - right;
    const plotH = h - top - bottom;
    const stepX = points.length > 1 ? (plotW / (points.length - 1)) : 1;
    let labels = '';
    let grid = '';
    let linePts = '';
    let dots = '';
    for(let i = 0; i <= 4; i++){
      const y = top + (plotH * i / 4);
      const v = maxCost * (1 - i / 4);
      grid += `<line x1=\"${left}\" y1=\"${y}\" x2=\"${w-right}\" y2=\"${y}\" stroke=\"#1f2937\" stroke-width=\"1\" />`;
      labels += `<text x=\"4\" y=\"${y+4}\" fill=\"#94a3b8\" font-size=\"10\">$${v.toFixed(2)}</text>`;
    }
    for(let i = 0; i < points.length; i++){
      const d = points[i];
      const v = Number(d.total_cost_usd || 0);
      const x = left + (i * stepX);
      const y = top + (plotH - ((v / maxCost) * plotH));
      linePts += `${x},${y} `;
      dots += `<circle cx=\"${x}\" cy=\"${y}\" r=\"3\" fill=\"#22c55e\"><title>${d.month}: $${v.toFixed(2)}</title></circle>`;
      if(i % 2 === 0 || i === points.length - 1){
        labels += `<text x=\"${x-12}\" y=\"${h-10}\" fill=\"#94a3b8\" font-size=\"10\">${(d.month || '').slice(2)}</text>`;
      }
    }
    const poly = `<polyline fill=\"none\" stroke=\"#22c55e\" stroke-width=\"2\" points=\"${linePts.trim()}\" />`;
    chart.innerHTML = `${grid}${poly}${dots}${labels}<line x1=\"${left}\" y1=\"${top+plotH}\" x2=\"${w-right}\" y2=\"${top+plotH}\" stroke=\"#64748b\" stroke-width=\"1\"/>`;
    rows.textContent = points.map((d) => `${d.month}  $${Number(d.total_cost_usd || 0).toFixed(2)}`).join('\\n');
  }
  async function loadCostAnalytics(forceFresh=false){
    const year = selectedCostYear || '';
    const qp = new URLSearchParams();
    if(forceFresh){ qp.set('fresh', '1'); }
    if(year){
      qp.set('year', year);
    } else {
      qp.set('months', '12');
    }
    const path = '/api/cost-analytics?' + qp.toString();
    try{
      const payload = await api(path, 'GET', null, 'Loading Cost Analytics');
      renderCostAnalytics(payload);
    } catch (e){
      const msg = (e && e.message) ? e.message : String(e);
      byId('cost_meta').textContent = 'Cost analytics unavailable: ' + msg;
      byId('cost_meta').className = 'small bad chart-note';
      byId('cost_chart').innerHTML = '<text x=\"20\" y=\"130\" fill=\"#ef4444\" font-size=\"14\">Cost analytics unavailable.</text>';
      byId('cost_rows').textContent = 'No cost rows.';
    }
  }
  async function clearCostCache(){
    try{
      const payload = await api('/api/cost-analytics/clear-cache', 'POST', {}, 'Clearing Cost Cache');
      const note = payload && payload.message ? payload.message : 'Cost cache cleared.';
      byId('cost_meta').textContent = note + ' Refreshing cost analytics...';
      byId('cost_meta').className = 'small chart-note';
      await loadCostAnalytics(true);
    } catch (e){
      const msg = (e && e.message) ? e.message : String(e);
      byId('cost_meta').textContent = 'Failed to clear cost cache: ' + msg;
      byId('cost_meta').className = 'small bad chart-note';
    }
  }
  // Safe default before first live status: only API test enabled.
	  setDisabled('btn_peak', true);
	  setDisabled('btn_off', true);
	  setDisabled('btn_clear', true);
	  setDisabled('btn_test', false);
	  setDisabled('btn_save_bw2', true);
  async function switchProfile(profile){
    try{
      setBusy(true, 'Applying override...', true);
      const hours = Number(document.getElementById('hours').value || '1');
      const actionLabel = profile === 'peak' ? 'Switching to On Peak' : 'Switching to Off Peak';
      trackingChange = true;
      const res = await api('/api/switch', 'POST', {profile, hours}, actionLabel);
      setActionMessage(res.message || 'Done', !res.ok);
      if(res.status){ renderStatus(res.status); }
      const confirm = await waitForLiveConfirmation(toLiveProfile(profile), 90, actionLabel + ' (waiting for Lumen confirmation)');
      if(confirm.ok){
        setActionMessage('Switch confirmed by live status.', false);
        await runPoll();
      } else {
        setActionMessage('Switch submitted. Live confirmation is still pending.', false);
        startBurstPolling(120);
      }
    } finally {
      setBusy(false, '', true);
    }
  }
  async function clearOverride(){
    try{
      setBusy(true, 'Clearing override...', true);
      trackingChange = true;
      const res = await api('/api/clear-override', 'POST', {}, 'Clearing Override');
      setActionMessage(res.message || 'Override cleared', !res.ok);
      if(res.status){ renderStatus(res.status); }
      const target = toLiveProfile(
        (res && res.status && res.status.base_profile) ||
        (res && res.status && res.status.current_profile) ||
        ''
      );
      if(target){
        const confirm = await waitForLiveConfirmation(target, 90, 'Clear override submitted (waiting for Lumen confirmation)');
        if(confirm.ok){
          setActionMessage('Clear override confirmed by live status.', false);
          await runPoll();
        } else {
          setActionMessage('Override cleared. Waiting on live confirmation.', false);
          startBurstPolling(120);
        }
      }
    } finally {
      setBusy(false, '', true);
    }
  }
  async function setOffPeakUntil(){
    const untilEl = byId('off_until');
    const untilLocal = untilEl ? String(untilEl.value || '').trim() : '';
    if(!untilLocal){
      setActionMessage('Select a date/time for Off-Peak override end.', true);
      return;
    }
    try{
      setBusy(true, 'Applying long Off-Peak override...', true);
      trackingChange = true;
      const res = await api('/api/override-off-peak-until', 'POST', {until_local: untilLocal}, 'Setting Off-Peak Until');
      setActionMessage(res.message || 'Long Off-Peak override set.', !res.ok);
      if(res.status){ renderStatus(res.status); }
      const confirm = await waitForLiveConfirmation('off_peak', 90, 'Long Off-Peak submitted (waiting for Lumen confirmation)');
      if(confirm.ok){
        setActionMessage('Long Off-Peak override confirmed by live status.', false);
        await runPoll();
      } else {
        setActionMessage('Long Off-Peak override set. Waiting on live confirmation.', false);
        startBurstPolling(120);
      }
    } finally {
      setBusy(false, '', true);
    }
  }
	  async function testConnection(){
	    try{
	      setBusy(true, 'Testing API connection...', false);
	      const res = await api('/api/test-connection', 'POST', {}, 'Testing API Connection');
	      setActionMessage(res.message || 'Connection test complete', !res.ok);
	      await runPoll();
	    } finally {
	      setBusy(false, '', false);
	    }
	  }
  async function loadBandwidthOptions(){
    const payload = await api('/api/bandwidth-options', 'GET', null, 'Fetching live bandwidth options');
    renderBandwidthControls(payload);
    var msg = byId('cfg_msg');
    if(msg){
      msg.textContent = 'Fetched live bandwidth options.';
      msg.className = 'small ok';
    }
  }
  async function saveBandwidthProfiles(){
    const peak = document.getElementById('peak_bw').value;
    const off = document.getElementById('off_bw').value;
	    const peakMbps = bandwidthToMbps(peak);
	    const offMbps = bandwidthToMbps(off);
	    const msg = document.getElementById('bw_msg');
	    if(Number.isNaN(peakMbps) || Number.isNaN(offMbps)){
	      msg.textContent = 'Invalid bandwidth selection.';
	      msg.className = 'small bad';
	      return;
	    }
    if(offMbps > peakMbps){
      msg.textContent = 'Off Peak cannot be higher than On Peak.';
      msg.className = 'small bad';
      return;
    }
    if(!hasBandwidthChanges()){
      msg.textContent = 'No profile changes to save.';
      msg.className = 'small';
      syncBandwidthSaveButton();
      return;
    }
    try{
      setBusy(true, 'Saving bandwidth profiles...', true);
	      const res = await api(
	        '/api/update-bandwidth-profiles',
	        'POST',
	        {peak_bandwidth: peak, off_peak_bandwidth: off},
	        'Saving bandwidth profiles'
      );
      msg.textContent = res.message || 'Bandwidth profiles saved.';
      msg.className = 'small ' + (res.ok ? 'ok' : 'bad');
      if(res.ok){
        lastBandwidthConfig = { peak, off_peak: off };
      }
      if(res.status){
        renderStatus(res.status);
      }
      const msg2 = document.getElementById('cfg_msg');
      if(msg2 && res.message){
        msg2.textContent = res.message;
        msg2.className = 'small ' + (res.ok ? 'ok' : 'bad');
      }
      syncBandwidthSaveButton();
    } finally {
      setBusy(false, '', true);
      syncBandwidthSaveButton();
    }
  }
  async function loadConfigPage(){
    const cfg = await api('/api/config', 'GET', null, 'Loading configuration');
    initTimezoneSelect(cfg.timezone || 'America/Los_Angeles', cfg.timezone_options || []);
    document.getElementById('cfg_service_id').value = cfg.service_id || '';
    document.getElementById('cfg_log_file').value = cfg.log_file || './lumen-scheduler.log';
    document.getElementById('cfg_logging_enabled').checked = Boolean(cfg.logging_enabled);
    document.getElementById('cfg_sensitive_logs').checked = Boolean(cfg.include_sensitive_logs);
    document.getElementById('cfg_debug_enabled').checked = Boolean(cfg.debug_enabled);
    document.getElementById('cfg_default_profile').value = cfg.default_profile || 'off_peak';
    renderRuleList(cfg.rules || []);
    renderBandwidthControls({
      options: cfg.bandwidth_options || [],
      peak_bandwidth: cfg.peak_bandwidth || '',
      off_peak_bandwidth: cfg.off_peak_bandwidth || '',
      source_note: 'Options loaded from config.'
    });
    const msg = document.getElementById('cfg_msg');
    msg.textContent = 'Configuration loaded.';
    msg.className = 'small ok';
    const rmsg = byId('rules_msg');
    if(rmsg){
      rmsg.textContent = `Loaded ${Array.isArray(cfg.rules) ? cfg.rules.length : 0} rule(s).`;
      rmsg.className = 'small ok';
    }
    configSnapshot = {
      timezone: cfg.timezone || 'America/Los_Angeles',
      service_id: cfg.service_id || '',
      log_file: cfg.log_file || './lumen-scheduler.log',
      logging_enabled: Boolean(cfg.logging_enabled),
      include_sensitive_logs: Boolean(cfg.include_sensitive_logs),
      debug_enabled: Boolean(cfg.debug_enabled),
      default_profile: cfg.default_profile || 'off_peak',
      rules: normalizeRules(cfg.rules || [])
    };
    syncConfigButtons();
    setDebugEnabled(Boolean(cfg.debug_enabled));
  }
  async function preloadBandwidthFromConfig(){
    try{
      const cfg = await api('/api/config', 'GET', null, 'Loading local bandwidth configuration');
      renderBandwidthControls({
        options: cfg.bandwidth_options || [],
        peak_bandwidth: cfg.peak_bandwidth || '',
        off_peak_bandwidth: cfg.off_peak_bandwidth || '',
        source_note: 'Options loaded from config.'
      });
    } catch (_e){
      // non-blocking for initial page load
    }
  }
  async function saveConfig(){
    try{
      const rules = collectRulesFromEditor();
      for(let i = 0; i < rules.length; i++){
        const r = rules[i];
        if(!Array.isArray(r.days) || r.days.length === 0){
          throw new Error(`Rule ${i + 1} must include at least one day.`);
        }
        const tr = (r.time_ranges && r.time_ranges[0]) ? r.time_ranges[0] : null;
        if(!tr || !tr.start || !tr.end){
          throw new Error(`Rule ${i + 1} must include start and end time.`);
        }
      }
      const payload = {
        timezone: document.getElementById('cfg_timezone').value.trim(),
        service_id: document.getElementById('cfg_service_id').value.trim(),
        log_file: document.getElementById('cfg_log_file').value.trim(),
        logging_enabled: document.getElementById('cfg_logging_enabled').checked,
        include_sensitive_logs: document.getElementById('cfg_sensitive_logs').checked,
        debug_enabled: document.getElementById('cfg_debug_enabled').checked,
        default_profile: document.getElementById('cfg_default_profile').value,
        rules: rules
      };
      const res = await api('/api/config', 'POST', payload, 'Saving configuration', true);
      const msg = document.getElementById('cfg_msg');
      msg.textContent = res.message || 'Configuration saved.';
      msg.className = 'small ' + (res.ok ? 'ok' : 'bad');
      const rmsg = byId('rules_msg');
      if(rmsg){
        rmsg.textContent = res.message || 'Rules saved.';
        rmsg.className = 'small ' + (res.ok ? 'ok' : 'bad');
      }
      configSnapshot = getConfigDraft();
      syncConfigButtons();
      setDebugEnabled(Boolean(payload.debug_enabled));
    } catch (e){
      const msg = document.getElementById('cfg_msg');
      msg.textContent = (e && e.message) || 'Failed to save configuration.';
      msg.className = 'small bad';
      const rmsg = byId('rules_msg');
      if(rmsg){
        rmsg.textContent = msg.textContent;
        rmsg.className = 'small bad';
      }
    }
  }
  function renderCronJobs(payload){
    const msg = document.getElementById('cron_msg');
    const out = document.getElementById('cron_jobs');
    const installBtn = byId('btn_install_managed_cron');
    const removeBtn = byId('btn_remove_managed_cron');
    const refreshBtn = byId('btn_refresh_cron');
    const installFields = byId('cron_install_fields');
    if(!payload.available){
      msg.textContent = 'crontab not available on this OS.';
      msg.className = 'small warn';
      out.textContent = 'No cron support detected.';
      if(installBtn){ installBtn.disabled = true; }
      if(removeBtn){ removeBtn.disabled = true; }
      if(refreshBtn){ refreshBtn.disabled = true; }
      if(installFields){ installFields.style.display = 'none'; }
      updateCronCard(false, false, '');
      return;
    }
    const jobs = payload.jobs || [];
    const hasAny = Boolean(payload.has_any_jobs);
    const hasManaged = Boolean(payload.has_managed_block);
    if(installBtn){
      installBtn.disabled = hasAny;
      installBtn.style.display = hasAny ? 'none' : 'inline-block';
    }
    if(removeBtn){ removeBtn.disabled = !hasAny; }
    if(refreshBtn){ refreshBtn.disabled = !hasAny; }
    if(installFields){ installFields.style.display = hasAny ? 'none' : 'flex'; }
    if(hasManaged){
      msg.textContent = 'Managed cron is installed.';
      msg.className = 'small ok';
      if(payload.managed_interval_minutes){
        byId('cron_interval').value = String(payload.managed_interval_minutes);
      }
    } else if(hasAny){
      msg.textContent = 'Existing cron entries detected. Managed install is disabled to avoid conflicts.';
      msg.className = 'small warn';
    } else {
      msg.textContent = 'No cron jobs found. Configure interval/python then install managed cron.';
      msg.className = 'small';
    }
    out.textContent = jobs.map((j) => `${j.disabled ? 'DISABLED ' : ''}${j.managed ? '[managed] ' : ''}${j.line}`).join('\\n') || 'No cron jobs.';
    const primaryJob = jobs.find((j) => !j.disabled) || jobs[0] || null;
    updateCronCard(true, hasAny, primaryJob ? primaryJob.line : '');
  }
  async function refreshCron(){
    try{
      const payload = await api('/api/cron', 'GET', null, 'Checking cron status');
      renderCronJobs(payload);
    } catch (e){
      const msg = document.getElementById('cron_msg');
      msg.textContent = (e && e.message) || 'Cron check failed.';
      msg.className = 'small bad';
    }
  }
  async function installManagedCron(){
    try{
      const interval = Number(document.getElementById('cron_interval').value || '5');
      const pythonBin = document.getElementById('cron_python_bin').value || '/usr/bin/env python3';
      const logFile = document.getElementById('cfg_log_file').value || './lumen-scheduler.log';
      await api('/api/cron/install-managed', 'POST', {interval_minutes: interval, python_bin: pythonBin, log_file: logFile}, 'Installing managed cron');
      await refreshCron();
    } catch (e){
      const msg = document.getElementById('cron_msg');
      msg.textContent = (e && e.message) || 'Failed to install managed cron.';
      msg.className = 'small bad';
    }
  }
  async function removeManagedCron(){
    try{
      await api('/api/cron/remove-managed', 'POST', {}, 'Removing managed cron');
      await refreshCron();
    } catch (e){
      const msg = document.getElementById('cron_msg');
      msg.textContent = (e && e.message) || 'Failed to remove managed cron.';
      msg.className = 'small bad';
    }
  }
  async function refresh(silent=false){
    const d = await api('/api/status?include_cost=0', 'GET', null, 'Loading Live Status', silent);
    renderStatus(d);
    return d;
  }
  async function refreshFresh(statusText='Checking live status...'){
    const d = await api('/api/status?fresh=1&include_cost=0', 'GET', null, statusText);
    renderStatus(d);
    return d;
  }
  async function waitForLiveConfirmation(targetLiveProfile, timeoutSec=90, statusText='Waiting for Lumen confirmation...'){
    const end = Date.now() + (timeoutSec * 1000);
    while(Date.now() < end){
      const d = await refreshFresh(statusText);
      const live = toLiveProfile(d.live_profile);
      const pending = ((d.live_status || '').toLowerCase() === 'change pending');
      if(!pending && live === targetLiveProfile){
        return {ok:true, status:d};
      }
      await new Promise(resolve => setTimeout(resolve, 3000));
    }
    return {ok:false, status:null};
  }
  async function bootstrapAfterAuth(){
	    if(!bootstrapped){
		      var peakEl = byId('peak_bw');
		      var offEl = byId('off_bw');
          var costYearEl = byId('cost_year');
          var cfgTz = byId('cfg_timezone');
          var cfgSvc = byId('cfg_service_id');
          var cfgLog = byId('cfg_log_file');
          var cfgLogEn = byId('cfg_logging_enabled');
          var cfgSens = byId('cfg_sensitive_logs');
          var cfgDbg = byId('cfg_debug_enabled');
          var cfgDefProf = byId('cfg_default_profile');
          var ruleList = byId('rule_list');
		      if(peakEl){ peakEl.addEventListener('change', syncBandwidthSaveButton); }
		      if(offEl){ offEl.addEventListener('change', syncBandwidthSaveButton); }
          if(costYearEl){
            populateYearSelect();
            costYearEl.addEventListener('change', function(){
              selectedCostYear = costYearEl.value || '';
              loadCostAnalytics(false);
            });
          }
          for(const el of [cfgTz,cfgSvc,cfgLog,cfgLogEn,cfgSens,cfgDbg,cfgDefProf]){
            if(el){ el.addEventListener('change', syncConfigButtons); }
            if(el){ el.addEventListener('input', syncConfigButtons); }
          }
          if(ruleList){
            ruleList.addEventListener('change', syncConfigButtons);
            ruleList.addEventListener('input', syncConfigButtons);
          }
		      bootstrapped = true;
		    }
	    setDisabled('btn_peak', true);
	    setDisabled('btn_off', true);
	    setDisabled('btn_clear', true);
      setDisabled('btn_off_until', true);
	    setDisabled('btn_test', false);
	    setDisabled('btn_save_bw2', true);
      const offUntil = byId('off_until');
      if(offUntil){
        const now = new Date(Date.now() + 24 * 60 * 60 * 1000);
        const pad = (n) => String(n).padStart(2, '0');
        offUntil.value = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
      }
	    await preloadBandwidthFromConfig();
	    const first = await api('/api/status?fast=1', 'GET', null, 'Loading Live Status');
	    renderStatus(first);
	    const pending = ((first.live_status || '').toLowerCase() === 'change pending');
	    trackingChange = pending;
	    if(pending){
	      setIdleActivity('Waiting for Lumen to finish applying change...');
	      startBurstPolling(120);
	    } else {
	      setIdleActivity('Idle. No active change pending.');
        // Fill in slower fields (cost + freshest live) after initial UI is ready.
        setTimeout(() => {
          refresh(true).catch(() => {});
        }, 50);
	    }
  }
  async function bootstrap(){
    if(startupRan){ return; }
    startupRan = true;
    try{
      setDebugEnabled(true);
      showView('dashboard');
      await bootstrapAfterAuth();
    } catch (e){
      const act = document.getElementById('activity');
      act.textContent = `Startup failed: ${e}`;
      act.className = 'small activity bad';
    }
  }
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', function(){ bootstrap(); });
  } else {
    bootstrap();
  }
	  </script>
</body>
</html>
"""

LAST_LIVE_OK: dict[str, Any] = {}
API_EVENTS: deque[dict[str, Any]] = deque(maxlen=200)
BANDWIDTH_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>mbps|gbps)\b", re.IGNORECASE)
SESSIONS: dict[str, float] = {}
SESSION_TTL_SECONDS = 8 * 60 * 60
DISABLED_PREFIX = "# DISABLED_BY_LUMEN_DASHBOARD "
PROFILE_LINE_RE = re.compile(r"time=(?P<time>\S+)\s+rule=.*?\sprofile=(?P<profile>[A-Za-z0-9_-]+)")
BILLING_CACHE: dict[str, Any] = {}
BILLING_TOKEN_CACHE: dict[str, Any] = {}
MONTHLY_SERIES_CACHE: dict[str, Any] = {}


def add_api_event(endpoint: str, ok: bool, details: dict[str, Any]) -> None:
    API_EVENTS.appendleft(
        {
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
            "endpoint": endpoint,
            "ok": ok,
            "details": details,
        }
    )


def tail_file(path: Path, lines: int = 120) -> str:
    if not path.exists():
        return ""
    dq: deque[str] = deque(maxlen=lines)
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            dq.append(line.rstrip("\n"))
    return "\n".join(dq)


def read_cron_block() -> tuple[bool, str]:
    if not cron_available():
        return False, ""
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return False, ""
    lines = proc.stdout.splitlines()
    in_block = False
    for line in lines:
        if line.strip() == ls.BEGIN_TAG:
            in_block = True
            continue
        if line.strip() == ls.END_TAG:
            break
        if in_block and line.strip():
            return True, line.strip()
    return False, ""


def get_live_inventory(config: dict[str, Any]) -> dict[str, Any]:
    iod_cfg = config.get("lumen_iod", {})
    if not iod_cfg:
        return {"live_error": "lumen_iod config is missing"}

    base_url = str(iod_cfg.get("base_url", "https://api.lumen.com")).rstrip("/")
    customer_number = str(iod_cfg.get("customer_number", "")).strip()
    service_id = str(iod_cfg.get("service_id", "")).strip()
    if not customer_number or not service_id:
        return {"live_error": "customer_number or service_id is missing"}

    timeout = int(iod_cfg.get("timeout_seconds", 20))
    auth_cfg = copy.deepcopy(iod_cfg.get("auth", {}))
    if not auth_cfg:
        return {"live_error": "auth config missing"}
    auth_cfg.setdefault("token_url", f"{base_url}/oauth/v2/token")

    try:
        token = ls.fetch_token(auth_cfg, timeout=timeout)
    except Exception as exc:
        return {"live_error": f"auth failed: {exc}"}

    headers = {
        "Authorization": f"Bearer {token}",
        "x-customer-number": customer_number,
        "Accept": "application/json",
    }
    inv_url = f"{base_url}/ProductInventory/v1/inventory?serviceId={ls.parse.quote(service_id)}"
    code, text = ls.json_request("GET", inv_url, timeout=timeout, headers=headers)
    if code < 200 or code > 299:
        detail = (text or "").strip().replace("\n", " ")
        if len(detail) > 400:
            detail = detail[:400] + "..."
        return {
            "live_error": f"inventory failed HTTP {code}",
            "live_http_code": code,
            "live_error_detail": detail,
        }

    try:
        payload = json.loads(text)
        items = payload.get("serviceInventory") or []
        if not items:
            return {"live_error": "inventory empty", "live_http_code": code}
        item = items[0]
        product = item.get("product", {})
        status = str(product.get("status", ""))
        bandwidth = ""
        for c in product.get("productCharacteristic", []):
            if str(c.get("name", "")).strip().lower() == "bandwidth":
                bandwidth = str(c.get("value", "")).strip()
                break

        live_profile = "unknown"
        profile_map = config.get("profiles", {})
        peak_bw = str(profile_map.get("peak", {}).get("bandwidth", "")).strip().lower()
        off_peak_bw = str(profile_map.get("off_peak", {}).get("bandwidth", "")).strip().lower()
        bw_norm = bandwidth.lower()
        if peak_bw and bw_norm == peak_bw:
            live_profile = "on_peak"
        elif off_peak_bw and bw_norm == off_peak_bw:
            live_profile = "off_peak"

        return {
            "live_http_code": code,
            "live_status": status,
            "live_bandwidth": bandwidth,
            "live_profile": live_profile,
        }
    except Exception as exc:
        return {
            "live_error": f"inventory parse failed: {exc}",
            "live_http_code": code,
            "live_error_detail": (text or "")[:400],
        }


def bandwidth_to_mbps(value: str) -> float:
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty bandwidth")
    match = BANDWIDTH_RE.search(text)
    if not match:
        raise ValueError(f"unsupported bandwidth format: {text}")
    num = float(match.group("num"))
    unit = match.group("unit").lower()
    return num * 1000.0 if unit == "gbps" else num


def normalize_bandwidth_label(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    low = text.lower()
    if low == "1gbps":
        return "1 Gbps"
    if low.endswith("mbps") or low.endswith("gbps"):
        match = BANDWIDTH_RE.search(text)
        if match:
            num = match.group("num")
            unit = match.group("unit").lower()
            suffix = "Gbps" if unit == "gbps" else "Mbps"
            if "." in num:
                num = str(float(num)).rstrip("0").rstrip(".")
            return f"{num} {suffix}"
    return text


def format_time_24_to_12(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    try:
        t = dt.time.fromisoformat(raw)
        return dt.datetime.combine(dt.date.today(), t).strftime("%-I:%M %p")
    except Exception:
        return raw


def day_label(day: str) -> str:
    m = {
        "mon": "Mon",
        "tue": "Tue",
        "wed": "Wed",
        "thu": "Thu",
        "fri": "Fri",
        "sat": "Sat",
        "sun": "Sun",
    }
    return m.get(str(day).strip().lower(), str(day))


def profile_friendly(name: str) -> str:
    x = str(name or "").strip().lower()
    if x in {"peak", "on_peak", "on-peak"}:
        return "On Peak"
    if x in {"off_peak", "off-peak"}:
        return "Off Peak"
    return str(name or "")


def schedule_summary_lines(config: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for rule in config.get("rules", []) or []:
        if not isinstance(rule, dict):
            continue
        days = [day_label(d) for d in (rule.get("days") or [])]
        day_text = ", ".join(days) if days else "Any day"
        ranges = rule.get("time_ranges") or []
        if ranges and isinstance(ranges[0], dict):
            start = format_time_24_to_12(str(ranges[0].get("start", "")))
            end = format_time_24_to_12(str(ranges[0].get("end", "")))
            time_text = f"{start} - {end}" if start and end else "All day"
        else:
            time_text = "All day"
        name = str(rule.get("name", "")).strip()
        profile = profile_friendly(str(rule.get("profile", "")))
        label = f"{name}: " if name else ""
        lines.append(f"{label}{day_text}, {time_text} -> {profile}")
    default_profile = profile_friendly(str(config.get("default_profile", "off_peak")))
    lines.append(f"Default (when no rule matches): {default_profile}")
    return lines


def get_available_bandwidth_options(config: dict[str, Any], include_live: bool = True) -> tuple[list[str], str]:
    options: list[str] = []
    source_note = "Options from config profiles."
    profiles = config.get("profiles", {})
    for name in ("peak", "off_peak"):
        bw = normalize_bandwidth_label(str(profiles.get(name, {}).get("bandwidth", "")))
        if bw:
            options.append(bw)

    extra = config.get("lumen_iod", {}).get("bandwidth_options", [])
    if isinstance(extra, list):
        for item in extra:
            bw = normalize_bandwidth_label(str(item))
            if bw:
                options.append(bw)
        if extra:
            source_note = "Options from lumen_iod.bandwidth_options and config profiles."

    if include_live:
        live = get_live_inventory(config)
        if not live.get("live_error"):
            bw = normalize_bandwidth_label(str(live.get("live_bandwidth", "")))
            if bw:
                options.append(bw)
                source_note = "Options from Lumen live inventory + config."

    seen: set[str] = set()
    uniq: list[str] = []
    for item in options:
        if item not in seen:
            seen.add(item)
            uniq.append(item)

    if not uniq:
        uniq = ["100 Mbps", "500 Mbps"]
        source_note = "Using safe defaults. Add lumen_iod.bandwidth_options in config for full customer-specific list."

    try:
        uniq.sort(key=bandwidth_to_mbps)
    except Exception:
        pass
    return uniq, source_note


def get_live_inventory_resilient(config: dict[str, Any], allow_cache: bool = True) -> dict[str, Any]:
    global LAST_LIVE_OK
    live = get_live_inventory(config)
    if not live.get("live_error"):
        LAST_LIVE_OK = dict(live)
        LAST_LIVE_OK["cached_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return live

    # Retry once for transient failures.
    time.sleep(0.35)
    retry = get_live_inventory(config)
    if not retry.get("live_error"):
        LAST_LIVE_OK = dict(retry)
        LAST_LIVE_OK["cached_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        return retry

    # If we have prior good data, keep showing it and expose warning.
    if allow_cache and LAST_LIVE_OK:
        merged = dict(LAST_LIVE_OK)
        merged["live_warning"] = (
            f"Latest live poll failed ({retry.get('live_error')}). "
            "Showing last known good live status."
        )
        merged["live_error"] = retry.get("live_error")
        merged["live_error_detail"] = retry.get("live_error_detail", "")
        merged["live_http_code"] = retry.get("live_http_code")
        return merged

    return retry


def parse_amount(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(str(value).strip())
    except Exception:
        return 0.0


def parse_any_date(value: str, tz: ls.ZoneInfo) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return dt.datetime.fromisoformat(raw).replace(tzinfo=tz)
        norm = raw.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(norm)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)
    except Exception:
        return None


def parse_any_date_to_local_midnight(value: str, tz: ls.ZoneInfo) -> dt.datetime | None:
    parsed = parse_any_date(value, tz)
    if parsed is None:
        return None
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def extract_bandwidth_from_text(text: str) -> str:
    raw = " ".join(str(text or "").split()).lower()
    if not raw:
        return "Unmapped"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(mbps|gbps)\b", raw, re.IGNORECASE)
    if m:
        num = m.group(1)
        unit = m.group(2).lower()
        suffix = "Gbps" if unit == "gbps" else "Mbps"
        if "." in num:
            num = str(float(num)).rstrip("0").rstrip(".")
        return f"{num} {suffix}"
    m2 = re.search(r"\b(\d+(?:\.\d+)?)\s*g\b", raw, re.IGNORECASE)
    if m2:
        num = m2.group(1)
        if "." in num:
            num = str(float(num)).rstrip("0").rstrip(".")
        return f"{num} Gbps"
    m3 = re.search(r"\b(\d+(?:\.\d+)?)\s*m\b", raw, re.IGNORECASE)
    if m3:
        num = m3.group(1)
        if "." in num:
            num = str(float(num)).rstrip("0").rstrip(".")
        return f"{num} Mbps"
    return "Unmapped"


def _billing_cache_key(config: dict[str, Any], month_start: str, month_end: str) -> str:
    iod = config.get("lumen_iod", {}) or {}
    billing_cfg = iod.get("customer_bill", {}) or {}
    base_url = str(billing_cfg.get("base_url") or iod.get("base_url") or "https://api.lumen.com").rstrip("/")
    billing_account = str(billing_cfg.get("billing_account_number") or iod.get("billing_account_id") or "").strip()
    service_id = str(iod.get("service_id", "")).strip()
    return "|".join([base_url, billing_account, service_id, month_start, month_end])


def fetch_oauth_token_cached(
    auth_cfg: dict[str, Any],
    timeout: int,
    cache_ns: str,
    ttl_seconds: int = 1500,
) -> str:
    key_raw = json.dumps(auth_cfg, sort_keys=True, default=str)
    key_hash = hashlib.sha256(key_raw.encode("utf-8")).hexdigest()
    key = f"{cache_ns}|{key_hash}"
    now_ts = time.time()
    cached = BILLING_TOKEN_CACHE.get(key)
    if cached and float(cached.get("expires_at", 0)) > (now_ts + 30):
        token = str(cached.get("token", "")).strip()
        if token:
            return token
    token = ls.fetch_token(auth_cfg, timeout=timeout)
    BILLING_TOKEN_CACHE[key] = {
        "token": token,
        "expires_at": now_ts + max(60, int(ttl_seconds)),
    }
    return token


def shift_year_month(year: int, month: int, delta_months: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta_months
    new_year = idx // 12
    new_month = (idx % 12) + 1
    return new_year, new_month


def build_monthly_cost_series(
    config: dict[str, Any],
    end_month_ym: str | None = None,
    months_count: int = 12,
    allow_cache: bool = True,
) -> list[dict[str, Any]]:
    tz_name = str(config.get("timezone") or "America/Los_Angeles")
    tz = ls.ZoneInfo(tz_name)
    now_local = dt.datetime.now(tz)
    end_raw = str(end_month_ym or "").strip()
    if end_raw:
        m = re.match(r"^(\d{4})-(\d{2})$", end_raw)
        if not m:
            raise ValueError("Invalid month format. Use YYYY-MM.")
        end_year = int(m.group(1))
        end_month = int(m.group(2))
    else:
        end_year = now_local.year
        end_month = now_local.month
    count = max(1, min(36, int(months_count)))

    # Monthly chart only needs month totals; skip service-level detail for speed/stability.
    temp_cfg = copy.deepcopy(config)
    iod = temp_cfg.setdefault("lumen_iod", {})
    cb = iod.setdefault("customer_bill", {})
    cb["use_service_level_breakdown"] = False

    # Cache the monthly chart payload to avoid recomputing 12+ month loops repeatedly.
    series_key = "|".join(
        [
            _billing_cache_key(temp_cfg, f"{end_year:04d}-{end_month:02d}-01", f"{end_year:04d}-{end_month:02d}-31"),
            f"months={count}",
            f"end={end_year:04d}-{end_month:02d}",
        ]
    )
    series_cache_seconds = int(cb.get("series_cache_seconds", min(int(cb.get("cache_seconds", 900)), 600)))
    now_ts = time.time()
    cached_series = MONTHLY_SERIES_CACHE.get(series_key)
    if allow_cache and cached_series and float(cached_series.get("expires_at", 0)) > now_ts:
        return list(cached_series.get("value") or [])

    points: list[dict[str, Any]] = []
    for rel in range(-(count - 1), 1):
        y, m = shift_year_month(end_year, end_month, rel)
        ym = f"{y:04d}-{m:02d}"
        summary, _note = fetch_month_to_date_cost_from_customer_bill(temp_cfg, allow_cache=allow_cache, month_ym=ym)
        total = 0.0
        if isinstance(summary, dict):
            total = float(summary.get("total_cost_usd") or 0.0)
        points.append({"month": ym, "total_cost_usd": round(total, 2)})
    MONTHLY_SERIES_CACHE[series_key] = {
        "expires_at": now_ts + max(30, series_cache_seconds),
        "value": points,
    }
    return points


def fetch_month_to_date_cost_from_customer_bill(
    config: dict[str, Any], allow_cache: bool = True, month_ym: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    iod = config.get("lumen_iod", {}) or {}
    billing_cfg = iod.get("customer_bill", {}) or {}
    enabled = bool(billing_cfg.get("enabled", True))
    if not enabled:
        return None, "Customer Bill API cost is disabled in config."

    tz_name = str(config.get("timezone") or "America/Los_Angeles")
    tz = ls.ZoneInfo(tz_name)
    now_local = dt.datetime.now(tz)
    selected_month = str(month_ym or "").strip()
    if selected_month:
        m = re.match(r"^(\d{4})-(\d{2})$", selected_month)
        if not m:
            return None, "Invalid month format. Use YYYY-MM."
        year = int(m.group(1))
        month = int(m.group(2))
        if month < 1 or month > 12:
            return None, "Invalid month. Use 01-12."
        period_start = dt.date(year, month, 1)
        if month == 12:
            period_end = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
        else:
            period_end = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
        current_month = (year == now_local.year and month == now_local.month)
        if current_month and period_end > now_local.date():
            period_end = now_local.date()
    else:
        period_start = now_local.date().replace(day=1)
        period_end = now_local.date()
        selected_month = f"{period_start.year:04d}-{period_start.month:02d}"
    month_start = period_start.isoformat()
    month_end = period_end.isoformat()

    cache_seconds = int(billing_cfg.get("cache_seconds", 900))
    cache_key = _billing_cache_key(config, month_start, month_end)
    cached = BILLING_CACHE.get(cache_key)
    now_ts = time.time()
    if allow_cache and cached and float(cached.get("expires_at", 0)) > now_ts:
        return cached.get("value"), cached.get("note")

    base_url = str(billing_cfg.get("base_url") or iod.get("base_url") or "https://api.lumen.com").rstrip("/")
    billing_account = str(billing_cfg.get("billing_account_number") or iod.get("billing_account_id") or "").strip()
    if not billing_account:
        return None, "Set lumen_iod.billing_account_id (or lumen_iod.customer_bill.billing_account_number) for Customer Bill API."

    timeout = int(billing_cfg.get("timeout_seconds", iod.get("timeout_seconds", 20)))
    auth_cfg = copy.deepcopy(iod.get("auth", {}) or {})
    auth_cfg.update(copy.deepcopy(billing_cfg.get("auth", {}) or {}))
    auth_cfg.setdefault("token_url", f"{base_url}/oauth/v2/token")

    try:
        token = fetch_oauth_token_cached(auth_cfg, timeout=timeout, cache_ns="customer_bill")
    except Exception as exc:
        return None, f"Customer Bill auth failed: {exc}"

    common_headers = {
        "Authorization": f"Bearer {token}",
        "x-billing-account-number": billing_account,
        "accept": "application/json;charset=utf-8",
    }
    limit = int(billing_cfg.get("limit", 25))
    offset = int(billing_cfg.get("offset", 0))

    # Endpoint is defined in the Customer Bill OpenAPI as requiring billingAccountNumber query param.
    bill_params = {
        "billingAccountNumber": billing_account,
        "startBillDate": month_start,
        "endBillDate": month_end,
        "offset": str(offset),
        "limit": str(limit),
    }
    bill_url = f"{base_url}/Billing/v2/CustomerBillManagement/customerBill?{urlencode(bill_params)}"
    bill_code, bill_text = ls.json_request("GET", bill_url, timeout=timeout, headers=common_headers)
    if bill_code < 200 or bill_code > 299:
        detail = (bill_text or "").strip().replace("\n", " ")
        if len(detail) > 260:
            detail = detail[:260] + "..."
        return None, f"Customer Bill API failed HTTP {bill_code}: {detail}"

    try:
        bill_payload = json.loads(bill_text)
    except Exception as exc:
        return None, f"Customer Bill parse failed: {exc}"

    wrappers = bill_payload if isinstance(bill_payload, list) else [bill_payload]
    invoice_numbers: list[str] = []
    invoice_total = 0.0
    currency = "USD"
    summary_ranges: list[tuple[dt.datetime | None, dt.datetime | None, float, str]] = []
    summary_by_statement: dict[str, float] = {}

    for wrapper in wrappers:
        details = wrapper.get("CustomerBillDetails") or wrapper.get("customerBillDetails") or []
        for item in details:
            invoice_no = str(item.get("invoiceNumber", "")).strip()
            if invoice_no:
                invoice_numbers.append(invoice_no)
            for summary in item.get("documentSummaryDetails", []) or []:
                charges = summary.get("billCharges", {}) or {}
                amount = charges.get("currentCharges")
                if amount is None:
                    amount = charges.get("totalAmountDue")
                amount_num = parse_amount(amount)
                invoice_total += amount_num
                if summary.get("currencyCode"):
                    currency = str(summary.get("currencyCode"))
                from_dt = parse_any_date_to_local_midnight(str(summary.get("fromDate", "")), tz)
                to_dt = parse_any_date_to_local_midnight(str(summary.get("toDate", "")), tz)
                statement_dt = parse_any_date_to_local_midnight(str(summary.get("statementDate", "")), tz)
                summary_ranges.append((from_dt, to_dt, amount_num, str(summary.get("statementDate", "")).strip()))
                if statement_dt is not None:
                    day_key = statement_dt.date().isoformat()
                    summary_by_statement[day_key] = summary_by_statement.get(day_key, 0.0) + amount_num

    service_id = str(iod.get("service_id", "")).strip()
    include_all_services = bool(billing_cfg.get("include_all_services", False))
    use_service_breakdown = bool(billing_cfg.get("use_service_level_breakdown", True))
    by_bw_cost: dict[str, float] = {}
    by_bw_hours: dict[str, float] = {}
    daily_cost: dict[str, float] = {}
    daily_by_bw: dict[str, dict[str, float]] = {}
    service_note = ""
    month_start_dt = dt.datetime.combine(period_start, dt.time.min, tzinfo=tz)
    month_end_exclusive_dt = dt.datetime.combine(period_end + dt.timedelta(days=1), dt.time.min, tzinfo=tz)

    # OpenAPI requires invoiceNumber for service level endpoint, so we request it per invoice.
    if use_service_breakdown and invoice_numbers:
        for inv in invoice_numbers:
            svc_params = {
                "billingAccountNumber": billing_account,
                "invoiceNumber": inv,
                "offset": str(offset),
                "limit": str(limit),
            }
            svc_url = f"{base_url}/Billing/v2/CustomerBillManagement/serviceLevelChargeDetails?{urlencode(svc_params)}"
            svc_code, svc_text = ls.json_request("GET", svc_url, timeout=timeout, headers=common_headers)
            if svc_code < 200 or svc_code > 299:
                service_note = f"Service-level breakdown unavailable (HTTP {svc_code})."
                continue
            try:
                svc_payload = json.loads(svc_text)
            except Exception:
                service_note = "Service-level breakdown parse failed."
                continue

            svc_wrappers = svc_payload if isinstance(svc_payload, list) else [svc_payload]
            for svc_wrapper in svc_wrappers:
                details = svc_wrapper.get("serviceLevelChargeDetail") or []
                for detail in details:
                    charges = detail.get("serviceLevelCharge") or []
                    for charge in charges:
                        billing_service_id = str(charge.get("billingServiceId") or "").strip()
                        if service_id and billing_service_id and (billing_service_id != service_id) and not include_all_services:
                            continue
                        text = " ".join(
                            [
                                str(charge.get("chargeDescription1", "")),
                                str(charge.get("chargeDescription2", "")),
                            ]
                        )
                        bw = extract_bandwidth_from_text(text)
                        amount_obj = charge.get("charge", {}) or {}
                        amount = amount_obj.get("netAmount")
                        if amount is None:
                            amount = amount_obj.get("chargeAmount")
                        amount_num = parse_amount(amount)
                        by_bw_cost[bw] = by_bw_cost.get(bw, 0.0) + amount_num

                        from_dt = parse_any_date(str(charge.get("chargeFromDate", "")), tz)
                        to_dt = parse_any_date(str(charge.get("chargeToDate", "")), tz)
                        if from_dt and to_dt and to_dt > from_dt:
                            total_seconds = max(0.0, (to_dt - from_dt).total_seconds())
                            by_bw_hours[bw] = by_bw_hours.get(bw, 0.0) + (total_seconds / 3600.0)
                            # Allocate charge across day buckets using overlap with each day in local timezone.
                            clip_start = max(from_dt, month_start_dt)
                            clip_end = min(to_dt, month_end_exclusive_dt)
                            if clip_end > clip_start and total_seconds > 0:
                                cursor = clip_start
                                while cursor < clip_end:
                                    day_start = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
                                    day_end = day_start + dt.timedelta(days=1)
                                    seg_end = min(day_end, clip_end)
                                    seg_seconds = max(0.0, (seg_end - cursor).total_seconds())
                                    if seg_seconds > 0:
                                        share = amount_num * (seg_seconds / total_seconds)
                                        day_key = day_start.date().isoformat()
                                        daily_cost[day_key] = daily_cost.get(day_key, 0.0) + share
                                        by_map = daily_by_bw.setdefault(day_key, {})
                                        by_map[bw] = by_map.get(bw, 0.0) + share
                                    cursor = seg_end

    if by_bw_cost:
        by_bandwidth = []
        for bw, cost in by_bw_cost.items():
            row: dict[str, Any] = {"bandwidth": bw, "cost_usd": round(cost, 2)}
            if bw in by_bw_hours and by_bw_hours[bw] > 0:
                row["hours"] = round(by_bw_hours[bw], 2)
            by_bandwidth.append(row)
        try:
            by_bandwidth.sort(key=lambda r: bandwidth_to_mbps(str(r.get("bandwidth", ""))))
        except Exception:
            by_bandwidth.sort(key=lambda r: str(r.get("bandwidth", "")))
    else:
        by_bandwidth = []

    summary = {
        "source": "Customer Bill API",
        "month_start": month_start,
        "month_end": month_end,
        "selected_month": selected_month,
        "currency": currency,
        "total_cost_usd": round(invoice_total, 2),
        "by_bandwidth": by_bandwidth,
        "daily_costs": [],
    }
    note: str | None = service_note or None
    if daily_cost:
        rows: list[dict[str, Any]] = []
        day = period_start
        while day <= period_end:
            key = day.isoformat()
            day_total = round(daily_cost.get(key, 0.0), 2)
            by_bw_rows: list[dict[str, Any]] = []
            day_bw = daily_by_bw.get(key, {})
            if day_bw:
                for bw, val in day_bw.items():
                    by_bw_rows.append({"bandwidth": bw, "cost_usd": round(val, 2)})
                try:
                    by_bw_rows.sort(key=lambda r: bandwidth_to_mbps(str(r.get("bandwidth", ""))))
                except Exception:
                    by_bw_rows.sort(key=lambda r: str(r.get("bandwidth", "")))
            rows.append({"date": key, "cost_usd": day_total, "by_bandwidth": by_bw_rows})
            day += dt.timedelta(days=1)
        summary["daily_costs"] = rows
    else:
        # Fallback: derive daily cost from invoice summary period range if available.
        fallback_daily: dict[str, float] = {}
        for from_dt, to_dt, amount_num, _statement_raw in summary_ranges:
            if amount_num <= 0:
                continue
            if from_dt is None and to_dt is None:
                continue
            if from_dt is None:
                from_dt = to_dt
            if to_dt is None:
                to_dt = from_dt
            if from_dt is None or to_dt is None:
                continue
            # treat toDate as inclusive at day granularity
            start = max(from_dt, month_start_dt)
            end = min(to_dt + dt.timedelta(days=1), month_end_exclusive_dt)
            if end <= start:
                continue
            total_seconds = (end - start).total_seconds()
            cursor = start
            while cursor < end:
                day_start = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + dt.timedelta(days=1)
                seg_end = min(day_end, end)
                seg_seconds = (seg_end - cursor).total_seconds()
                if seg_seconds > 0 and total_seconds > 0:
                    share = amount_num * (seg_seconds / total_seconds)
                    key = day_start.date().isoformat()
                    fallback_daily[key] = fallback_daily.get(key, 0.0) + share
                cursor = seg_end

        if not fallback_daily and summary_by_statement:
            fallback_daily = dict(summary_by_statement)

        if fallback_daily:
            rows = []
            day = period_start
            while day <= period_end:
                key = day.isoformat()
                rows.append({"date": key, "cost_usd": round(fallback_daily.get(key, 0.0), 2), "by_bandwidth": []})
                day += dt.timedelta(days=1)
            summary["daily_costs"] = rows
            if summary_by_statement and not any(v > 0 for v in fallback_daily.values()):
                note = note or "Using invoice statement dates for daily view."
            else:
                note = note or "Using prorated invoice summary date ranges for daily view."
        else:
            note = note or "Daily breakdown unavailable (service-level and invoice date ranges missing)."
    if not by_bandwidth:
        note = note or "No service-level bandwidth breakdown returned for this period."
    cache_item = {
        "expires_at": now_ts + cache_seconds,
        "value": summary,
        "note": note,
    }
    BILLING_CACHE[cache_key] = cache_item
    return summary, note


def collect_status(
    config_path: Path,
    log_path: Path,
    allow_cache: bool = True,
    include_live: bool = True,
    include_cost: bool = False,
) -> dict[str, Any]:
    now_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        config = ls.load_config(config_path)
        state_path = ls.state_path_from_config(config, config_path)
        state = ls.load_state(state_path)
        result = ls.profile_for_now(config, state)
        base_state = dict(state)
        base_state.pop("override", None)
        base_result = ls.evaluate_with_state(config, now_local=result.now_local, state=base_state)
        cron_installed, cron_line = read_cron_block()
        live: dict[str, Any]
        if include_live:
            live = get_live_inventory_resilient(config, allow_cache=allow_cache)
        else:
            live = {
                "live_status": "",
                "live_bandwidth": "",
                "live_profile": "",
                "live_warning": "Live status not requested for this refresh.",
            }
        mtd_cost: dict[str, Any] | None
        mtd_note: str | None
        if include_cost:
            mtd_cost, mtd_note = fetch_month_to_date_cost_from_customer_bill(config, allow_cache=allow_cache)
        else:
            mtd_cost, mtd_note = None, ""
        return {
            "now_utc": now_utc,
            "timezone": str(config.get("timezone") or "America/Los_Angeles"),
            "config_path": str(config_path),
            "state_path": str(state_path),
            "current_profile": result.profile_name,
            "current_rule": result.rule_name,
            "current_bandwidth": str(result.profile.get("bandwidth", "")),
            "base_profile": base_result.profile_name,
            "base_rule": base_result.rule_name,
            "last_profile": state.get("last_profile", ""),
            "last_rule": state.get("last_rule", ""),
            "last_applied_at": state.get("last_applied_at", ""),
            "last_run_at": state.get("last_run_at", ""),
            "last_run_result": state.get("last_run_result", ""),
            "last_error": state.get("last_error", ""),
            "override": state.get("override"),
            "schedule_lines": schedule_summary_lines(config),
            "cron_installed": cron_installed,
            "cron_line": cron_line,
            "log_tail": tail_file(log_path),
            "month_to_date_cost": mtd_cost,
            "month_to_date_cost_note": mtd_note or "",
            **live,
        }
    except Exception as exc:
        return {
            "now_utc": now_utc,
            "error": str(exc),
            "config_path": str(config_path),
            "log_tail": tail_file(log_path),
        }


def parse_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def load_raw_config(config_path: Path) -> dict[str, Any]:
    return ls.load_json(config_path)


def dashboard_settings(config_path: Path) -> dict[str, Any]:
    raw = load_raw_config(config_path)
    dashboard_cfg = raw.get("dashboard", {})
    passphrase_env = str(dashboard_cfg.get("passphrase_env", "DASHBOARD_PASSPHRASE")).strip() or "DASHBOARD_PASSPHRASE"
    auth_required = bool(dashboard_cfg.get("auth_required", True))
    debug_enabled = bool(dashboard_cfg.get("debug_enabled", True))
    ls.load_dotenv_near_config(config_path)
    passphrase = os.environ.get(passphrase_env, "")
    return {
        "auth_required": auth_required,
        "debug_enabled": debug_enabled,
        "passphrase_env": passphrase_env,
        "passphrase_ready": bool(passphrase),
    }


def apply_scheduler_logging(config_path: Path, fallback_log_path: Path) -> Path:
    resolved = ls.configure_logging_from_config(config_path, fallback_log_path)
    return resolved


def cron_available() -> bool:
    return shutil.which("crontab") is not None


def read_crontab_lines() -> list[str]:
    if not cron_available():
        return []
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return []
    return proc.stdout.splitlines()


def write_crontab_lines(lines: list[str]) -> None:
    if not cron_available():
        raise RuntimeError("crontab not available on this OS")
    if not lines:
        subprocess.run(["crontab", "-r"], check=False)
        return
    data = "\n".join(lines).rstrip() + "\n"
    subprocess.run(["crontab", "-"], input=data, text=True, check=True)


def list_cron_jobs() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    lines = read_crontab_lines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        disabled = stripped.startswith(DISABLED_PREFIX)
        value = stripped[len(DISABLED_PREFIX) :] if disabled else stripped
        managed = (ls.BEGIN_TAG in value) or (ls.END_TAG in value) or ("lumen_scheduler.py run" in value)
        jobs.append(
            {
                "index": idx,
                "line": value,
                "disabled": disabled,
                "managed": managed,
            }
        )
    return jobs


def detect_managed_interval(lines: list[str]) -> int | None:
    in_block = False
    for line in lines:
        s = line.strip()
        if s == ls.BEGIN_TAG:
            in_block = True
            continue
        if s == ls.END_TAG:
            in_block = False
            continue
        if in_block and s:
            m = re.match(r"\*/(\d+)\s+\*\s+\*\s+\*\s+\*", s)
            if m:
                try:
                    return int(m.group(1))
                except Exception:
                    return None
    return None


def compute_month_to_date_cost(config: dict[str, Any], log_path: Path) -> dict[str, Any]:
    tz_name = str(config.get("timezone") or "America/Los_Angeles")
    tz = ls.ZoneInfo(tz_name)
    now = dt.datetime.now(tz)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    profile_meta: dict[str, dict[str, Any]] = {}
    for profile_name, profile in (config.get("profiles", {}) or {}).items():
        bw = str(profile.get("bandwidth", "")).strip()
        rate = profile.get("rate_per_hour_usd")
        rate_num: float | None = None
        try:
            if rate is not None and str(rate).strip() != "":
                rate_num = float(rate)
        except Exception:
            rate_num = None
        profile_meta[str(profile_name)] = {"bandwidth": bw, "rate": rate_num}

    records: list[tuple[dt.datetime, str]] = []
    if log_path.exists():
        with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                m = PROFILE_LINE_RE.search(line)
                if not m:
                    continue
                raw_time = m.group("time")
                raw_profile = m.group("profile")
                try:
                    t = dt.datetime.fromisoformat(raw_time)
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=tz)
                    t = t.astimezone(tz)
                except Exception:
                    continue
                records.append((t, raw_profile))
    records.sort(key=lambda x: x[0])

    if not records:
        return {"month_start": month_start.isoformat(), "total_cost_usd": 0.0, "by_bandwidth": []}

    prev_profile: str | None = None
    prev_time = month_start
    for t, p in records:
        if t <= month_start:
            prev_profile = p
        else:
            break

    if prev_profile is None:
        for t, p in records:
            if t >= month_start:
                prev_profile = p
                prev_time = t
                break
    if prev_profile is None:
        return {"month_start": month_start.isoformat(), "total_cost_usd": 0.0, "by_bandwidth": []}

    by_bw: dict[str, dict[str, float | str]] = {}
    for t, p in records:
        if t < prev_time:
            continue
        if t > now:
            break
        if p != prev_profile and t > prev_time:
            hrs = (t - prev_time).total_seconds() / 3600.0
            meta = profile_meta.get(prev_profile, {})
            bw = str(meta.get("bandwidth") or prev_profile)
            rate = meta.get("rate")
            entry = by_bw.setdefault(bw, {"bandwidth": bw, "hours": 0.0, "cost_usd": 0.0})
            entry["hours"] = float(entry["hours"]) + hrs
            if isinstance(rate, (int, float)):
                entry["cost_usd"] = float(entry["cost_usd"]) + (hrs * float(rate))
            prev_profile = p
            prev_time = t
        elif p != prev_profile:
            prev_profile = p
            prev_time = t

    if now > prev_time and prev_profile:
        hrs = (now - prev_time).total_seconds() / 3600.0
        meta = profile_meta.get(prev_profile, {})
        bw = str(meta.get("bandwidth") or prev_profile)
        rate = meta.get("rate")
        entry = by_bw.setdefault(bw, {"bandwidth": bw, "hours": 0.0, "cost_usd": 0.0})
        entry["hours"] = float(entry["hours"]) + hrs
        if isinstance(rate, (int, float)):
            entry["cost_usd"] = float(entry["cost_usd"]) + (hrs * float(rate))

    rows = list(by_bw.values())
    rows.sort(key=lambda r: str(r.get("bandwidth", "")))
    total_cost = sum(float(r.get("cost_usd", 0.0)) for r in rows)
    return {
        "month_start": month_start.isoformat(),
        "total_cost_usd": round(total_cost, 2),
        "by_bandwidth": [
            {
                "bandwidth": str(r.get("bandwidth", "")),
                "hours": round(float(r.get("hours", 0.0)), 2),
                "cost_usd": round(float(r.get("cost_usd", 0.0)), 2),
            }
            for r in rows
        ],
    }


def find_logo_file(config_path: Path) -> Path | None:
    candidates = [
        "logo.png",
        "logo-white.png",
        "logo.svg",
        "logo.jpg",
        "logo.jpeg",
        "company-logo.png",
    ]
    roots = [config_path.parent, Path.cwd()]
    for root in roots:
        for name in candidates:
            p = (root / name).resolve()
            if p.exists() and p.is_file():
                return p
    return None


class Handler(BaseHTTPRequestHandler):
    config_path: Path
    log_path: Path

    def _write_json(
        self,
        payload: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _dashboard_settings(self) -> dict[str, Any]:
        return dashboard_settings(self.config_path)

    def _session_token(self) -> str:
        hdr = str(self.headers.get("X-Session-Token", "")).strip()
        if hdr:
            return hdr
        cookie_raw = self.headers.get("Cookie", "")
        if not cookie_raw:
            return ""
        cookie = SimpleCookie()
        cookie.load(cookie_raw)
        morsel = cookie.get("lumen_dash_session")
        if not morsel:
            return ""
        return str(morsel.value)

    def _is_authenticated(self) -> bool:
        return True

    def _auth_guard(self) -> bool:
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._auth_guard():
            return
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            body = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/logo":
            logo = find_logo_file(self.config_path)
            if not logo:
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()
                return
            raw = logo.read_bytes()
            suffix = logo.suffix.lower()
            content_type = "image/png"
            if suffix == ".svg":
                content_type = "image/svg+xml"
            elif suffix in {".jpg", ".jpeg"}:
                content_type = "image/jpeg"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if parsed.path == "/api/auth/status":
            settings = self._dashboard_settings()
            self._write_json(
                {
                    "ok": True,
                    "authenticated": True,
                    "auth_required": False,
                    "build": "noauth-ui-v1",
                    "passphrase_ready": settings["passphrase_ready"],
                    "debug_enabled": settings["debug_enabled"],
                    "passphrase_env": settings["passphrase_env"],
                    "session_token_seen": False,
                }
            )
            return

        if parsed.path == "/api/status":
            qs = parse_qs(parsed.query or "")
            allow_cache = not (qs.get("fresh", ["0"])[0] == "1")
            fast = qs.get("fast", ["0"])[0] == "1"
            include_live = qs.get("include_live", ["1"])[0] != "0"
            include_cost = qs.get("include_cost", ["0"])[0] != "0"
            if fast:
                # Fast mode prioritizes immediate UI response; cost can load in a follow-up call.
                allow_cache = True
                include_cost = False
            payload = collect_status(
                self.config_path,
                self.log_path,
                allow_cache=allow_cache,
                include_live=include_live,
                include_cost=include_cost,
            )
            add_api_event(
                "/api/status",
                ok=not bool(payload.get("live_error")),
                details={
                    "live_http_code": payload.get("live_http_code"),
                    "live_status": payload.get("live_status"),
                    "live_bandwidth": payload.get("live_bandwidth"),
                    "live_profile": payload.get("live_profile"),
                    "live_error": payload.get("live_error"),
                },
            )
            self._write_json(payload)
            return

        if parsed.path == "/api/cost-analytics":
            qs = parse_qs(parsed.query or "")
            allow_cache = not (qs.get("fresh", ["0"])[0] == "1")
            year_raw = str(qs.get("year", [""])[0]).strip()
            months_count = int(qs.get("months", ["12"])[0] or "12")
            config = ls.load_config(self.config_path)
            payload: dict[str, Any] = {
                "ok": True,
                "source": "Customer Bill API",
            }
            try:
                if year_raw:
                    if not re.match(r"^\d{4}$", year_raw):
                        raise ValueError("Invalid year format. Use YYYY.")
                    selected_year = int(year_raw)
                    payload["scope"] = "year"
                    payload["scope_label"] = f"{selected_year} (Jan-Dec)"
                    payload["selected_year"] = selected_year
                    payload["monthly_series"] = build_monthly_cost_series(
                        config,
                        end_month_ym=f"{selected_year:04d}-12",
                        months_count=12,
                        allow_cache=allow_cache,
                    )
                else:
                    payload["scope"] = "last_12_months"
                    payload["scope_label"] = "Last 12 Months"
                    payload["selected_year"] = None
                    payload["monthly_series"] = build_monthly_cost_series(
                        config,
                        end_month_ym=None,
                        months_count=months_count,
                        allow_cache=allow_cache,
                    )
            except Exception as exc:
                payload["monthly_series"] = []
                payload["note"] = str(exc)
            payload["series_total_usd"] = round(
                sum(float(x.get("total_cost_usd") or 0.0) for x in payload.get("monthly_series", [])),
                2,
            )
            if not payload.get("monthly_series"):
                payload["note"] = payload.get("note") or "Cost analytics unavailable."
            self._write_json(payload)
            return

        if parsed.path == "/api/events":
            self._write_json({"events": list(API_EVENTS)})
            return

        if parsed.path == "/api/bandwidth-options":
            try:
                config = ls.load_config(self.config_path)
                options, source_note = get_available_bandwidth_options(config, include_live=True)
                peak_bw = normalize_bandwidth_label(str(config.get("profiles", {}).get("peak", {}).get("bandwidth", "")))
                off_bw = normalize_bandwidth_label(str(config.get("profiles", {}).get("off_peak", {}).get("bandwidth", "")))
                payload = {
                    "options": options,
                    "peak_bandwidth": peak_bw,
                    "off_peak_bandwidth": off_bw,
                    "source_note": source_note,
                }
                add_api_event("/api/bandwidth-options", ok=True, details=payload)
                self._write_json(payload)
            except Exception as exc:
                add_api_event("/api/bandwidth-options", ok=False, details={"exception": str(exc)})
                self._write_json({"options": [], "source_note": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/config":
            raw = load_raw_config(self.config_path)
            settings = self._dashboard_settings()
            payload = {
                "timezone": raw.get("timezone", "America/Los_Angeles"),
                "default_profile": raw.get("default_profile", "off_peak"),
                "rules": raw.get("rules", []),
                "service_id": raw.get("lumen_iod", {}).get("service_id", ""),
                "timezone_options": sorted(available_timezones()),
                "log_file": raw.get("logging", {}).get("file", "./lumen-scheduler.log"),
                "logging_enabled": bool(raw.get("logging", {}).get("enabled", True)),
                "include_sensitive_logs": bool(raw.get("logging", {}).get("include_sensitive", False)),
                "debug_enabled": bool(raw.get("dashboard", {}).get("debug_enabled", True)),
                "auth_required": bool(raw.get("dashboard", {}).get("auth_required", True)),
                "passphrase_env": settings["passphrase_env"],
                "passphrase_ready": settings["passphrase_ready"],
                "bandwidth_options": raw.get("lumen_iod", {}).get("bandwidth_options", []),
                "peak_bandwidth": str(raw.get("profiles", {}).get("peak", {}).get("bandwidth", "")),
                "off_peak_bandwidth": str(raw.get("profiles", {}).get("off_peak", {}).get("bandwidth", "")),
            }
            self._write_json(payload)
            return

        if parsed.path == "/api/cron":
            available = cron_available()
            lines = read_crontab_lines() if available else []
            jobs = list_cron_jobs() if available else []
            managed_interval = detect_managed_interval(lines) if available else None
            payload = {
                "available": available,
                "jobs": jobs,
                "has_any_jobs": bool(jobs),
                "has_managed_block": bool(managed_interval),
                "managed_interval_minutes": managed_interval,
            }
            self._write_json(payload)
            return

        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if not self._auth_guard():
            return
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/auth/login":
                body = parse_json_body(self)
                submitted = str(body.get("passphrase", ""))
                settings = self._dashboard_settings()
                if not settings["auth_required"]:
                    self._write_json({"ok": True, "message": "Authentication disabled."})
                    return
                if not settings["passphrase_ready"]:
                    self._write_json(
                        {"ok": False, "message": f"Passphrase not configured in env var {settings['passphrase_env']}."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                expected = os.environ.get(settings["passphrase_env"], "")
                if not hmac.compare_digest(submitted, expected):
                    self._write_json({"ok": False, "message": "Invalid passphrase."}, status=HTTPStatus.UNAUTHORIZED)
                    return
                token = secrets.token_urlsafe(32)
                SESSIONS[token] = time.time() + SESSION_TTL_SECONDS
                cookie = f"lumen_dash_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL_SECONDS}"
                self._write_json(
                    {"ok": True, "message": "Authenticated.", "session_token": token},
                    extra_headers={"Set-Cookie": cookie},
                )
                return

            if parsed.path == "/api/auth/logout":
                token = self._session_token()
                if token:
                    SESSIONS.pop(token, None)
                cookie = "lumen_dash_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
                self._write_json({"ok": True, "message": "Logged out."}, extra_headers={"Set-Cookie": cookie})
                return

            if parsed.path == "/api/switch":
                body = parse_json_body(self)
                profile = str(body.get("profile", "peak"))
                hours = float(body.get("hours", 1))
                minutes = int(round(hours * 60))
                rc = ls.run_override(self.config_path, profile_name=profile, duration_minutes=minutes, dry_run=False)
                if rc == 0:
                    config = ls.load_config(self.config_path)
                    post_live = get_live_inventory_resilient(config, allow_cache=False)
                    if post_live.get("live_error"):
                        # Give backend a few seconds to settle and retry before returning.
                        for _ in range(3):
                            time.sleep(1.2)
                            post_live = get_live_inventory_resilient(config, allow_cache=False)
                            if not post_live.get("live_error"):
                                break
                    status_payload = collect_status(self.config_path, self.log_path, allow_cache=False)
                    add_api_event(
                        "/api/switch",
                        ok=True,
                        details={
                            "profile": profile,
                            "hours": hours,
                            "live_status": status_payload.get("live_status"),
                            "live_bandwidth": status_payload.get("live_bandwidth"),
                            "live_profile": status_payload.get("live_profile"),
                        },
                    )
                    self._write_json(
                        {
                            "ok": True,
                            "message": (
                                f"Switched to {profile} for "
                                f"{round(float((status_payload.get('override') or {}).get('effective_duration_minutes', minutes)) / 60.0, 2)} hour(s)."
                            ),
                            "status": status_payload,
                        }
                    )
                else:
                    add_api_event(
                        "/api/switch",
                        ok=False,
                        details={"profile": profile, "hours": hours, "message": "Switch failed"},
                    )
                    self._write_json({"ok": False, "message": "Switch failed. Check logs."}, status=HTTPStatus.BAD_REQUEST)
                return

            if parsed.path == "/api/override-off-peak-until":
                body = parse_json_body(self)
                until_local_raw = str(body.get("until_local", "")).strip()
                if not until_local_raw:
                    self._write_json(
                        {"ok": False, "message": "until_local is required (YYYY-MM-DDTHH:MM)."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                config = ls.load_config(self.config_path)
                tz_name = str(config.get("timezone") or "America/Los_Angeles")
                tz = ls.ZoneInfo(tz_name)
                try:
                    until_local = dt.datetime.fromisoformat(until_local_raw)
                    if until_local.tzinfo is None:
                        until_local = until_local.replace(tzinfo=tz)
                    else:
                        until_local = until_local.astimezone(tz)
                except Exception:
                    self._write_json(
                        {"ok": False, "message": "Invalid until_local format. Use YYYY-MM-DDTHH:MM."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return

                rc = ls.apply_override_until(
                    config_path=self.config_path,
                    profile_name="off_peak",
                    until_local=until_local,
                    dry_run=False,
                    clip_to_schedule=False,
                )
                if rc == 0:
                    status_payload = collect_status(self.config_path, self.log_path, allow_cache=False)
                    eff_min = int((status_payload.get("override") or {}).get("effective_duration_minutes", 0) or 0)
                    eff_hours = round(float(eff_min) / 60.0, 2) if eff_min > 0 else 0.0
                    add_api_event(
                        "/api/override-off-peak-until",
                        ok=True,
                        details={
                            "until_local": until_local.isoformat(),
                            "effective_hours": eff_hours,
                            "live_status": status_payload.get("live_status"),
                            "live_bandwidth": status_payload.get("live_bandwidth"),
                            "live_profile": status_payload.get("live_profile"),
                        },
                    )
                    self._write_json(
                        {
                            "ok": True,
                            "message": f"Off-Peak override set until {until_local.strftime('%Y-%m-%d %H:%M %Z')} ({eff_hours}h).",
                            "status": status_payload,
                        }
                    )
                else:
                    add_api_event(
                        "/api/override-off-peak-until",
                        ok=False,
                        details={"until_local": until_local_raw, "message": "Long Off-Peak override failed"},
                    )
                    self._write_json(
                        {"ok": False, "message": "Failed to set Off-Peak override until date/time."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                return

            if parsed.path == "/api/clear-override":
                rc = ls.clear_override(self.config_path)
                if rc == 0:
                    apply_rc = ls.run_once(self.config_path, force=True, dry_run=False)
                    status_payload = collect_status(self.config_path, self.log_path, allow_cache=False)
                    ok = apply_rc == 0
                    add_api_event(
                        "/api/clear-override",
                        ok=ok,
                        details={
                            "message": "Override cleared and schedule applied" if ok else "Override cleared but schedule apply failed",
                            "live_status": status_payload.get("live_status"),
                            "live_bandwidth": status_payload.get("live_bandwidth"),
                            "live_profile": status_payload.get("live_profile"),
                        },
                    )
                    self._write_json(
                        {
                            "ok": ok,
                            "message": "Override cleared and schedule applied." if ok else "Override cleared, but schedule apply failed.",
                            "status": status_payload,
                        },
                        status=HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST,
                    )
                else:
                    add_api_event("/api/clear-override", ok=False, details={"message": "Failed to clear override"})
                    self._write_json({"ok": False, "message": "Failed to clear override."}, status=HTTPStatus.BAD_REQUEST)
                return

            if parsed.path == "/api/test-connection":
                config = ls.load_config(self.config_path)
                live = get_live_inventory(config)
                if live.get("live_error"):
                    add_api_event("/api/test-connection", ok=False, details=live)
                    self._write_json(
                        {
                            "ok": False,
                            "message": f"API test failed: {live['live_error']}",
                            **live,
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return

                profile = live.get("live_profile", "unknown")
                add_api_event("/api/test-connection", ok=True, details=live)
                self._write_json(
                    {
                        "ok": True,
                        "message": (
                            "API test successful. "
                            f"Live status={live.get('live_status','')}, "
                            f"bandwidth={live.get('live_bandwidth','')}, "
                            f"profile={profile}."
                        ),
                        **live,
                    }
                )
                return

            if parsed.path == "/api/cost-analytics/clear-cache":
                cleared = {
                    "billing_cache_entries": len(BILLING_CACHE),
                    "token_cache_entries": len(BILLING_TOKEN_CACHE),
                    "monthly_series_entries": len(MONTHLY_SERIES_CACHE),
                }
                BILLING_CACHE.clear()
                BILLING_TOKEN_CACHE.clear()
                MONTHLY_SERIES_CACHE.clear()
                add_api_event("/api/cost-analytics/clear-cache", ok=True, details=cleared)
                self._write_json(
                    {
                        "ok": True,
                        "message": (
                            "Cost cache cleared "
                            f"(billing={cleared['billing_cache_entries']}, "
                            f"tokens={cleared['token_cache_entries']}, "
                            f"series={cleared['monthly_series_entries']})."
                        ),
                        "cleared": cleared,
                    }
                )
                return

            if parsed.path == "/api/update-bandwidth-profiles":
                body = parse_json_body(self)
                peak_bw = normalize_bandwidth_label(str(body.get("peak_bandwidth", "")).strip())
                off_bw = normalize_bandwidth_label(str(body.get("off_peak_bandwidth", "")).strip())
                if not peak_bw or not off_bw:
                    self._write_json({"ok": False, "message": "peak_bandwidth and off_peak_bandwidth are required."}, status=HTTPStatus.BAD_REQUEST)
                    return

                peak_mbps = bandwidth_to_mbps(peak_bw)
                off_mbps = bandwidth_to_mbps(off_bw)
                if off_mbps > peak_mbps:
                    self._write_json({"ok": False, "message": "Off Peak bandwidth cannot be higher than On Peak bandwidth."}, status=HTTPStatus.BAD_REQUEST)
                    return

                config = ls.load_config(self.config_path)
                allowed, _ = get_available_bandwidth_options(config, include_live=False)
                if peak_bw not in allowed or off_bw not in allowed:
                    self._write_json(
                        {
                            "ok": False,
                            "message": "Selected bandwidth is not in available Lumen options.",
                            "allowed": allowed,
                        },
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return

                raw_config = ls.load_json(self.config_path)
                raw_profiles = raw_config.setdefault("profiles", {})
                raw_peak = raw_profiles.setdefault("peak", {})
                raw_off = raw_profiles.setdefault("off_peak", {})
                old_peak_bw = normalize_bandwidth_label(str(raw_peak.get("bandwidth", "")))
                old_off_bw = normalize_bandwidth_label(str(raw_off.get("bandwidth", "")))
                peak_changed = old_peak_bw != peak_bw
                off_changed = old_off_bw != off_bw
                if not peak_changed and not off_changed:
                    self._write_json(
                        {
                            "ok": True,
                            "message": "No changes detected. Bandwidth profiles unchanged.",
                            "changed": False,
                            "applied_live_change": False,
                        }
                    )
                    return
                raw_peak["bandwidth"] = peak_bw
                raw_off["bandwidth"] = off_bw
                ls.save_json(self.config_path, raw_config)

                state_path = ls.state_path_from_config(config, self.config_path)
                state = ls.load_state(state_path)
                result = ls.profile_for_now(config, state)
                active_profile = str(result.profile_name)
                active_changed = (active_profile == "peak" and peak_changed) or (
                    active_profile == "off_peak" and off_changed
                )
                applied_live_change = False
                apply_ok = True
                apply_msg = "No immediate Lumen apply required."
                status_payload: dict[str, Any] | None = None
                if active_changed:
                    apply_rc = ls.run_once(self.config_path, force=True, dry_run=False)
                    applied_live_change = True
                    apply_ok = apply_rc == 0
                    if apply_ok:
                        apply_msg = "Config updated and active profile applied to Lumen."
                    else:
                        apply_msg = "Config updated, but active profile apply to Lumen failed."
                    status_payload = collect_status(self.config_path, self.log_path, allow_cache=False)

                add_api_event(
                    "/api/update-bandwidth-profiles",
                    ok=apply_ok,
                    details={
                        "peak_bandwidth": peak_bw,
                        "off_peak_bandwidth": off_bw,
                        "peak_changed": peak_changed,
                        "off_peak_changed": off_changed,
                        "active_profile": active_profile,
                        "applied_live_change": applied_live_change,
                        "apply_ok": apply_ok,
                    },
                )
                self._write_json(
                    {
                        "ok": apply_ok,
                        "message": (
                            f"Saved bandwidth profiles: On Peak={peak_bw}, Off Peak={off_bw}. "
                            f"{apply_msg}"
                        ),
                        "changed": True,
                        "applied_live_change": applied_live_change,
                        "active_profile": active_profile,
                        "status": status_payload,
                    }
                    ,
                    status=HTTPStatus.OK if apply_ok else HTTPStatus.BAD_REQUEST,
                )
                return

            if parsed.path == "/api/config":
                body = parse_json_body(self)
                raw = load_raw_config(self.config_path)
                raw["timezone"] = str(body.get("timezone", raw.get("timezone", "America/Los_Angeles"))).strip()
                default_profile = str(body.get("default_profile", raw.get("default_profile", "off_peak"))).strip()
                if default_profile not in {"peak", "off_peak"}:
                    raise ValueError("default_profile must be 'peak' or 'off_peak'")
                raw["default_profile"] = default_profile
                if "rules" in body:
                    input_rules = body.get("rules", [])
                    if not isinstance(input_rules, list):
                        raise ValueError("rules must be a list")
                    normalized_rules: list[dict[str, Any]] = []
                    for idx, rule in enumerate(input_rules, start=1):
                        if not isinstance(rule, dict):
                            raise ValueError(f"Rule {idx} must be an object")
                        profile = str(rule.get("profile", "")).strip()
                        if profile not in {"peak", "off_peak"}:
                            raise ValueError(f"Rule {idx}: profile must be 'peak' or 'off_peak'")
                        days = rule.get("days", [])
                        if not isinstance(days, list) or not days:
                            raise ValueError(f"Rule {idx}: days must be a non-empty list")
                        norm_days = []
                        for d in days:
                            day = str(d).strip().lower()
                            if day not in ls.DAY_MAP:
                                raise ValueError(f"Rule {idx}: invalid day '{d}'")
                            if day not in norm_days:
                                norm_days.append(day)
                        time_ranges = rule.get("time_ranges", [])
                        if not isinstance(time_ranges, list) or not time_ranges:
                            raise ValueError(f"Rule {idx}: time_ranges must be a non-empty list")
                        tr0 = time_ranges[0]
                        if not isinstance(tr0, dict):
                            raise ValueError(f"Rule {idx}: first time range must be an object")
                        start = str(tr0.get("start", "")).strip()
                        end = str(tr0.get("end", "")).strip()
                        if not start or not end:
                            raise ValueError(f"Rule {idx}: start and end are required")
                        # Validate HH:MM by reusing scheduler parser.
                        ls.normalize_time(start)
                        ls.normalize_time(end)
                        normalized_rules.append(
                            {
                                "name": str(rule.get("name", f"Rule {idx}")).strip() or f"Rule {idx}",
                                "profile": profile,
                                "days": norm_days,
                                "time_ranges": [{"start": start, "end": end}],
                            }
                        )
                    raw["rules"] = normalized_rules
                raw.setdefault("lumen_iod", {})
                raw["lumen_iod"]["service_id"] = str(body.get("service_id", raw["lumen_iod"].get("service_id", ""))).strip()
                raw.setdefault("logging", {})
                raw["logging"]["enabled"] = bool(body.get("logging_enabled", raw["logging"].get("enabled", True)))
                raw["logging"]["include_sensitive"] = bool(
                    body.get("include_sensitive_logs", raw["logging"].get("include_sensitive", False))
                )
                raw["logging"]["file"] = str(body.get("log_file", raw["logging"].get("file", "./lumen-scheduler.log"))).strip()
                raw.setdefault("dashboard", {})
                raw["dashboard"]["debug_enabled"] = bool(body.get("debug_enabled", raw["dashboard"].get("debug_enabled", True)))
                raw["dashboard"]["auth_required"] = bool(body.get("auth_required", raw["dashboard"].get("auth_required", True)))
                passphrase_env = str(body.get("passphrase_env", raw["dashboard"].get("passphrase_env", "DASHBOARD_PASSPHRASE"))).strip()
                raw["dashboard"]["passphrase_env"] = passphrase_env or "DASHBOARD_PASSPHRASE"
                ls.save_json(self.config_path, raw)
                Handler.log_path = apply_scheduler_logging(self.config_path, self.log_path)
                self._write_json({"ok": True, "message": "Configuration saved."})
                return

            if parsed.path == "/api/cron/install-managed":
                if not cron_available():
                    self._write_json({"ok": False, "message": "crontab is not available on this OS."}, status=HTTPStatus.BAD_REQUEST)
                    return
                existing_jobs = list_cron_jobs()
                if existing_jobs:
                    self._write_json(
                        {"ok": False, "message": "Existing cron entries detected. Remove them before installing managed cron."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                body = parse_json_body(self)
                interval = int(body.get("interval_minutes", 5))
                python_bin = str(body.get("python_bin", "/usr/bin/env python3"))
                log_file = Path(str(body.get("log_file", self.log_path)))
                script_path = (self.config_path.parent / "lumen_scheduler.py").resolve()
                ls.install_cron(script_path, self.config_path, interval, log_file.resolve(), python_bin)
                self._write_json({"ok": True, "message": "Managed cron installed."})
                return

            if parsed.path == "/api/cron/remove-managed":
                if not cron_available():
                    self._write_json({"ok": False, "message": "crontab is not available on this OS."}, status=HTTPStatus.BAD_REQUEST)
                    return
                ls.remove_cron()
                self._write_json({"ok": True, "message": "Managed cron removed."})
                return

            if parsed.path == "/api/cron/add":
                if not cron_available():
                    self._write_json({"ok": False, "message": "crontab is not available on this OS."}, status=HTTPStatus.BAD_REQUEST)
                    return
                existing_jobs = list_cron_jobs()
                if existing_jobs:
                    self._write_json(
                        {"ok": False, "message": "Existing cron entries detected. Remove them before adding a new line."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                body = parse_json_body(self)
                line = str(body.get("line", "")).strip()
                if not line:
                    self._write_json({"ok": False, "message": "Cron line is required."}, status=HTTPStatus.BAD_REQUEST)
                    return
                lines = read_crontab_lines()
                lines.append(line)
                write_crontab_lines(lines)
                self._write_json({"ok": True, "message": "Cron line added."})
                return

            if parsed.path in {"/api/cron/delete", "/api/cron/disable", "/api/cron/enable"}:
                if not cron_available():
                    self._write_json({"ok": False, "message": "crontab is not available on this OS."}, status=HTTPStatus.BAD_REQUEST)
                    return
                body = parse_json_body(self)
                idx = int(body.get("index", -1))
                lines = read_crontab_lines()
                if idx < 0 or idx >= len(lines):
                    self._write_json({"ok": False, "message": "Invalid cron index."}, status=HTTPStatus.BAD_REQUEST)
                    return
                if parsed.path == "/api/cron/delete":
                    lines.pop(idx)
                    write_crontab_lines(lines)
                    self._write_json({"ok": True, "message": "Cron line deleted."})
                    return
                if parsed.path == "/api/cron/disable":
                    if not lines[idx].strip().startswith(DISABLED_PREFIX):
                        lines[idx] = DISABLED_PREFIX + lines[idx]
                    write_crontab_lines(lines)
                    self._write_json({"ok": True, "message": "Cron line disabled."})
                    return
                if parsed.path == "/api/cron/enable":
                    if lines[idx].strip().startswith(DISABLED_PREFIX):
                        lines[idx] = lines[idx].replace(DISABLED_PREFIX, "", 1)
                    write_crontab_lines(lines)
                    self._write_json({"ok": True, "message": "Cron line enabled."})
                    return

            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()
        except Exception as exc:
            add_api_event(parsed.path, ok=False, details={"exception": str(exc)})
            self._write_json({"ok": False, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lumen scheduler dashboard")
    parser.add_argument("--config", default="./config.json", help="Scheduler config path")
    parser.add_argument("--log-file", default="./lumen-scheduler.log", help="Scheduler log path")
    parser.add_argument("--port", type=int, default=8787, help="Dashboard port")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = Path(args.config).expanduser().resolve()
    log_path = Path(args.log_file).expanduser().resolve()
    log_path = apply_scheduler_logging(config_path, log_path)

    Handler.config_path = config_path
    Handler.log_path = log_path

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Dashboard running: http://127.0.0.1:{args.port}")
    print(f"Config: {config_path}")
    print(f"Log: {log_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

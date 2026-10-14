"use strict";

const $ = (s) => document.querySelector(s);
const money = (v) => (v === null || v === undefined ? null : "¥" + Number(v).toFixed(2));
const BUFF_URL = (id) => `https://buff.163.com/market/s/${id}`;
const STEAM_URL = (hash) => `https://steamcommunity.com/market/listings/730/${encodeURIComponent(hash)}`;
function fmtTime(ts) { return ts ? new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "源站未提供"; }
function hhmmss(d) { return d.toTimeString().slice(0, 8); }
function esc(s) { return (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

// ---- 运行模式 ----
let MODE = "server";     // server | static
let ALL = [];            // static 全量
let META = {};           // static 元信息
let LAST_MOD = null;     // data.json 的 Last-Modified，用于条件请求避免重复下载
const state = { keyword: "", sort: "ratio", nextAt: 0, lastAt: null, timer: null };
const STATIC_LIMIT = 40; // 静态模式客户端最多渲染多少条(性能)

async function fetchJson(url, opt) {
  const r = await fetch(url, opt);
  const t = await r.text();
  return JSON.parse(t);
}

// 条件请求加载 data.json；Pages 返回 304 时不重复下载(10MB)。
async function loadData(force) {
  const headers = {};
  if (LAST_MOD && !force) headers["If-Modified-Since"] = LAST_MOD;
  const r = await fetch("data.json", { headers, cache: "no-cache" });
  if (r.status === 304) return false;          // 未更新
  const lm = r.headers.get("last-modified");
  if (lm) LAST_MOD = lm;
  META = await r.json();
  ALL = META.items || [];
  return true;
}

// ---- 启动：探测本地服务，否则加载静态 data.json ----
async function boot() {
  try {
    const s = await fetchJson("api/status");
    if (s && s.ok) { MODE = "server"; await loadStatus(); await runSearch(false); startLoop(); return; }
  } catch (e) { /* 静态部署：没有 /api */ }
  MODE = "static";
  await loadData(true);
  await loadStatus();
  await runSearch(false);   // 打开即按最佳比例降序列出榜单
  startLoop();
}

async function loadStatus() {
  if (MODE === "server") return loadStatusServer();
  const when = META.generated_at ? new Date(META.generated_at * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";
  $("#status").innerHTML =
    `静态快照 · ${ALL.length.toLocaleString()} 条 · 生成于 ${when} · Steam口径:${META.steam_mode === "proxy_or_runner" ? "国服(精确)" : "全球(估算)"}`;
  const banner = $("#banner");
  if (META.steam_mode !== "proxy_or_runner") {
    banner.hidden = false; banner.className = "banner warnbox";
    banner.innerHTML = "当前为<b>全球估算(偏乐观)</b>、每日快照，已按最低价去噪。要精确国服价与 24h 成交量过滤，" +
      "需构建时能连 Steam 的境外 runner——部署到 GitHub Actions 后自动填充，榜上件会标『精确』。";
  } else {
    banner.hidden = false; banner.className = "banner okbox";
    banner.textContent = "本次快照含关注列表的国服精确价；其余为全量估算。";
  }
}
async function loadStatusServer() {
  const s = await fetchJson("api/status");
  const map = { idle: "待命", downloading: "获取ID映射", importing: "导入索引", ready: "就绪", error: "导入失败" };
  const last = s.catalog_imported_at ? fmtTime(s.catalog_imported_at) : "尚未导入";
  $("#status").innerHTML =
    `目录(ID-Mapper):${map[s.catalog_state] || s.catalog_state} · 已索引 ${s.indexed_items} 条 · 导入 ${last} · Steam:${s.steam_mode === "proxy" ? "国服(代理)" : s.steam_mode === "global" ? "全球估" : "缺省"}`;
  const banner = $("#banner");
  if (s.catalog_state === "error") { banner.hidden = false; banner.className = "banner errbox"; banner.textContent = s.catalog_message || "ID-Mapper 导入失败。"; }
  else if (s.catalog_state !== "ready") { banner.hidden = false; banner.className = "banner warnbox"; banner.textContent = "正在初始化目录索引与聚合快照，首次下载稍候。"; }
  else if (s.steam_mode === "proxy") { banner.hidden = true; }
  else if (s.steam_mode === "global") { banner.hidden = false; banner.className = "banner warnbox"; banner.innerHTML = "<b>比例为全球估算(偏高)</b>，仅供排序参考；连接 Steam 出口后自动切换国服精确值。"; }
  else { banner.hidden = false; banner.className = "banner warnbox"; banner.textContent = "聚合快照未就绪，比例暂缺；BUFF 实时价仍可用。"; }
}

// ---- 单元格 ----
function steamCell(it) { if (it.steam_ref_cny == null) return `<span class="none">待Steam价</span>`; const t = it.steam_mode === "global" ? ` <span class="gtag">全球估</span>` : ` <span class="gtag okg">精确</span>`; return money(it.steam_ref_cny) + t; }
function netCell(it) { return it.steam_ref_cny == null ? `<span class="none">待Steam价</span>` : priceCell(it.steam_net_est); }
function priceCell(v) { return v == null ? `<span class="none">暂无报价</span>` : money(v); }
function ratioCell(it) { if (it.ratio_pct == null) return `<span class="none">待Steam价</span>`; return `<span class="ratio ${it.steam_mode === "global" ? "approx" : ""}">${it.ratio_pct.toFixed(1)}%</span>`; }

function renderRows(items) {
  $("#rows").innerHTML = items.map((it) => {
    const name = it.name_cn || it.market_hash_name;
    const hash = it.name_cn ? it.market_hash_name : "";
    const ts = it.source_updated_at ? ` <span class="tstamp">· ${fmtTime(it.source_updated_at)}</span>` : "";
    const buff = it.buff_status === "ok" || it.buff_min_sell != null ? priceCell(it.buff_min_sell) : `<span class="none">获取失败</span>`;
    return `<tr>
      <td class="item"><div class="cn">${esc(name)}</div><div class="hash">${esc(hash)}${ts}</div></td>
      <td>${buff}</td><td>${it.sell_num ?? `<span class="none">--</span>`}</td>
      <td>${it.volume != null ? Number(it.volume).toLocaleString() : `<span class="none">—</span>`}</td>
      <td><span class="none">暂无报价</span></td>
      <td>${steamCell(it)}</td><td>${netCell(it)}</td><td>${ratioCell(it)}</td>
      <td class="links"><a href="${it.buff_url || BUFF_URL(it.goods_id)}" target="_blank" rel="noopener">BUFF</a>
        <a href="${it.steam_url || STEAM_URL(it.market_hash_name)}" target="_blank" rel="noopener">Steam</a></td>
    </tr>`;
  }).join("");
}

function minVol() { return +($("#minvol").value || 0); }

function staticSearch(q, sort, scope) {
  const mv = minVol(), kw = q.toLowerCase(), box = scope === "box";
  let rows;
  const hasAnyVol = ALL.some((it) => it.volume != null);
  if (!q) {
    if (hasAnyVol) {
      // 有成交量数据(部署在能连 Steam 的 runner)：精确榜,按阈值过滤
      rows = ALL.filter((it) => (!box || it.cat === "box") && it.volume != null && it.volume >= mv);
    } else {
      // 无成交量数据：回落"最低价去噪"的全球估榜,保证有内容
      rows = ALL.filter((it) => (!box || it.cat === "box") && (it.buff_min_sell || 0) >= 3);
    }
  } else {
    rows = ALL.filter((it) => (it.market_hash_name || "").toLowerCase().includes(kw)
      || (it.name_cn || "").toLowerCase().includes(kw));
    if (box) rows = rows.filter((it) => it.cat === "box");
    rows = rows.filter((it) => it.volume == null || it.volume >= mv); // 未知成交量的仍保留
  }
  if (sort === "min_sell") rows.sort((a, b) => (a.buff_min_sell == null) - (b.buff_min_sell == null) || (a.buff_min_sell || 0) - (b.buff_min_sell || 0));
  else rows.sort((a, b) => (a.ratio_pct == null) - (b.ratio_pct == null) || (b.ratio_pct || 0) - (a.ratio_pct || 0));
  return rows.slice(0, STATIC_LIMIT);
}

async function runSearch(silent) {
  const q = $("#q").value.trim();
  const scope = $("#scope").value;
  state.keyword = q; state.sort = $("#sort").value;
  if (!silent) $("#rows").innerHTML = `<tr class="empty"><td colspan="9">${q ? "搜索中…" : (scope === "box" ? "载入箱子榜…" : "载入最佳榜…")}</td></tr>`;
  let items;
  if (MODE === "server") {
    const url = q ? `/api/search?q=${encodeURIComponent(q)}&sort=${state.sort}` : `/api/top?limit=40&scope=${scope}`;
    const res = await fetchJson(url);
    if (!res.ok) { if (!silent) $("#rows").innerHTML = `<tr class="empty"><td colspan="9">出错：${esc(res.reason)}</td></tr>`; return; }
    items = res.items;
    if (q && scope === "box") items = items.filter((it) => it.cat === "box");
  } else {
    items = staticSearch(q, state.sort, scope);
  }
  if (!items.length) {
    const msg = (!q && MODE === "static")
      ? "默认榜需“已知成交量≥阈值”的件——本机无 Steam 出口时为空，部署到境外 runner 后自动填充"
      : "无匹配结果";
    $("#rows").innerHTML = `<tr class="empty"><td colspan="9">${msg}</td></tr>`;
    return;
  }
  renderRows(items);
  state.lastAt = new Date();
}

// ---- 自动刷新 ----
function startLoop() {
  if (state.timer) clearInterval(state.timer);
  state.nextAt = Date.now() + (+$("#interval").value) * 1000;
  if (!$("#auto").checked) { paintTick(); return; }
  state.timer = setInterval(() => {
    if (!$("#auto").checked) { paintTick(); return; }
    if (Date.now() >= state.nextAt) {
      state.nextAt = Date.now() + (+$("#interval").value) * 1000;
      if (MODE === "static") reloadStatic().then(() => runSearch(true));
      else runSearch(true);
    }
    paintTick();
  }, 1000);
  paintTick();
}
async function reloadStatic() { try { const changed = await loadData(false); await loadStatus(); return changed; } catch (e) { return false; } }
function paintTick() {
  const last = state.lastAt ? `上次更新 ${hhmmss(state.lastAt)}` : "尚未更新";
  if (!state.keyword) { $("#tick").textContent = ""; return; }
  if (!$("#auto").checked) { $("#tick").innerHTML = `<span class="none">自动刷新已暂停</span> · ${last}`; return; }
  const secs = Math.max(0, Math.round((state.nextAt - Date.now()) / 1000));
  $("#tick").innerHTML = `自动刷新 · ${last} · <b>${secs}s</b> 后${MODE === "static" ? "重取快照" : "刷新报价"}`;
}

// ---- 事件 ----
$("#go").addEventListener("click", () => { runSearch(false); startLoop(); });
$("#q").addEventListener("keydown", (e) => { if (e.key === "Enter") { runSearch(false); startLoop(); } });
$("#sort").addEventListener("change", () => { runSearch(false); startLoop(); });
$("#minvol").addEventListener("change", () => { if (MODE === "static") runSearch(false); });
$("#scope").addEventListener("change", () => { runSearch(false); startLoop(); });
$("#auto").addEventListener("change", startLoop);
$("#interval").addEventListener("change", startLoop);
$("#refresh").addEventListener("click", async () => {
  if (MODE === "static") { await reloadStatic(); if (state.keyword) runSearch(false); }
  else { await fetch("api/refresh"); setTimeout(loadStatus, 1500); }
});

boot();

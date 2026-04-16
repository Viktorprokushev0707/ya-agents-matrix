"""Матрица зрелости × релизов: сервис × микросценарий с кружками.

- Цвет кружка — Уровень зрелости микросценария (Гигиена=зелёный, Дифференциация=розовый, Эксперимент=фиолетовый)
- Размер кружка — Количество релизов (квантили 33/66% по ненулевым значениям)
- Кружок рисуется только если Наличие функции ∈ {ДА, БЕТА}
- Релиз = запись в Статьях, где Тип id начинается с «Функция», с привязкой к сервису и микросценарию
"""
import json, os, urllib.request
from collections import defaultdict

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

TOKEN = os.environ.get("AIRTABLE_TOKEN")
BASE = "appUYifXcDfuqO001"
T_SERVICES = "tblfRVLB4aGb1yJI3"
T_MICROS = "tbl0CO70UilUN2uaK"
T_FUNCS = "tblnZrvq0PXNV3qgI"
T_ARTICLES = "tbl1AzFKIVEB4mnqy"

# Сервисы, у которых статьи уже собраны (MEMORY.md, 16.04.2026)
SERVICES_WITH_ARTICLES = {
    "ChatGPT", "Алиса AI", "Gemini", "Claude", "Copilot",
    "GigaChat", "Grok", "Perplexity AI", "Google AI Search", "Atlas",
}

HERE = os.path.dirname(os.path.abspath(__file__))


def fetch_all(table):
    recs, offset = [], None
    while True:
        url = f"https://api.airtable.com/v0/{BASE}/{table}?pageSize=100"
        if offset:
            url += "&offset=" + offset
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
        data = json.loads(urllib.request.urlopen(req).read())
        recs.extend(data["records"])
        offset = data.get("offset")
        if not offset:
            break
    return recs


def micro_sort_key(num):
    parts = (num or "0.0").split(".")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except ValueError:
        return (999, 999)


def quantile_thresholds(values):
    """Возвращает (T1, T2) — границы трёх бакетов по квантилям 33/66%."""
    if not values:
        return (1, 3)
    vs = sorted(values)
    n = len(vs)
    t1 = vs[int(n * 0.333)]
    t2 = vs[int(n * 0.666)]
    if t2 <= t1:
        t2 = t1 + 1
    return (t1, t2)


def size_bucket(n, t1, t2):
    """Возвращает радиус кружка: 0 → 6 (минимальный), >0..T1 → 10, T1+1..T2 → 20, >T2 → 30."""
    if n == 0:
        return 6
    if n <= t1:
        return 10
    if n <= t2:
        return 20
    return 30


def build():
    if not TOKEN:
        raise SystemExit("AIRTABLE_TOKEN не найден в окружении или в ../env")

    print("→ Загружаю Сервисы…")
    services = fetch_all(T_SERVICES)
    svc_by_id = {}
    for r in services:
        f = r["fields"]
        name = f.get("Название сервиса", "?")
        svc_by_id[r["id"]] = {
            "id": r["id"],
            "name": name,
            "has_articles": name in SERVICES_WITH_ARTICLES,
        }

    print("→ Загружаю Микросценарии…")
    micros = fetch_all(T_MICROS)
    micro_by_id = {}
    for r in micros:
        f = r["fields"]
        micro_by_id[r["id"]] = {
            "id": r["id"],
            "num": f.get("Номер", ""),
            "name": f.get("Микросценарий", ""),
            "desc": f.get("Описание", ""),
            "cluster": f.get("Макрокластер", "Прочее"),
            "maturity": f.get("Уровень зрелости", ""),
        }

    print("→ Загружаю Функции…")
    funcs = fetch_all(T_FUNCS)
    # presence[sid][mid] = 'ДА'/'БЕТА'/'НЕТ'/'На проверке'
    presence = defaultdict(dict)
    func_meta = defaultdict(dict)
    for r in funcs:
        f = r["fields"]
        svcs = f.get("Сервис") or []
        ms = f.get("Микросценарий") or []
        if not svcs or not ms:
            continue
        sid, mid = svcs[0], ms[0]
        presence[sid][mid] = f.get("Наличие функции", "НЕТ")
        func_meta[sid][mid] = {
            "stage": f.get("Стадия", ""),
            "name": f.get("Запись", ""),
            "comment": f.get("Комментарий", ""),
            "doc": f.get("Документация", ""),
            "doc_title": f.get("Название ссылки", ""),
        }

    print("→ Загружаю Статьи (для подсчёта релизов)…")
    articles = fetch_all(T_ARTICLES)
    # releases[sid][mid] = N
    releases = defaultdict(lambda: defaultdict(int))
    for r in articles:
        f = r["fields"]
        type_id = f.get("Тип id (Сервис, Компания, Функция, Маркетинг)", "") or ""
        if not type_id.startswith("Функция"):
            continue
        svcs = f.get("Сервис") or []
        ms = f.get("Микросценарий") or []
        if not svcs or not ms:
            continue
        for sid in svcs:
            for mid in ms:
                releases[sid][mid] += 1

    # Статистика для квантилей — только по парам с >0 релизами
    non_zero = []
    for sid, msmap in releases.items():
        for mid, n in msmap.items():
            if n > 0:
                non_zero.append(n)
    t1, t2 = quantile_thresholds(non_zero)
    print(f"   Квантили размеров: T1={t1}, T2={t2} (всего пар с релизами: {len(non_zero)})")

    # Сортировка сервисов — сначала те, у кого статьи собраны, потом остальные; внутри — по числу ДА
    svc_list = list(svc_by_id.values())
    def svc_sort(s):
        sid = s["id"]
        yes_count = sum(1 for v in presence[sid].values() if v in ("ДА", "БЕТА"))
        return (0 if s["has_articles"] else 1, -yes_count)
    svc_list.sort(key=svc_sort)

    # Сортировка микросценариев — по макрокластеру (порядок появления) и номеру
    cluster_order = []
    by_cluster = defaultdict(list)
    for m in sorted(micro_by_id.values(), key=lambda x: micro_sort_key(x["num"])):
        if m["cluster"] not in cluster_order:
            cluster_order.append(m["cluster"])
        by_cluster[m["cluster"]].append(m)
    clusters_out = [{"name": c, "micros": by_cluster[c]} for c in cluster_order]

    # Сборка ячеек
    cells_out = {}
    for s in svc_list:
        sid = s["id"]
        cells_out[sid] = {}
        for mid in micro_by_id:
            status = presence[sid].get(mid, "НЕТ")
            has_circle = status in ("ДА", "БЕТА")
            rel = releases[sid].get(mid, 0)
            radius = size_bucket(rel, t1, t2) if has_circle else 0
            m = micro_by_id[mid]
            cells_out[sid][mid] = {
                "status": status,
                "has_circle": has_circle,
                "releases": rel,
                "radius": radius,
                "maturity": m["maturity"],
                "meta": func_meta[sid].get(mid, {}),
            }

    data = {
        "services": svc_list,
        "clusters": clusters_out,
        "cells": cells_out,
        "thresholds": {"t1": t1, "t2": t2},
        "services_with_articles": sorted(SERVICES_WITH_ARTICLES),
    }

    out_path = os.path.join(HERE, "maturity_matrix.html")
    html_out = TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    print(f"✓ HTML сохранён: {out_path}")
    total_circles = sum(1 for sid in cells_out for mid in cells_out[sid] if cells_out[sid][mid]["has_circle"])
    print(f"   Сервисов: {len(svc_list)}, микросценариев: {sum(len(c['micros']) for c in clusters_out)}, кружков: {total_circles}")


TEMPLATE = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Матрица зрелости × релизов — ИИ-агенты · Cloud Research × Алиса Про</title>
<link rel="preconnect" href="https://yastatic.net" crossorigin>
<style>
  @font-face { font-family: 'YS Display'; font-weight: 400; src: url('https://yastatic.net/s3/home/fonts/ys/4/display-regular.woff2') format('woff2'); font-display: swap; }
  @font-face { font-family: 'YS Display'; font-weight: 500; src: url('https://yastatic.net/s3/home/fonts/ys/4/display-medium.woff2') format('woff2'); font-display: swap; }
  @font-face { font-family: 'YS Display'; font-weight: 700; src: url('https://yastatic.net/s3/home/fonts/ys/4/display-bold.woff2') format('woff2'); font-display: swap; }
  @font-face { font-family: 'YS Text'; font-weight: 400; src: url('https://yastatic.net/s3/home/fonts/ys/4/text-regular.woff2') format('woff2'); font-display: swap; }
  @font-face { font-family: 'YS Text'; font-weight: 500; src: url('https://yastatic.net/s3/home/fonts/ys/4/text-medium.woff2') format('woff2'); font-display: swap; }

  :root {
    --bg: #ffffff;
    --bg-soft: #f7f7f9;
    --ink: #1a1a1a;
    --ink-soft: #343434;
    --muted: #84839c;
    --line: #eaeaea;
    --line-soft: #f0f0f3;

    /* Уровни зрелости — цвета как просил клиент */
    --hygiene: #2E7D32;       /* зелёный — Гигиена */
    --diff: #E91E63;          /* розовый — Дифференциация */
    --exp: #8E24AA;           /* фиолетовый — Эксперимент */

    --cell-size: 48px;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; font-family: 'YS Text', 'Helvetica Neue', Arial, sans-serif; color: var(--ink); background: var(--bg); }

  header {
    padding: 14px 28px; border-bottom: 1px solid var(--line);
    position: sticky; top: 0; background: rgba(255,255,255,.96);
    backdrop-filter: saturate(160%) blur(10px); z-index: 50;
  }
  .bar { display: flex; align-items: center; gap: 22px; flex-wrap: wrap; }
  h1 {
    font-family: 'YS Display', sans-serif; font-weight: 500; font-size: 22px;
    letter-spacing: -0.02em; margin: 0;
  }
  h1 em { font-style: normal; font-weight: 400; color: var(--muted); }
  .eyebrow {
    font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); font-weight: 500;
  }

  .legend { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; font-size: 12px; }
  .legend-group { display: flex; gap: 10px; align-items: center; }
  .legend-group .label {
    font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); font-weight: 500;
  }
  .dot {
    display: inline-block; border-radius: 50%; vertical-align: middle;
  }
  .dot-hy { background: var(--hygiene); }
  .dot-df { background: var(--diff); }
  .dot-ex { background: var(--exp); }
  .sz-1 { width: 10px; height: 10px; }
  .sz-2 { width: 18px; height: 18px; }
  .sz-3 { width: 26px; height: 26px; }
  .mini { width: 7px; height: 7px; background: #bbb; }

  .note {
    font-size: 11.5px; color: var(--muted); padding: 8px 28px; border-bottom: 1px solid var(--line-soft); background: var(--bg-soft);
  }
  .note b { color: var(--ink-soft); font-weight: 500; }

  .table-wrap {
    overflow: auto; padding: 0 0 40px; scrollbar-width: thin;
  }
  .table-wrap::-webkit-scrollbar { width: 10px; height: 10px; }
  .table-wrap::-webkit-scrollbar-thumb { background: var(--line); border-radius: 10px; }

  table { border-collapse: separate; border-spacing: 0; font-size: 12px; margin: 0 28px; }
  th, td { padding: 0; margin: 0; }

  th.corner {
    position: sticky; left: 0; top: 0; z-index: 25;
    background: var(--bg); border-right: 1px solid var(--line); border-bottom: 1px solid var(--line);
    min-width: 360px; max-width: 360px;
    padding: 10px 18px 14px; text-align: left; vertical-align: bottom;
    font-size: 10.5px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); font-weight: 500;
  }
  th.svc {
    position: sticky; top: 0; z-index: 20;
    background: var(--bg); border-bottom: 1px solid var(--line); border-right: 1px solid var(--line-soft);
    padding: 8px 0 10px; height: 200px;
    min-width: var(--cell-size); max-width: var(--cell-size);
    text-align: center; vertical-align: bottom;
  }
  th.svc .rot {
    writing-mode: vertical-rl; transform: rotate(180deg);
    font-family: 'YS Display', sans-serif; font-weight: 500; font-size: 13px;
  }
  th.svc .mark {
    display: block; margin-top: 8px; font-size: 9px; color: var(--muted);
    writing-mode: horizontal-tb;
  }
  th.svc.no-articles .rot { color: var(--muted); }
  th.svc.no-articles .mark::before { content: "○"; color: #bbb; font-size: 11px; }
  th.svc.has-articles .mark::before { content: "●"; color: var(--ink-soft); font-size: 11px; }

  tr.cluster-row > td.cluster-label {
    position: sticky; left: 0; z-index: 15;
    background: var(--bg-soft); padding: 11px 18px;
    border-bottom: 1px solid var(--line-soft); border-right: 1px solid var(--line);
    font-family: 'YS Display', sans-serif; font-weight: 500; font-size: 13.5px;
    cursor: pointer; user-select: none;
    transition: background .15s;
  }
  tr.cluster-row:hover > td.cluster-label { background: #f1edf2; }
  tr.cluster-row > td.cluster-label .chev {
    display: inline-block; width: 16px; height: 16px;
    border-radius: 50%; background: var(--ink); color: #fff;
    text-align: center; line-height: 16px; font-size: 10px;
    margin-right: 10px; vertical-align: 1px;
    transition: transform .2s cubic-bezier(.4,0,.2,1), background .15s;
  }
  tr.cluster-row.open > td.cluster-label .chev {
    transform: rotate(90deg); background: var(--diff);
  }
  tr.cluster-row > td.cluster-cell {
    background: var(--bg-soft);
    border-bottom: 1px solid var(--line-soft); border-right: 1px solid var(--line-soft);
    min-width: var(--cell-size); max-width: var(--cell-size); height: 44px;
    text-align: center; vertical-align: middle;
    font-family: 'YS Text'; font-weight: 500; font-size: 10px;
    font-variant-numeric: tabular-nums; color: var(--muted);
    position: relative;
  }
  tr.cluster-row > td.cluster-cell svg.pie { display: block; margin: 0 auto; }
  tr.cluster-row > td.cluster-cell .cov-num {
    display: block; margin-top: 2px; font-size: 9.5px; color: var(--muted);
  }
  tr.cluster-row.open > td.cluster-cell { opacity: 0.45; }
  tr.cluster-row.hidden, tr.micro-row.hidden { display: none !important; }

  tr.micro-row { display: none; }
  tr.micro-row.visible { display: table-row; animation: fadeIn .2s ease both; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(-2px); } to { opacity: 1; transform: translateY(0); } }
  tr.micro-row > td.micro-label {
    position: sticky; left: 0; z-index: 12;
    background: var(--bg); padding: 8px 18px 8px 40px;
    border-bottom: 1px solid var(--line-soft); border-right: 1px solid var(--line);
    font-size: 12.5px; line-height: 1.35; max-width: 360px;
  }
  tr.micro-row > td.micro-label .num {
    color: var(--muted); margin-right: 10px; font-weight: 500; font-variant-numeric: tabular-nums; font-size: 11px;
  }
  tr.micro-row > td.micro-label .mat-tag {
    display: inline-block; margin-left: 8px; font-size: 9.5px; padding: 1px 7px; border-radius: 999px;
    color: #fff; letter-spacing: 0.04em; vertical-align: 2px;
  }
  .mat-tag.mat-hy { background: var(--hygiene); }
  .mat-tag.mat-df { background: var(--diff); }
  .mat-tag.mat-ex { background: var(--exp); }

  tr.micro-row > td.cell {
    border-bottom: 1px solid var(--line-soft); border-right: 1px solid var(--line-soft);
    min-width: var(--cell-size); max-width: var(--cell-size); height: var(--cell-size);
    position: relative; text-align: center; vertical-align: middle;
    cursor: default;
  }
  tr.micro-row > td.cell .circle {
    display: block; margin: 0 auto; border-radius: 50%;
    transition: transform .12s ease;
  }
  tr.micro-row > td.cell.hoverable:hover .circle { transform: scale(1.2); }
  tr.micro-row > td.cell.hoverable { cursor: pointer; }

  /* Tooltip */
  .tip {
    position: fixed; background: var(--ink); color: #fff;
    padding: 10px 13px; font-size: 12px; max-width: 320px;
    pointer-events: none; z-index: 80; box-shadow: 0 12px 32px -8px rgba(0,0,0,.3);
    border-radius: 8px; display: none; line-height: 1.5;
  }
  .tip.show { display: block; }
  .tip .t { font-weight: 500; font-family: 'YS Display'; font-size: 13px; margin-bottom: 3px; }
  .tip .s { font-size: 10.5px; letter-spacing: 0.08em; text-transform: uppercase; color: rgba(255,255,255,.65); margin-bottom: 5px; }
  .tip .c { color: rgba(255,255,255,.85); font-size: 11.5px; }
  .tip .pill { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 9.5px; margin-left: 6px; }
  .tip .pill.hy { background: var(--hygiene); }
  .tip .pill.df { background: var(--diff); }
  .tip .pill.ex { background: var(--exp); }

  .stats { display: flex; gap: 20px; font-size: 12px; }
  .stat b { font-family: 'YS Display'; font-weight: 500; font-size: 15px; margin-right: 5px; }
  .stat span { color: var(--muted); }

  .controls { display: flex; gap: 8px; align-items: center; margin-left: auto; position: relative; }
  button.btn {
    font-family: 'YS Text'; font-weight: 500; font-size: 12px;
    background: var(--ink); color: #fff; border: 0;
    padding: 7px 13px; border-radius: 999px; cursor: pointer;
    transition: background .15s;
  }
  button.btn:hover { background: var(--diff); }
  button.btn.ghost { background: transparent; color: var(--ink); border: 1px solid var(--line); }
  button.btn.ghost:hover { border-color: var(--diff); color: var(--diff); }

  .dd {
    position: absolute; top: 40px; right: 0; z-index: 60;
    background: #fff; border: 1px solid var(--line); border-radius: 12px;
    box-shadow: 0 12px 32px -8px rgba(0,0,0,.2);
    padding: 8px 0; min-width: 280px; max-height: 420px; overflow: auto;
    display: none;
  }
  .dd.open { display: block; }
  .dd .ddhead {
    display: flex; justify-content: space-between; padding: 6px 14px 8px; border-bottom: 1px solid var(--line-soft);
    font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em;
  }
  .dd .ddhead a { color: var(--diff); text-decoration: none; cursor: pointer; font-weight: 500; }
  .dd label {
    display: flex; align-items: center; gap: 10px; padding: 7px 14px;
    font-size: 12.5px; cursor: pointer; line-height: 1.3;
  }
  .dd label:hover { background: var(--bg-soft); }
  .dd label input { cursor: pointer; }
  .dd label .cnt { color: var(--muted); font-size: 11px; margin-left: auto; font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<header>
  <div class="bar">
    <div style="display:flex;align-items:center;gap:14px;">
      <h1>Матрица зрелости <em>× релизов</em></h1>
      <span class="eyebrow">Cloud Research × Алиса Про</span>
    </div>
    <div class="stats">
      <div class="stat"><b id="stat-svc">—</b><span>сервисов</span></div>
      <div class="stat"><b id="stat-ms">—</b><span>микросценариев</span></div>
      <div class="stat"><b id="stat-circles">—</b><span>кружков</span></div>
    </div>
    <div class="controls">
      <button class="btn" id="btn-expand">Развернуть все</button>
      <button class="btn ghost" id="btn-collapse">Свернуть все</button>
      <button class="btn ghost" id="btn-filter">Кластеры ▾</button>
      <div class="dd" id="dd-clusters">
        <div class="ddhead"><span>Показать кластеры</span><a id="dd-all">все</a> · <a id="dd-none">ничего</a></div>
        <div id="dd-list"></div>
      </div>
    </div>
  </div>
  <div class="bar" style="margin-top:10px;">
    <div class="legend">
      <div class="legend-group">
        <span class="label">Зрелость</span>
        <span><span class="dot dot-hy sz-2"></span> Гигиена</span>
        <span><span class="dot dot-df sz-2"></span> Дифференциация</span>
        <span><span class="dot dot-ex sz-2"></span> Эксперимент</span>
      </div>
      <div class="legend-group">
        <span class="label">Релизы за 3 мес.</span>
        <span><span class="dot dot-df mini"></span> 0</span>
        <span><span class="dot dot-df sz-1"></span> <span id="lg-1">1–T1</span></span>
        <span><span class="dot dot-df sz-2"></span> <span id="lg-2">T1+1–T2</span></span>
        <span><span class="dot dot-df sz-3"></span> <span id="lg-3">&gt;T2</span></span>
      </div>
      <div class="legend-group">
        <span class="label">Шапка сервиса</span>
        <span>● — статьи собраны</span>
        <span>○ — статей ещё нет</span>
      </div>
    </div>
  </div>
</header>
<div class="note">
  <b>Данные:</b> Функции — у всех 20 сервисов. Статьи — у 10 сервисов (помечены ●), собраны за последние ~3 месяца.
  У сервисов с отметкой ○ число релизов = 0 → кружки минимального размера. <b>Размер кружка</b> — квантильные бакеты по количеству релизов пары (сервис × микросценарий): T1=<span id="note-t1">?</span>, T2=<span id="note-t2">?</span>.
</div>

<div class="table-wrap">
  <table id="grid"></table>
</div>

<div class="tip" id="tip"></div>

<script>
const DATA = __DATA__;
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

const MAT_CLASS = {
  "Гигиена": "hy",
  "Дифференциация": "df",
  "Эксперимент": "ex",
};
const MAT_COLOR = {
  "Гигиена": "#2E7D32",
  "Дифференциация": "#E91E63",
  "Эксперимент": "#8E24AA",
};

function render() {
  const t = document.getElementById("grid");
  const t1 = DATA.thresholds.t1, t2 = DATA.thresholds.t2;
  document.getElementById("note-t1").textContent = t1;
  document.getElementById("note-t2").textContent = t2;
  document.getElementById("lg-1").textContent = `1–${t1}`;
  document.getElementById("lg-2").textContent = `${t1+1}–${t2}`;
  document.getElementById("lg-3").textContent = `>${t2}`;

  let h = "<thead><tr><th class='corner'>Макрокластер / Микросценарий</th>";
  DATA.services.forEach((s, i) => {
    const mark = s.has_articles ? "has-articles" : "no-articles";
    const tag = s.has_articles ? "статьи собраны" : "статей ещё нет";
    h += `<th class='svc ${mark}' data-sid='${s.id}'><span class='rot'>${esc(s.name)}</span><span class='mark' title='${tag}'></span></th>`;
  });
  h += "</tr></thead><tbody>";

  let totalCircles = 0;

  function clusterCoverage(cluster, sid) {
    let have = 0;
    const by = { "Гигиена": 0, "Дифференциация": 0, "Эксперимент": 0 };
    for (const m of cluster.micros) {
      const c = DATA.cells[sid]?.[m.id];
      if (c && c.has_circle) {
        have++;
        if (by[c.maturity] !== undefined) by[c.maturity]++;
      }
    }
    return { have, total: cluster.micros.length, by };
  }

  function piePath(cx, cy, r, a1, a2) {
    const large = (a2 - a1) > Math.PI ? 1 : 0;
    const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    const x2 = cx + r * Math.cos(a2), y2 = cy + r * Math.sin(a2);
    return `M ${cx} ${cy} L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`;
  }

  function renderPie(cov) {
    if (cov.have === 0) {
      return `<svg class='pie' width='10' height='10'><circle cx='5' cy='5' r='3' fill='#ddd'/></svg>`;
    }
    const share = cov.have / cov.total;
    // Радиус по покрытию: 8, 11, 14 — для низкого/среднего/высокого
    const r = share >= 0.66 ? 14 : share >= 0.33 ? 11 : 8;
    const size = r * 2 + 4;
    const cx = size / 2, cy = size / 2;
    const colors = { "Гигиена": "#2E7D32", "Дифференциация": "#E91E63", "Эксперимент": "#8E24AA" };

    // Если один уровень зрелости — просто кружок
    const nonZero = Object.entries(cov.by).filter(([,v]) => v > 0);
    if (nonZero.length === 1) {
      return `<svg class='pie' width='${size}' height='${size}'><circle cx='${cx}' cy='${cy}' r='${r}' fill='${colors[nonZero[0][0]]}'/></svg>`;
    }
    // Пай
    let ang = -Math.PI / 2;
    let paths = "";
    for (const [mat, n] of nonZero) {
      const delta = 2 * Math.PI * (n / cov.have);
      paths += `<path d='${piePath(cx, cy, r, ang, ang + delta)}' fill='${colors[mat]}'/>`;
      ang += delta;
    }
    return `<svg class='pie' width='${size}' height='${size}'>${paths}</svg>`;
  }

  DATA.clusters.forEach((cluster, ci) => {
    h += `<tr class='cluster-row' data-ci='${ci}' data-cluster='${esc(cluster.name)}'>`;
    h += `<td class='cluster-label'><span class='chev'>▸</span>${esc(cluster.name)} <span style='color:var(--muted);font-weight:400;font-size:11px;'>· ${cluster.micros.length}</span></td>`;
    DATA.services.forEach((s) => {
      const cov = clusterCoverage(cluster, s.id);
      const tip = `${s.name} · ${cluster.name}\nПокрытие: ${cov.have}/${cov.total}\nГигиена: ${cov.by["Гигиена"]} · Диф: ${cov.by["Дифференциация"]} · Эксп: ${cov.by["Эксперимент"]}`;
      const content = renderPie(cov) + (cov.have ? `<span class='cov-num'>${cov.have}/${cov.total}</span>` : "");
      h += `<td class='cluster-cell' data-ci='${ci}' title='${esc(tip)}'>${content}</td>`;
    });
    h += "</tr>";

    for (const m of cluster.micros) {
      const mat = m.maturity || "";
      const matCls = MAT_CLASS[mat] || "";
      const matShort = mat === "Гигиена" ? "Гиг" : mat === "Дифференциация" ? "Диф" : mat === "Эксперимент" ? "Эксп" : "";
      h += `<tr class='micro-row' data-mid='${m.id}' data-ci='${ci}'>`;
      h += `<td class='micro-label'><span class='num'>${esc(m.num)}</span>${esc(m.name)}`;
      if (matShort) h += ` <span class='mat-tag mat-${matCls}'>${matShort}</span>`;
      h += `</td>`;
      DATA.services.forEach((s) => {
        const c = DATA.cells[s.id]?.[m.id];
        if (c && c.has_circle) {
          totalCircles++;
          const color = MAT_COLOR[c.maturity] || "#999";
          const r = c.radius;
          const halo = c.status === "БЕТА" ? `stroke='#fff' stroke-width='2' stroke-dasharray='3,2'` : "";
          h += `<td class='cell hoverable' data-sid='${s.id}' data-mid='${m.id}'>`;
          h += `<svg class='circle' width='${r*2}' height='${r*2}' style='display:block;margin:0 auto;'>`;
          h += `<circle cx='${r}' cy='${r}' r='${r-1}' fill='${color}' ${halo}/>`;
          h += `</svg>`;
          h += `</td>`;
        } else {
          h += `<td class='cell'></td>`;
        }
      });
      h += "</tr>";
    }
  });

  h += "</tbody>";
  t.innerHTML = h;

  document.getElementById("stat-svc").textContent = DATA.services.length;
  document.getElementById("stat-ms").textContent = DATA.clusters.reduce((a,c)=>a+c.micros.length,0);
  document.getElementById("stat-circles").textContent = totalCircles;

  t.querySelectorAll("td.cell.hoverable").forEach(td => {
    td.addEventListener("mouseenter", (e) => showTip(e, td));
    td.addEventListener("mousemove", moveTip);
    td.addEventListener("mouseleave", hideTip);
  });

  // Свёртка кластеров — клик по заголовку
  t.querySelectorAll("tr.cluster-row > td.cluster-label").forEach(td => {
    td.addEventListener("click", () => {
      const row = td.parentElement;
      const ci = row.dataset.ci;
      const open = row.classList.toggle("open");
      t.querySelectorAll(`tr.micro-row[data-ci='${ci}']`).forEach(mr => mr.classList.toggle("visible", open));
    });
  });
}

function setAllOpen(open) {
  const t = document.getElementById("grid");
  t.querySelectorAll("tr.cluster-row").forEach(r => {
    r.classList.toggle("open", open);
    const ci = r.dataset.ci;
    t.querySelectorAll(`tr.micro-row[data-ci='${ci}']`).forEach(mr => mr.classList.toggle("visible", open));
  });
}

function applyFilter() {
  const checked = new Set();
  document.querySelectorAll("#dd-list input:checked").forEach(cb => checked.add(cb.value));
  const t = document.getElementById("grid");
  t.querySelectorAll("tr.cluster-row").forEach(r => {
    const name = r.dataset.cluster;
    const show = checked.has(name);
    r.classList.toggle("hidden", !show);
    const ci = r.dataset.ci;
    t.querySelectorAll(`tr.micro-row[data-ci='${ci}']`).forEach(mr => {
      mr.classList.toggle("hidden", !show);
    });
  });
}

function buildFilter() {
  const list = document.getElementById("dd-list");
  let h = "";
  DATA.clusters.forEach(c => {
    h += `<label><input type='checkbox' value='${esc(c.name)}' checked> <span>${esc(c.name)}</span> <span class='cnt'>${c.micros.length}</span></label>`;
  });
  list.innerHTML = h;
  list.querySelectorAll("input").forEach(cb => cb.addEventListener("change", applyFilter));
  document.getElementById("dd-all").addEventListener("click", () => {
    list.querySelectorAll("input").forEach(cb => cb.checked = true);
    applyFilter();
  });
  document.getElementById("dd-none").addEventListener("click", () => {
    list.querySelectorAll("input").forEach(cb => cb.checked = false);
    applyFilter();
  });
  document.getElementById("btn-filter").addEventListener("click", (e) => {
    e.stopPropagation();
    document.getElementById("dd-clusters").classList.toggle("open");
  });
  document.addEventListener("click", (e) => {
    const dd = document.getElementById("dd-clusters");
    if (!dd.contains(e.target) && e.target.id !== "btn-filter") dd.classList.remove("open");
  });
}


function findMicro(mid) { for (const c of DATA.clusters) for (const m of c.micros) if (m.id === mid) return m; return null; }
function findService(sid) { return DATA.services.find(s => s.id === sid); }

const tip = document.getElementById("tip");
function showTip(e, td) {
  const sid = td.dataset.sid, mid = td.dataset.mid;
  const s = findService(sid), m = findMicro(mid);
  const c = DATA.cells[sid][mid];
  const matCls = MAT_CLASS[c.maturity] || "";
  tip.innerHTML =
    `<div class='s'>${esc(s.name)} · ${esc(m.num)}` +
    (c.maturity ? `<span class='pill ${matCls}'>${esc(c.maturity)}</span>` : "") +
    `</div>` +
    `<div class='t'>${esc(m.name)}</div>` +
    `<div class='c'>Наличие: <b>${esc(c.status)}</b>${c.meta.stage ? " · " + esc(c.meta.stage) : ""}<br>` +
    `Релизов в статьях: <b>${c.releases}</b>${s.has_articles ? "" : " <span style='color:#f7993c'>(статьи не собраны)</span>"}</div>`;
  tip.classList.add("show");
  moveTip(e);
}
function moveTip(e) {
  const pad = 14;
  const r = tip.getBoundingClientRect();
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + r.width > window.innerWidth) x = e.clientX - r.width - pad;
  if (y + r.height > window.innerHeight) y = e.clientY - r.height - pad;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
function hideTip() { tip.classList.remove("show"); }

render();
buildFilter();
document.getElementById("btn-expand").addEventListener("click", () => setAllOpen(true));
document.getElementById("btn-collapse").addEventListener("click", () => setAllOpen(false));
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build()

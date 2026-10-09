"""Portable research briefing: comparison table, mechanism map, evidence."""
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
import re

from processing.codex_review import safe_url, validate_review

LABELS = {"know": "了解即可", "read": "建议精读", "try": "建议试验",
          "skip": "暂不推荐", "needs_evidence": "待补证据"}
LEVELS = {"metadata": "仅元信息", "abstract": "已读摘要", "full_text": "已核对原文",
          "code": "已查看代码", "reproduced": "已有复现实验"}
SHORT_TOPICS = {"data": "人类数据", "adaptation": "泛化 / ICL", "models": "模型与动作",
                "deployment": "真机部署", "resources": "评测与资源"}


def link(url, title):
    if not safe_url(url):
        return escape(str(title))
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(str(title))}</a>'


def _short_name(paper, review):
    return re.split(r"[:：]", review["headline"], maxsplit=1)[0][:45]


def render_review(packet: dict, result: dict, path: Path) -> None:
    validate_review(packet, result)
    papers = {p["id"]: p for p in packet["candidates"]}
    reviews = {r["id"]: r for r in result["reviews"]}
    selected = result["highlight_ids"]
    reviewed_at = datetime.fromisoformat(result["reviewer"]["reviewed_at"].replace("Z", "+00:00"))
    if reviewed_at.tzinfo is None:
        reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
    reviewed_at = reviewed_at.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    overview = result.get("overview") or {}
    summary = overview.get("summary") or "先看总览，再展开感兴趣的工作。方法是否适合当前系统，需要结合原文条件判断。"
    rows, details = [], []
    for index, pid in enumerate(selected, 1):
        r, p = reviews[pid], papers[pid]
        tags = ''.join(f'<span class="tag">{escape(SHORT_TOPICS[t])}</span>' for t in r["topics"])
        name = _short_name(p, r)
        rows.append(f'''<tr data-topics="{' '.join(r['topics'])}">
        <td class="paper"><span class="number">{index:02d}</span><a href="#paper-{index}">{escape(name)}</a>
        <div class="tags">{tags}</div><span class="verdict">{LABELS[r['decision']]}</span></td>
        <td>{escape(r['what_changed'])}</td><td>{escape(r['why_care'])}</td>
        <td class="limit">{escape(r['caveat'])}</td></tr>''')
        sources = ''.join(f'<li>{link(s["url"], s["locator"])}<p>{escape(s["claim"])}</p></li>' for s in r["sources"])
        details.append(f'''<details class="paper-detail" id="paper-{index}">
        <summary><span class="detail-title"><span class="number">{index:02d}</span>{escape(r['headline'])}</span>
        <span class="summary-hint">依据与下一步 <span aria-hidden="true">＋</span></span></summary>
        <div class="detail-body"><p><b>可以先做什么</b>{escape(r['transfer'])}</p>
        <p><b>适用边界</b>{escape(r['caveat'])}</p>
        <div class="evidence"><div><span class="tag">{LEVELS[r['evidence_level']]}</span>
        <p>{link(p['url'], p['title'])}</p><small>首次发表：{escape(p.get('date') or '待核验')}<br>
        行业重要性 {r['industry_importance']}/5 · 迁移价值 {r['transfer_value']}/5<br>评分是阅读建议，不代表复现结果。</small></div><ol>{sources}</ol></div>
        </div></details>''')
    map_rows = []
    for item in overview.get("paths", []):
        pid = item["paper_id"]
        if pid not in selected:
            continue
        number = selected.index(pid) + 1
        name = _short_name(papers[pid], reviews[pid])
        map_rows.append(f'''<div class="map-row"><a class="map-name" href="#paper-{number}">{escape(name)}</a>
        <div class="node">{escape(item['input'])}</div><span class="arrow" aria-hidden="true">→</span>
        <div class="node mechanism">{escape(item['mechanism'])}</div><span class="arrow" aria-hidden="true">→</span>
        <div class="node">{escape(item['outcome'])}</div></div>''')
    diagram = ''
    if map_rows:
        diagram = f'''<section class="map-section"><div class="section-heading"><h2>这些方法有什么不同？</h2>
        <span>读图：用什么信息 → 怎么处理 → 想改善什么</span></div>
        <div class="map-scroll"><div class="method-map">{''.join(map_rows)}</div></div>
        <p class="caption">方法思路示意；箭头表示信息流，不表示效果排名或可直接组合。</p></section>'''
    rest = [r for r in result["reviews"] if r["id"] not in selected]
    rest_rows = ''.join(f'<tr><td>{link(papers[r["id"]]["url"], papers[r["id"]]["title"])}</td>'
                        f'<td>{LABELS[r["decision"]]}</td><td>{escape(r["why_care"])}</td></tr>' for r in rest)
    rest_html = '' if not rest else f'''<details class="other"><summary>其余 {len(rest)} 项已评估工作</summary>
    <div class="table-scroll"><table><thead><tr><th>工作</th><th>建议</th><th>原因</th></tr></thead><tbody>{rest_rows}</tbody></table></div></details>'''
    filters = ''.join(f'<button type="button" data-filter="{t}" aria-pressed="false">{escape(SHORT_TOPICS[t])}</button>'
                      for t in SHORT_TOPICS if any(t in reviews[pid]["topics"] for pid in selected))
    empty = '<tr><td colspan="4">本期没有证据充分的重点推荐，不凑数。其余候选和待补证据的项目仍保留。</td></tr>'
    content = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(packet['label'])} · WAM 研究简报</title>
    <style>
    :root{{color-scheme:light;--ink:#172a44;--muted:#667487;--line:#e1e6ed;--blue:#245ccc;--soft:#f3f6fb}}
    *{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:#fff;color:var(--ink);font-family:Inter,'Segoe UI','Microsoft YaHei',sans-serif;font-size:14px;line-height:1.75}}
    .topbar{{border-bottom:1px solid var(--line)}}.topbar>div{{max-width:1200px;margin:auto;padding:15px 32px;display:flex;justify-content:space-between;gap:20px;font-size:12px;letter-spacing:.4px}}
    .brand{{font-weight:750}}.muted{{color:var(--muted)}}main{{max-width:1200px;padding:35px 32px 40px;margin:auto}}
    header{{margin-bottom:27px}}.kicker{{font-size:12px;color:var(--blue);font-weight:650}}h1{{font-size:32px;line-height:1.35;letter-spacing:-.5px;margin:10px 0 16px}}
    .intro{{max-width:950px;font-size:16px;margin:0}}.stats{{margin:17px 0 0;display:flex;gap:22px;font-size:12px;color:var(--muted)}}.stats b{{color:var(--ink);font-size:16px;margin-right:4px}}
    h2{{font-size:19px;margin:0}}.section-heading{{display:flex;align-items:baseline;justify-content:space-between;gap:16px;margin:0 0 13px}}.section-heading>span{{font-size:12px;color:var(--muted)}}
    .filters{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:15px 0}}button{{font:inherit;font-size:12px;background:white;border:1px solid var(--line);border-radius:5px;padding:4px 11px;cursor:pointer;color:var(--muted)}}
    button[aria-pressed=true]{{background:var(--ink);border-color:var(--ink);color:white}}button:focus-visible,a:focus-visible,summary:focus-visible{{outline:2px solid var(--blue);outline-offset:3px}}
    .table-scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:7px}}table{{width:100%;border-collapse:collapse;table-layout:fixed;min-width:760px}}th{{text-align:left;background:var(--soft);font-size:12px;color:var(--muted);padding:12px 17px;font-weight:600}}
    td{{border-top:1px solid var(--line);padding:19px 17px;vertical-align:top;font-size:14px;line-height:1.75}}.paper{{width:23%}}.paper>a{{font-size:16px;font-weight:700;color:var(--ink)}}.number{{font-variant-numeric:tabular-nums;color:#8d9aad;font-size:11px;margin-right:9px}}
    a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}.tags{{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0}}.tag{{display:inline-block;font-size:10px;color:#53667e;background:#f1f4f8;border:1px solid #e7ecf2;border-radius:4px;padding:1px 6px;white-space:nowrap}}
    .verdict{{font-size:11px;color:var(--blue)}}.limit{{color:var(--muted);font-size:13px}}.map-section{{margin:33px 0}}.map-scroll{{overflow-x:auto;border:1px solid var(--line);border-radius:7px;padding:18px 22px;background:#fbfcfe}}
    .method-map{{min-width:730px}}.map-row{{display:grid;grid-template-columns:120px 1fr 30px 1fr 30px 1fr;align-items:center;gap:10px;margin:10px 0}}.map-name{{font-size:13px;font-weight:650;color:var(--ink)}}
    .node{{padding:10px 13px;text-align:center;background:white;border:1px solid var(--line);border-radius:5px;font-size:13px}}.mechanism{{border-color:#c9d8f3;background:#f0f5ff;color:#234d8b}}.arrow{{text-align:center;color:#8b9bb0;font-size:19px}}
    .caption{{font-size:11px;color:var(--muted);margin:8px 0}}.paper-detail{{border-bottom:1px solid var(--line);scroll-margin-top:20px}}.paper-detail:first-of-type{{border-top:1px solid var(--line)}}
    summary{{cursor:pointer;list-style:none;padding:17px 0;display:flex;align-items:center;justify-content:space-between;gap:12px}}summary::-webkit-details-marker{{display:none}}.detail-title{{font-size:14px;font-weight:650}}.summary-hint{{font-size:11px;color:var(--muted);white-space:nowrap}}details[open] .summary-hint span{{display:inline-block;transform:rotate(45deg)}}
    .detail-body{{padding:0 0 22px 25px}}.detail-body p{{margin:8px 0}}.detail-body b{{margin-right:14px}}.evidence{{display:grid;grid-template-columns:1fr 1.5fr;gap:28px;padding:17px 20px;margin-top:18px;background:var(--soft);border-radius:5px;font-size:12px}}.evidence small{{color:var(--muted)}}.evidence ol{{margin:0;padding-left:18px}}.evidence li{{margin:0 0 10px}}.evidence li p{{color:var(--muted);margin:3px 0}}
    .other{{margin-top:22px;font-size:13px}}footer{{border-top:1px solid var(--line);margin-top:30px;padding-top:15px;display:flex;justify-content:space-between;gap:20px;font-size:11px;color:var(--muted)}}[hidden]{{display:none!important}}
    @media(max-width:700px){{main{{padding:25px 17px}}.topbar>div{{padding:12px 17px}}.topbar .muted{{display:none}}h1{{font-size:25px}}.intro{{font-size:14px}}.section-heading{{display:block}}.section-heading>span{{display:block;margin-top:4px}}.evidence{{grid-template-columns:1fr}}.summary-hint{{font-size:0}}.summary-hint span{{font-size:15px}}footer{{display:block}}.detail-body{{padding-left:0}}}}
    @media print{{.filters{{display:none}}main{{max-width:none;padding:0}}.table-scroll,.map-scroll{{overflow:visible}}table{{min-width:0}}.paper-detail{{break-inside:avoid}}}}
    </style></head><body>
    <div class="topbar"><div><span class="brand">WAM / 研究简报</span><span class="muted">人类数据 · 多任务泛化 · 真实场景部署</span></div></div>
    <main><header><div class="kicker">{escape(packet['label'])}</div><h1>{escape(overview.get('title') or '今天，哪些方法值得借鉴？')}</h1>
    <p class="intro">{escape(summary)}</p><div class="stats"><span><b>{len(selected)}</b>项重点</span><span><b>{len(result['reviews'])}</b>项已评估</span><span>{reviewed_at} · 北京时间</span></div></header>
    <section><div class="section-heading"><h2>一分钟总览</h2><span>先横向比较，再决定读哪篇</span></div>
    <div class="filters" aria-label="筛选总览"><button type="button" data-filter="all" aria-pressed="true">全部</button>{filters}<span class="muted" id="filter-count" aria-live="polite"></span></div>
    <div class="table-scroll"><table id="highlights"><colgroup><col style="width:23%"><col style="width:27%"><col style="width:25%"><col style="width:25%"></colgroup><thead><tr><th>工作 / 标签</th><th>它做了什么</th><th>对 WAM 有什么参考</th><th>先注意什么</th></tr></thead><tbody>{''.join(rows) or empty}</tbody></table></div></section>
    {diagram}<section><div class="section-heading"><h2>想多看一点</h2><span>展开原文依据、限制和可尝试的下一步</span></div>{''.join(details)}</section>{rest_html}
    <footer><span>觉得有用、想试验或已经知道，直接告诉 Codex；反馈保存在本地。</span><span>迁移建议尚需实验验证 · 原文入口随证据提供</span></footer></main>
    <script>
    document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{{
      const topic=button.dataset.filter;let count=0;
      document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
      document.querySelectorAll('#highlights tbody tr[data-topics]').forEach(row=>{{
        row.hidden=topic!=='all'&&!row.dataset.topics.split(' ').includes(topic);if(!row.hidden)count++;
      }});document.getElementById('filter-count').textContent=topic==='all'?'':`显示 ${{count}} 项`;
    }}));
    document.querySelectorAll('a[href^="#paper-"]').forEach(a=>a.addEventListener('click',()=>{{
      const item=document.getElementById(a.getAttribute('href').slice(1));if(item)item.open=true;
    }}));
    </script></body></html>'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

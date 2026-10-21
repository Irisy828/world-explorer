"""Metadata-only discovery and explainable editorial selection; no video inference."""
import csv
import json
import math
from datetime import datetime, timedelta, timezone
from collections import Counter
import collector as app

TOPICS = {
 '自然科学': ['science explained', '科学', '科普', 'experiment'],
 '地理生态': ['geography explained', '地理', '生态', 'wildlife'],
 '历史文化': ['history explained', '历史', '文化', 'archaeology'],
 '工程技术': ['engineering explained', '工程', '技术', 'how it works'],
}

def instant(value):
    try:
        t = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def discover(c, key, now, fetch=app.request):
    """One search per market, rotating topics daily; bounded by number of markets."""
    found = {}
    topics = list(TOPICS)
    for i, region in enumerate(c['regions']):
        topic = topics[(now.date().toordinal() + i) % len(topics)]
        page = fetch({'part': 'snippet', 'type': 'video', 'q': TOPICS[topic][0],
                      'regionCode': region, 'maxResults': c['page_size'], 'order': 'relevance',
                      'publishedAfter': (now-timedelta(days=30)).isoformat(),
                      'safeSearch': 'strict'}, key, 'search')
        ids = [v.get('id', {}).get('videoId', '') for v in page.get('items', [])]
        ids = [v for v in ids if app.re.fullmatch('[A-Za-z0-9_-]{11}', v)]
        if not ids:
            continue
        page = fetch({'part': 'snippet,statistics,status', 'id': ','.join(ids)}, key, 'videos')
        # Reuse public status, keyword filtering, metadata parsing and deduplication.
        local = dict(c, regions=[region], pages_per_region=1, top_n=500)
        for row in app.collect(local, key, lambda p, k: page):
            if row['video_id'] not in found:
                found[row['video_id']] = dict(row, topic=topic, discovery_query=TOPICS[topic][0])
            else:
                found[row['video_id']]['regions'].append(region)
    return list(found.values())  # Store the full discovery pool, not only selected ten.


def delta(row, root, now):
    current = row.get('view_count')
    pub = instant(row.get('published_at'))
    if type(current) is not int or current < 0 or not pub or pub > now:
        return None, None, '当前计数或发布时间缺失/无效'
    snapshots = []
    for path in (root/'history').glob('*.json'):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            t = instant(data.get('collected_at'))
            if data.get('demo') or not t or t.date() >= now.astimezone(timezone.utc).date() or not pub <= t < now:
                continue
            hours = (now-t).total_seconds()/3600
            if not 18 <= hours <= 30:
                continue
            old = next((v for v in data['videos'] if v['video_id'] == row['video_id']), None)
            if old and type(old.get('view_count')) is int and old['view_count'] >= 0:
                snapshots.append((t, old['view_count'], hours))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if not snapshots:
        return None, None, '无18至30小时内有效跨日基线；不补零、不外推'
    t, previous, hours = max(snapshots)
    if current < previous:
        return None, hours, '计数回退；不计算增量'
    return current-previous, round(hours, 2), '相邻有效快照差值；非精确24小时热度'


def enrich(rows, root, now, c, demo=False):
    candidates=[]
    for r in rows:
        pub=instant(r.get('published_at'))
        if not pub or pub > now or (now-pub).total_seconds() > 30*86400:
            continue
        text=(r['title']+' '+r.get('description','')).casefold()
        matches=[t for t, words in TOPICS.items() if any(w.casefold() in text for w in words)]
        # A search query is a discovery hint, never evidence that a video teaches a topic.
        if not matches:
            continue
        topic=matches[0]
        age=(now-pub).total_seconds()/86400
        score=60 + round(30*(1-age/30)) + min(10, len(matches)*2)
        change, hours, reason=(None,None,'模拟数据不计算真实增量') if demo else delta(r,root,now)
        candidates.append(dict(r, topic=topic, candidate_title_zh=f'{topic}观察：围绕“{next(w for w in TOPICS[topic] if w.casefold() in text)}”，有哪些问题值得追问？',
            title_note='编辑拟题，非原题翻译；未观看视频，须核验内容后重拟',
            score=score, score_basis=f'元数据关键词匹配60分；30日内新鲜度{round(30*(1-age/30))}分；题材匹配{min(10,len(matches)*2)}分；不使用播放量评分',
            evidence='; '.join(w for w in TOPICS[topic] if w.casefold() in text),
            view_delta=change, interval_hours=hours, delta_note=reason, review_status='待审核'))
    candidates.sort(key=lambda r:(-r['score'],r['video_id']))
    selected=[]; topics=Counter(); regions=Counter()
    # Assign one observed market per video; hard caps stay in place even on sparse days.
    for r in candidates:
        market=min(r['regions'],key=lambda x:(regions[x],x))
        if topics[r['topic']]>=3 or regions[market]>=3:
            continue
        r['selection_market']=market
        selected.append(r); topics[r['topic']]+=1; regions[market]+=1
        if len(selected)==10:
            break
    return [dict(r,rank=i) for i,r in enumerate(selected,1)]


def report(root, rows, now, demo=False):
    folder=root/'reports';folder.mkdir(parents=True,exist_ok=True)
    stem=now.strftime('%Y-%m-%d')+'-review'
    fields=['rank','video_id','candidate_title_zh','title_note','title','channel','published_at','topic','selection_market','regions','score','score_basis','evidence','view_count','view_delta','interval_hours','delta_note','review_status','url']
    with (folder/(stem+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for r in rows:
            writer.writerow({k:app.csv_safe('、'.join(r[k]) if isinstance(r.get(k),list) else (r.get(k) if r.get(k) is not None else '')) for k in fields})
    lines=[f'# 每日待审核报告 · {now.date()}', '', '模拟数据，仅验证流程。' if demo else '来源：YouTube Data API；仅依据公开元数据筛选。',
           '', f'目标10条；实际{len(rows)}条；不足时不虚构或放宽多样性上限。',
           '累计播放量不等于24小时热度；增量仅为标注间隔内的有效快照差值。',
           '地区是发现市场，不是作者国籍、拍摄地或语言。拟题不是翻译或视频内容结论。',
           '评分为本地编辑优先级，不是YouTube指标、事实可靠性或内容质量评分。',
           '审核：观看原视频、核对原题/内容/来源/版权、确认中文标题后才可使用。','']
    for r in rows:
        lines += [f"## {r['rank']}. {app.cell(r['candidate_title_zh'])}",
            f"原题：[{app.cell(r['title'])}]({r['url']})",f"频道：{app.cell(r['channel'])}；发布时间：{app.cell(r['published_at'])}",
            f"题材：{r['topic']}；发现市场：{','.join(r['regions'])}；分配市场：{r['selection_market']}",
            f"编辑评分：{r['score']}；{app.cell(r['score_basis'])}；匹配词：{app.cell(r['evidence'])}",
            f"累计播放量：{r['view_count'] if r['view_count'] is not None else '缺测'}；快照增量：{r['view_delta'] if r['view_delta'] is not None else '不可计算'}；间隔小时：{r['interval_hours']}；{r['delta_note']}",
            '状态：待审核；'+r['title_note'],'']
    (folder/(stem+'.md')).write_text('\n'.join(lines),encoding='utf-8')

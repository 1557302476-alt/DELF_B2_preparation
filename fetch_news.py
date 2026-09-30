#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_news.py — 抓取当日法语新闻，就地写入 index.html 的 NEWSJS / NEWS_META。

- 只用 Python 标准库（无 pip 依赖）。
- 用法：python3 fetch_news.py
- 数据源：
    · RFI 慢速新闻（含音频）：RSS + 逐篇抓取同步文字稿 → 听力播放器 & 阅读精读共用
    · RFI 法国新闻 / TV5Monde Apprendre / Courrier International / 20 Minutes：RSS 标题+链接
- 只存标题、链接、摘要与 RFI 的文字稿，不存第三方全文（RSS 协议禁止全文转载）。
- 失败处理：某源失败就标记 status 并置空，不清空其它源；全部失败则不改动 index.html。
"""

import sys, os, re, json, html as _html, datetime, ssl
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, 'index.html')

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# 每个源取多少条
PODCAST_COUNT = 8        # RFI 慢速新闻
PODCAST_TXT   = 6        # 其中多少条抓文字稿（用于阅读精读 + 听写）
TEXT_COUNTS = {
    'RFI 法国新闻': 12,
    'TV5Monde Apprendre': 10,
    'Courrier International': 10,
    'Le Monde': 12,
}

# 部分自包含 Python（如官网 .pkg 安装版）不携带 CA 证书，导致 SSL 校验失败。
# 优先用系统 CA 包（macOS / Linux 常见位置），找不到才退回默认。
_CA_CANDIDATES = [
    '/etc/ssl/cert.pem',                     # macOS / 部分 Linux
    '/etc/ssl/certs/ca-certificates.crt',    # Debian / Ubuntu
    '/etc/pki/tls/certs/ca-bundle.crt',      # RHEL / Fedora
]
_CAFILE = next((p for p in _CA_CANDIDATES if os.path.isfile(p)), None)
_SSL_CTX = ssl.create_default_context(cafile=_CAFILE) if _CAFILE else ssl.create_default_context()

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'application/rss+xml,application/xml,text/xml,application/json,*/*',
        'Accept-Language': 'fr,fr-FR;q=0.9,en;q=0.8',
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return r.read()

def strip_tags(s):
    return _html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))

def clean(s):
    return re.sub(r'\s+', ' ', strip_tags(s)).strip()

def parse_rss(xml_bytes):
    """RSS/Atom → [{t,u,d,s,audio?}]"""
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter('item'):
        d = {}
        for child in it:
            tag = child.tag.split('}')[-1]
            if tag == 'title':
                d['t'] = clean(child.text)
            elif tag == 'link':
                d['u'] = clean(child.text)
            elif tag == 'pubDate':
                d['d'] = clean(child.text)
            elif tag == 'description':
                d['s'] = clean(child.text)
            elif tag == 'enclosure':
                u = child.get('url', '')
                typ = (child.get('type') or '')
                # 只把真正的音频 enclosure 当听力材料；图片 enclosure 忽略
                if u and 'audio' in typ:
                    d['audio'] = u
        if d.get('t'):
            items.append(d)
    return items

def extract_transcript(page_bytes):
    """从 RFI 文章页抽出同步文字稿（单词被双空格分隔，需压平）。"""
    try:
        txt = page_bytes.decode('utf-8', 'replace')
    except Exception:
        return ''
    i = txt.find('m-transcription__content')
    if i < 0:
        return ''
    seg = txt[i:i + 90000]
    paras = re.findall(r'<p>(.*?)</p>', seg, re.S)
    out = []
    for p in paras:
        t = _html.unescape(re.sub(r'<[^>]+>', ' ', p))
        t = re.sub(r'\s+', ' ', t).strip()
        if len(t) > 1:
            out.append(t)
    return '\n\n'.join(out)

def fetch_podcast():
    name = 'RFI 慢速新闻（含音频）'
    url = ('https://apis.fle.rfi.fr/products/get_product/'
           'fle_getpodcast_by_nid_author_rfi?token_application=applepodcast_fle'
           '&program.entrepriseId=WBMZ39-FLE-FR-20220627')
    src = {'name': name, 'url': url, 'kind': 'audio', 'status': 'ok', 'items': []}
    try:
        items = parse_rss(fetch(url))[:PODCAST_COUNT]
    except Exception as e:
        src['status'] = 'fail: ' + str(e)[:80]
        return src
    for it in items:
        it['s'] = (it.get('s') or '')[:220]

    def grab(it):
        if it.get('u') and not it.get('ex'):
            try:
                ex = extract_transcript(fetch(it['u'], timeout=40))
                if ex:
                    it['ex'] = ex
            except Exception:
                pass
        return it

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(grab, items[:PODCAST_TXT]))
    src['items'] = items
    return src

def fetch_text(name, url):
    kind = 'article'
    src = {'name': name, 'url': url, 'kind': kind, 'status': 'ok', 'items': []}
    try:
        items = parse_rss(fetch(url))[:TEXT_COUNTS.get(name, 12)]
    except Exception as e:
        src['status'] = 'fail: ' + str(e)[:80]
        return src
    for it in items:
        it['s'] = (it.get('s') or '')[:220]
    src['items'] = items
    return src

def patch_index(newsjs, meta_ts):
    with open(INDEX, encoding='utf-8') as f:
        h = f.read()
    for mk in ('/*__NEWSJS_START__*/', '/*__NEWSJS_END__*/',
               '/*__NEWS_META_START__*/', '/*__NEWS_META_END__*/'):
        if mk not in h:
            print('✗ 缺少标记 %s —— 请先给 index.html 打补丁。' % mk)
            sys.exit(2)
    js = json.dumps(newsjs, ensure_ascii=False, separators=(',', ':'))
    meta = json.dumps({'ts': meta_ts}, ensure_ascii=False)
    # 用字符串切片替换（不能用 re.sub：它会把替换串里的 \\n 再解析成换行）
    def replace_between(text, start_mk, end_mk, payload):
        i = text.index(start_mk) + len(start_mk)
        j = text.index(end_mk)
        return text[:i] + payload + text[j:]

    h = replace_between(h, '/*__NEWS_META_START__*/', '/*__NEWS_META_END__*/', meta)
    h = replace_between(h, '/*__NEWSJS_START__*/', '/*__NEWSJS_END__*/', js)
    with open(INDEX, 'w', encoding='utf-8') as f:
        f.write(h)

def main():
    sources = []
    sources.append(fetch_podcast())
    sources.append(fetch_text('RFI 法国新闻', 'https://www.rfi.fr/fr/rss'))
    sources.append(fetch_text('TV5Monde Apprendre', 'https://apprendre.tv5monde.com/fr/rss'))
    sources.append(fetch_text('Courrier International',
                              'https://www.courrierinternational.com/feed/all/rss.xml'))
    sources.append(fetch_text('Le Monde', 'https://www.lemonde.fr/rss/une.xml'))

    total = sum(len(s['items']) for s in sources)
    if total == 0:
        print('✗ 所有源都抓取失败，保留上次数据，未改动 index.html。')
        sys.exit(1)

    ts = datetime.datetime.now().isoformat(timespec='seconds')
    patch_index(sources, ts)

    print('✓ 已更新 index.html（抓取时间 %s）' % ts)
    for s in sources:
        tag = '✓' if s['status'] == 'ok' else '✗'
        print('  %s %-24s %2d 条  %s' % (tag, s['name'], len(s['items']),
                                          '' if s['status'] == 'ok' else s['status']))
    print('  合计 %d 条。' % total)

if __name__ == '__main__':
    main()

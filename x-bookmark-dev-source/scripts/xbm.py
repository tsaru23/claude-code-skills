#!/usr/bin/env python3
"""xbm.py - X (Twitter) Bookmarks -> Markdown 保存 CLI

公式 X API v2 のみを使用する。スクレイピングや Cookie 利用は一切行わない。
標準ライブラリのみに依存する。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.server
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
USERS_ME_URL = "https://api.x.com/2/users/me"
BOOKMARKS_URL_TMPL = "https://api.x.com/2/users/{id}/bookmarks"
TWEET_FIELDS = "created_at,author_id,entities,note_tweet,lang,conversation_id"
SCOPE = "tweet.read users.read bookmark.read offline.access"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
COST_PER_TWEET_USD = 0.001

SELF_LINK_DOMAINS = {"twitter.com", "www.twitter.com", "x.com", "www.x.com"}

# ---------------------------------------------------------------------------
# 開発関連フィルタの既定値
# ---------------------------------------------------------------------------

DEFAULT_FILTER_KEYWORDS = [
    "GitHub", "API", "SDK", "CLI", "OSS", "Claude", "Claude Code", "MCP", "LLM",
    "Python", "TypeScript", "JavaScript", "React", "Next.js", "Cloudflare",
    "Workers", "GAS", "Google Apps Script", "Docker", "SQL",
    "プログラミング", "実装", "開発", "エンジニア", "コード", "リポジトリ",
    "ライブラリ", "フレームワーク", "個人開発", "自動化", "デプロイ", "プロンプト",
]

DEFAULT_FILTER_DOMAINS = [
    "github.com", "gitlab.com", "gist.github.com", "npmjs.com", "pypi.org",
    "huggingface.co", "zenn.dev", "qiita.com", "dev.to", "stackoverflow.com",
    "docs.anthropic.com", "code.claude.com", "developers.cloudflare.com",
    "vercel.com",
]

DEFAULT_FILTER_CONFIG = {
    "enabled": True,
    "keywords": DEFAULT_FILTER_KEYWORDS,
    "domains": DEFAULT_FILTER_DOMAINS,
    "claude_model": "haiku",
    "claude_batch_size": 30,
}

CLAUDE_TIMEOUT_SEC = 180
_ASCII_ONLY_RE = re.compile(r"^[\x00-\x7f]+$")

# X の投稿ID・ユーザーIDは数字のみのsnowflake文字列。ファイル名やクエリに使う前に検証する。
TWEET_ID_RE = re.compile(r"^[0-9]{1,25}$")


def is_valid_tweet_id(value):
    return bool(TWEET_ID_RE.match(str(value or "")))

# ---------------------------------------------------------------------------
# 投稿者本人のリプライ（返信スレッド）取得
# ---------------------------------------------------------------------------

SEARCH_ALL_URL = "https://api.x.com/2/tweets/search/all"
SEARCH_RECENT_URL = "https://api.x.com/2/tweets/search/recent"
REPLY_TWEET_FIELDS = "created_at,author_id,entities,note_tweet,conversation_id,in_reply_to_user_id"
# search/all の max_results 最小値。ノイズ（他人宛て返信）にかかる課金上限を抑えるため
# 実用上必要な件数まで絞る。
SEARCH_ALL_MAX_RESULTS = 10
SEARCH_RECENT_MAX_RESULTS = 10
SEARCH_ALL_RATE_SLEEP_SEC = 1.1
COST_PER_REPLY_SEARCH_USD = 0.005
REPLIES_SECTION_HEADING = "## 投稿者のリプライ"

DEFAULT_REPLIES_CONFIG = {
    "enabled": True,
    "max_posts_per_run": 50,
}

# ---------------------------------------------------------------------------
# 動画・画像（media）
# ---------------------------------------------------------------------------

MEDIA_EXPANSIONS = "attachments.media_keys"
MEDIA_FIELDS = "type,preview_image_url,url,duration_ms,variants"


# ---------------------------------------------------------------------------
# パス関連（テストでは XBM_HOME / XBM_DATA、または引数で差し替え可能）
# ---------------------------------------------------------------------------


def default_home_dir() -> Path:
    env = os.environ.get("XBM_HOME")
    if env:
        return Path(env)
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(profile) / ".x-bookmarks"


def default_data_dir() -> Path:
    env = os.environ.get("XBM_DATA")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "data"


# ---------------------------------------------------------------------------
# HTTP（テストでモックしやすいように小関数に閉じ込める）
# ---------------------------------------------------------------------------


def http_request(method, url, headers=None, data=None, timeout=30):
    """HTTP リクエストを実行する。

    戻り値: (status_code, headers_dict(lower-case key), body_bytes)
    """
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            hdrs = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, hdrs, body
    except urllib.error.HTTPError as e:
        body = e.read()
        hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        return e.code, hdrs, body


# ---------------------------------------------------------------------------
# 設定 / トークンの読み書き
# ---------------------------------------------------------------------------


def load_config(home):
    p = Path(home) / "config.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_config(home, cfg):
    p = Path(home)
    p.mkdir(parents=True, exist_ok=True)
    (p / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_filter_config(cfg):
    """config.json の `filter` セクションを既定値とマージして返す。

    キーが無ければ（既存 config との互換のため）既定値を使う。
    """
    filt = dict(DEFAULT_FILTER_CONFIG)
    user_filt = (cfg or {}).get("filter") or {}
    for key in ("enabled", "keywords", "domains", "claude_model", "claude_batch_size"):
        if key in user_filt:
            filt[key] = user_filt[key]
    return filt


def get_replies_config(cfg):
    """config.json の `replies` セクションを既定値とマージして返す。"""
    conf = dict(DEFAULT_REPLIES_CONFIG)
    user_conf = (cfg or {}).get("replies") or {}
    for key in ("enabled", "max_posts_per_run"):
        if key in user_conf:
            conf[key] = user_conf[key]
    return conf


def load_token(home):
    p = Path(home) / "token.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_token(home, token):
    p = Path(home)
    p.mkdir(parents=True, exist_ok=True)
    (p / "token.json").write_text(
        json.dumps(token, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# YAML / テキスト整形
# ---------------------------------------------------------------------------


def yaml_dquote(s):
    """YAML のダブルクォート文字列として安全にエスケープする。"""
    if s is None:
        s = ""
    s = str(s)
    s = s.replace("\\", "\\\\").replace('"', '\\"')
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\n", "\\n").replace("\t", "\\t")
    return '"' + s + '"'


def replace_tco(text, entities):
    """entities.urls の expanded_url で t.co 短縮URLを置換する。"""
    if not text:
        return text or ""
    if not entities:
        return text
    urls = entities.get("urls") or []
    for u in urls:
        short = u.get("url")
        expanded = u.get("expanded_url")
        if short and expanded:
            text = text.replace(short, expanded)
    return text


def extract_links(entities, tweet_id=None):
    """entities.urls から、その投稿自身への自己参照リンクを除いた expanded_url 一覧を返す。

    除外するのは x.com/twitter.com ドメインかつパスに
    `/status/{tweet_id}`（例: .../status/123, .../status/123/photo/1,
    .../status/123/video/1）を含む URL のみ。他人の投稿・別の投稿への
    リンク（引用ポストなど）は除外しない。
    """
    if not entities:
        return []
    urls = entities.get("urls") or []
    out = []
    seen = set()
    self_status_re = None
    if tweet_id is not None:
        self_status_re = re.compile(rf"/status/{re.escape(str(tweet_id))}(?:/|$)")
    for u in urls:
        expanded = u.get("expanded_url")
        if not expanded:
            continue
        parsed = urllib.parse.urlparse(expanded)
        host = parsed.netloc.lower()
        if self_status_re is not None and host in SELF_LINK_DOMAINS:
            if self_status_re.search(parsed.path):
                continue
        if expanded in seen:
            continue
        seen.add(expanded)
        out.append(expanded)
    return out


def build_body_and_links(tweet):
    """note_tweet 優先で本文を組み立て、t.co 置換とリンク抽出を行う。"""
    entities = tweet.get("entities") or {}
    note = tweet.get("note_tweet")
    if note and note.get("text"):
        raw_text = note["text"]
        text_entities = note.get("entities") or entities
    else:
        raw_text = tweet.get("text", "")
        text_entities = entities
    # X API は本文の & < > を &amp; &lt; &gt; で返すので戻す
    body = html.unescape(replace_tco(raw_text, text_entities))
    links = extract_links(entities, tweet.get("id"))
    return body, links


def _keyword_pattern(keyword):
    """キーワード1件を検索する正規表現を作る。

    英数字のみのキーワード（"API" など）は単語境界で判定し、
    "rapid" のような語の内部に部分一致してしまうのを防ぐ。
    日本語などマルチバイト文字を含むキーワードは単純な部分一致とする。
    """
    if _ASCII_ONLY_RE.match(keyword):
        return re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(keyword) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
    return re.compile(re.escape(keyword), re.IGNORECASE)


def match_filter(body, links, keywords, domains):
    """本文またはリンクが開発関連キーワード/ドメインにマッチするか判定する。

    マッチした場合 "keyword:<マッチ語>" または "domain:<ドメイン>" を返し、
    マッチしなければ None を返す。
    """
    body = body or ""
    for kw in keywords or []:
        if _keyword_pattern(kw).search(body):
            return f"keyword:{kw}"

    for link in links or []:
        host = urllib.parse.urlparse(link).netloc.lower()
        bare_host = host[4:] if host.startswith("www.") else host
        for dom in domains or []:
            dom_l = dom.lower()
            if bare_host == dom_l or bare_host.endswith("." + dom_l):
                return f"domain:{dom_l}"

    return None


def _select_best_mp4_variant(variants):
    """variants の中から content_type=video/mp4 かつ bit_rate 最大のものを返す。"""
    best = None
    best_bitrate = -1
    for v in variants or []:
        if v.get("content_type") != "video/mp4":
            continue
        br = v.get("bit_rate")
        if br is None:
            continue
        if br > best_bitrate:
            best_bitrate = br
            best = v
    return best


def build_media_list(tweet, media_by_key):
    """tweet.attachments.media_keys と includes.media を突き合わせて media 一覧を作る。

    photo: {"type": "photo", "url": ...}
    video/animated_gif: {"type": ..., "preview": ..., "duration_ms": ...,
                          "video_url": <variantsのうちvideo/mp4でbit_rate最大>}
    ファイルのダウンロードは行わない。
    """
    keys = (tweet.get("attachments") or {}).get("media_keys") or []
    out = []
    for k in keys:
        m = (media_by_key or {}).get(k)
        if not m:
            continue
        mtype = m.get("type")
        if mtype == "photo":
            out.append({"type": "photo", "url": m.get("url")})
        elif mtype in ("video", "animated_gif"):
            entry = {"type": mtype}
            if m.get("preview_image_url"):
                entry["preview"] = m.get("preview_image_url")
            if m.get("duration_ms") is not None:
                entry["duration_ms"] = m.get("duration_ms")
            best = _select_best_mp4_variant(m.get("variants"))
            if best and best.get("url"):
                entry["video_url"] = best.get("url")
            out.append(entry)
        else:
            out.append({"type": mtype})
    return out


def _render_media_lines(media):
    lines = ["media:"]
    key_order = ["type", "preview", "duration_ms", "video_url", "url"]
    for m in media:
        first = True
        for k in key_order:
            if k not in m or m[k] is None:
                continue
            v = m[k]
            v_str = yaml_dquote(v) if isinstance(v, str) else str(v)
            prefix = "  - " if first else "    "
            lines.append(f"{prefix}{k}: {v_str}")
            first = False
    return lines


def format_replies_section(replies):
    """投稿者本人のリプライを本文末尾に追記するための Markdown セクションを作る。

    created_at 昇順で渡されている前提。空リストなら空文字列を返す。
    """
    if not replies:
        return ""
    parts = ["", REPLIES_SECTION_HEADING]
    for r in replies:
        body, _links = build_body_and_links(r)
        parts.append("")
        parts.append(f"### {r.get('created_at', '')}")
        parts.append(f"https://x.com/i/status/{r.get('id')}")
        parts.append("")
        parts.append(body)
    return "\n".join(parts) + "\n"


def format_frontmatter(tweet, body, links, fetched_at, filter_label=None,
                        replies_count=None, media=None):
    tid = str(tweet.get("id", ""))
    author_id = str(tweet.get("author_id", ""))
    created_at = tweet.get("created_at", "")
    conversation_id = str(tweet.get("conversation_id") or tid)
    lang = tweet.get("lang", "")

    lines = ["---"]
    lines.append(f"id: {yaml_dquote(tid)}")
    lines.append(f"url: https://x.com/i/status/{tid}")
    lines.append(f"author_id: {yaml_dquote(author_id)}")
    lines.append(f"created_at: {created_at}")
    lines.append(f"conversation_id: {yaml_dquote(conversation_id)}")
    lines.append(f"lang: {lang}")
    if filter_label:
        lines.append(f"filter: {yaml_dquote(filter_label)}")
    if replies_count is not None:
        lines.append(f"replies: {int(replies_count)}")
    lines.append(f"fetched_at: {fetched_at}")
    if links:
        lines.append("links:")
        for link in links:
            lines.append(f"  - {yaml_dquote(link)}")
    else:
        lines.append("links: []")
    if media is not None:
        if media:
            lines.extend(_render_media_lines(media))
        else:
            lines.append("media: []")
    lines.append("---")
    lines.append("")
    lines.append(body)

    return "\n".join(lines) + "\n"


def month_from_created_at(created_at):
    try:
        value = created_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
    except Exception:
        dt = datetime.utcnow()
    return dt.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# 保存 / index.jsonl / usage.log
# ---------------------------------------------------------------------------


def save_tweet(data_dir, tweet, fetched_at, filter_label=None, replies_list=None, media=None):
    """投稿を Markdown として保存する。

    replies_list: None なら「今回リプライは取得していない」＝frontmatterに
    `replies:` を書かない（後日 `xbm.py replies` で後追い可能な状態）。
    空リスト []（取得したが0件）なら `replies: 0` を書く。
    """
    data_dir = Path(data_dir)

    tid_check = str(tweet.get("id", ""))
    if not is_valid_tweet_id(tid_check):
        print(f"警告: 不正な投稿ID '{tid_check}' のため保存をスキップします。")
        return None, None

    body, links = build_body_and_links(tweet)
    text_preview = body[:100]

    if replies_list is not None:
        valid_replies = []
        for r in replies_list:
            rid = str(r.get("id", ""))
            if not is_valid_tweet_id(rid):
                print(f"警告: 不正なリプライID '{rid}' のためスキップします。")
                continue
            valid_replies.append(r)
        replies_list = valid_replies

    if replies_list:
        merged_links = list(links)
        seen = set(links)
        for r in replies_list:
            _, rlinks = build_body_and_links(r)
            for l in rlinks:
                if l not in seen:
                    seen.add(l)
                    merged_links.append(l)
        links = merged_links
        body = body + format_replies_section(replies_list)

    replies_count = len(replies_list) if replies_list is not None else None

    md = format_frontmatter(
        tweet, body, links, fetched_at,
        filter_label=filter_label, replies_count=replies_count, media=media,
    )

    month = month_from_created_at(tweet.get("created_at", ""))
    dirpath = data_dir / "bookmarks" / month
    dirpath.mkdir(parents=True, exist_ok=True)

    tid = str(tweet.get("id", ""))
    path = dirpath / f"{tid}.md"
    path.write_text(md, encoding="utf-8")

    record = {
        "id": tid,
        "created_at": tweet.get("created_at", ""),
        "author_id": str(tweet.get("author_id", "")),
        "text": text_preview,
        "path": str(path.relative_to(data_dir)).replace("\\", "/"),
        "fetched_at": fetched_at,
    }
    if filter_label:
        record["filter"] = filter_label
    if replies_count is not None:
        record["replies"] = replies_count
    return path, record


def load_known_ids(data_dir):
    idx = Path(data_dir) / "index.jsonl"
    known = set()
    if idx.exists():
        with idx.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    known.add(str(rec["id"]))
                except Exception:
                    continue
    return known


def append_index(data_dir, record):
    if record is None:
        return False
    rid = str(record.get("id", ""))
    if not is_valid_tweet_id(rid):
        print(f"警告: 不正な投稿ID '{rid}' のため index.jsonl への記録をスキップします。")
        return False
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    idx = data_dir / "index.jsonl"
    with idx.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return True


def append_usage_log(data_dir, fetched, new, cost_usd, skipped=0, pending=0,
                      reply_count=0, reply_cost_usd=0.0):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "usage.log"
    ts = datetime.now().astimezone().isoformat(timespec="seconds")
    total_cost = cost_usd + reply_cost_usd
    with log_path.open("a", encoding="utf-8") as f:
        f.write(
            f"{ts}\tfetched={fetched}\tnew={new}\tskipped={skipped}\t"
            f"pending={pending}\treplies={reply_count}\t"
            f"reply_cost_usd={reply_cost_usd:.3f}\tcost_usd={total_cost:.3f}\n"
        )


# ---------------------------------------------------------------------------
# 開発非関連の除外 / 判定保留（開発フィルタ）
# ---------------------------------------------------------------------------


def append_skipped(data_dir, record):
    """開発と無関係と判定された投稿を、本文は保存せず id のみ記録する。"""
    rid = str(record.get("id", ""))
    if not is_valid_tweet_id(rid):
        print(f"警告: 不正な投稿ID '{rid}' のため skipped.jsonl への記録をスキップします。")
        return False
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / "skipped.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return True


def load_skipped_ids(data_dir):
    p = Path(data_dir) / "skipped.jsonl"
    ids = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ids.add(str(rec["id"]))
            except Exception:
                continue
    return ids


def append_pending(data_dir, tweet):
    """Claude 判定に失敗した投稿を、次回 sync 冒頭で再判定できるよう退避する。"""
    tid = str(tweet.get("id", ""))
    if not is_valid_tweet_id(tid):
        print(f"警告: 不正な投稿ID '{tid}' のため pending.jsonl への記録をスキップします。")
        return False
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    p = data_dir / "pending.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(tweet, ensure_ascii=False) + "\n")
    return True


def load_pending_tweets(data_dir):
    p = Path(data_dir) / "pending.jsonl"
    tweets = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                tweets.append(json.loads(line))
            except Exception:
                continue
    return tweets


def clear_pending(data_dir):
    p = Path(data_dir) / "pending.jsonl"
    if p.exists():
        p.unlink()


def load_pending_ids(data_dir):
    return {str(t.get("id")) for t in load_pending_tweets(data_dir)}


def load_all_known_ids(data_dir):
    """既知ID = index.jsonl ∪ skipped.jsonl ∪ pending.jsonl。"""
    return (
        load_known_ids(data_dir)
        | load_skipped_ids(data_dir)
        | load_pending_ids(data_dir)
    )


def sleep_seconds(seconds):
    """time.sleep のラッパー（テストでモックしやすいように分離）。"""
    time.sleep(seconds)


def build_search_url(mode, conversation_id, author_id):
    max_results = SEARCH_ALL_MAX_RESULTS if mode == "all" else SEARCH_RECENT_MAX_RESULTS
    query = f"conversation_id:{conversation_id} from:{author_id} is:reply"
    params = {
        "query": query,
        "max_results": str(max_results),
        "tweet.fields": REPLY_TWEET_FIELDS,
    }
    base = SEARCH_ALL_URL if mode == "all" else SEARCH_RECENT_URL
    return base + "?" + urllib.parse.urlencode(params)


def fetch_replies_for_tweet(token, tweet, state):
    """投稿者本人による、指定した投稿へのリプライを検索して取得する。

    state: {"mode": "all"|"recent"} を呼び出し元で使い回すことで、一度
    search/all が使えなかった場合は、以降このsync実行中は search/recent を
    使い続ける（フォールバックの永続化）。

    戻り値: (replies, fetched_count, rate_limited)
      replies: 検索に成功した場合は、投稿自身・ID不正な結果・
        投稿者本人が「投稿者以外の誰か」に宛てて書いたリプライ
        （in_reply_to_user_id != author_id。コメント欄で他人に返した
        リプライ等のノイズ）を除き、created_at 昇順に整列したリプライの
        リスト（0件なら空リスト）。検索に失敗した場合（両エンドポイント
        とも非200・ネットワーク例外・応答が不正なJSON）は None を返し、
        呼び出し元は `replies:` を書かずにこの投稿を後日の再取得対象と
        して残す。
      fetched_count: 検索APIが返した件数（費用集計に使う。投稿自身や
        他人宛てのノイズも含む、返却件数そのもの。課金は返却件数ベース
        のため、フィルタ後の件数ではなくこちらを使う）。
      rate_limited: 429 を受けた場合 True。呼び出し元はこのsync実行内での
        以降のリプライ検索を打ち切ること。
    """
    conversation_id = tweet.get("conversation_id") or tweet.get("id")
    author_id = tweet.get("author_id")
    tid = str(tweet.get("id"))

    mode = state.get("mode", "all")
    used_all = False
    status = None
    headers = {}
    body = None

    try:
        if mode == "all":
            used_all = True
            url = build_search_url("all", conversation_id, author_id)
            status, headers, body = http_request(
                "GET", url, headers={"Authorization": f"Bearer {token['access_token']}"}
            )
            if status in (400, 403):
                # search/all が使えない環境（アクセスレベル不足等）。以降は recent に固定する。
                state["mode"] = "recent"
                mode = "recent"
                url = build_search_url("recent", conversation_id, author_id)
                status, headers, body = http_request(
                    "GET", url, headers={"Authorization": f"Bearer {token['access_token']}"}
                )
        else:
            url = build_search_url("recent", conversation_id, author_id)
            status, headers, body = http_request(
                "GET", url, headers={"Authorization": f"Bearer {token['access_token']}"}
            )
    except Exception as e:
        print(f"警告: リプライ検索中にエラーが発生しました: {e}。この投稿は次回以降に持ち越します。")
        if used_all:
            sleep_seconds(SEARCH_ALL_RATE_SLEEP_SEC)
        return None, 0, False

    if used_all:
        # search/all は 1 req/秒の制限があるため、呼び出し間隔を空ける
        sleep_seconds(SEARCH_ALL_RATE_SLEEP_SEC)

    if status == 429:
        print("警告: リプライ検索がレート制限(429)に達しました。今回の実行での以降のリプライ検索を打ち切ります。")
        return None, 0, True

    if status != 200:
        print(f"警告: リプライ検索に失敗しました (status={status})。この投稿は次回以降に持ち越します。")
        return None, 0, False

    body_text = body.decode("utf-8") if isinstance(body, bytes) else body
    try:
        resp = json.loads(body_text)
    except Exception:
        print("警告: リプライ検索の応答が不正なJSONです。この投稿は次回以降に持ち越します。")
        return None, 0, False

    tweets = resp.get("data", []) or []
    fetched_count = len(tweets)

    author_id_str = str(author_id or "")
    replies = [
        r for r in tweets
        if str(r.get("id")) != tid
        and is_valid_tweet_id(r.get("id"))
        # 投稿者本人が"投稿者自身"に宛てて書いたリプライ（自分のスレッドの続き）のみ残す。
        # コメント欄で第三者に返信したものは in_reply_to_user_id がその第三者になるため除外される。
        and str(r.get("in_reply_to_user_id") or "") == author_id_str
    ]
    replies.sort(key=lambda r: r.get("created_at", ""))
    return replies, fetched_count, False


# ---------------------------------------------------------------------------
# Claude 判定（開発関連フィルタの第2段階）
# ---------------------------------------------------------------------------


def run_claude(prompt, model="haiku"):
    """`claude -p` をサブプロセスで1回呼び出し、テキスト応答（result）を返す。

    - ツールは一切使わせない（--tools ""）
    - 非対話・1ターンで完結（-p / --print）
    - JSON 出力を取得し（--output-format json）、その "result" フィールドを返す
    - プロンプトは stdin 経由で渡す
    - セッションは永続化しない（--no-session-persistence）
    - cwd は一時ディレクトリ（プロジェクトの CLAUDE.md 等の影響を受けないように）

    呼び出しに失敗した場合（claude が無い・タイムアウト・非0終了・JSON不正）は
    例外を送出する。呼び出し元はこれを捕捉し、対象投稿を pending.jsonl に退避する。
    """
    # Windows では claude 終了直後もフォルダが掴まれていて削除に失敗することがあるため無視する
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as cwd:
        proc = subprocess.run(
            [
                "claude",
                "-p",
                "--model", model,
                "--output-format", "json",
                "--tools", "",
                "--permission-prompts", "none",
                "--no-session-persistence",
                # 登録済み MCP のツール定義を読み込むとプロンプト上限を超えるため読み込まない
                "--strict-mcp-config",
            ],
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=cwd,
            timeout=CLAUDE_TIMEOUT_SEC,
        )

    if proc.returncode != 0:
        raise RuntimeError(
            f"claude が異常終了しました (status={proc.returncode}): {proc.stderr[:500]}"
        )

    try:
        envelope = json.loads(proc.stdout)
    except Exception as e:
        raise RuntimeError(f"claude の出力が不正なJSONです: {e}") from e

    result = envelope.get("result")
    if result is None:
        raise RuntimeError("claude のJSON出力に 'result' フィールドがありません")
    return result


def parse_claude_id_list(text):
    """run_claude() の応答テキストから id の配列を取り出す。

    コードフェンス(```json ... ```)で囲まれている場合や、
    {"ids": [...]} 形式で返ってきた場合にも対応する。
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in ("json", ""):
            lines = lines[1:]
        text = "\n".join(lines).strip()

    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("ids", data.get("id", []))
    if not isinstance(data, list):
        raise ValueError("claude の応答がID配列ではありません")
    return [str(x) for x in data]


def build_claude_prompt(tweets):
    items = []
    for t in tweets:
        body, links = build_body_and_links(t)
        items.append({"id": str(t.get("id")), "text": body, "links": links})
    payload = json.dumps(items, ensure_ascii=False)
    return (
        "以下はXのブックマーク投稿のリストです（JSON配列、各要素は id/text/links）。\n"
        "このうち、ソフトウェア開発・プログラミング・AI活用開発・技術ツールに"
        "役立つと判断できる投稿の id だけを含む JSON 配列を1つだけ返してください。\n"
        "説明・前置き・コードブロックのフェンスは不要です。該当が無ければ [] を返してください。\n\n"
        f"投稿一覧:\n{payload}"
    )


def classify_batch_with_claude(tweets, model):
    prompt = build_claude_prompt(tweets)
    raw = run_claude(prompt, model=model)
    return parse_claude_id_list(raw)


def classify_and_persist(tweets, data_dir, filt_cfg, filter_enabled, token=None, replies_ctx=None):
    """未判定の投稿群を、フィルタ設定に従って保存/除外/保留に振り分ける。

    保存が確定した投稿についてのみ（除外・保留分には取りに行かない）、
    replies_ctx が有効かつ予算が残っていれば投稿者本人のリプライを検索して
    本文に追記する。media は各 tweet の `_media`（run_sync が事前に
    includes.media と突き合わせて埋め込んだもの）をそのまま使う。

    戻り値: (saved_count, skipped_count, pending_count)
    """
    data_dir = Path(data_dir)
    saved = 0
    skipped = 0
    pending = 0

    if not tweets:
        return saved, skipped, pending

    def _persist(t, filter_label):
        """保存を試み、実際に保存できたら True を返す（不正IDなら False）。"""
        tid = str(t.get("id", ""))
        if not is_valid_tweet_id(tid):
            print(f"警告: 不正な投稿ID '{tid}' のため保存をスキップします。")
            return False

        fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
        replies_list = None
        if (
            replies_ctx is not None
            and replies_ctx.get("enabled")
            and token is not None
            and replies_ctx.get("remaining_budget", 0) > 0
        ):
            fetched_replies, search_count, rate_limited = fetch_replies_for_tweet(
                token, t, replies_ctx["state"]
            )
            replies_ctx["remaining_budget"] -= 1
            replies_ctx["search_count"] = replies_ctx.get("search_count", 0) + search_count
            if fetched_replies is not None:
                replies_list = fetched_replies
            if rate_limited:
                # 429: 今回の実行では以降のリプライ検索を一切行わない
                replies_ctx["enabled"] = False

        media_list = t.get("_media")
        _path, record = save_tweet(
            data_dir, t, fetched_at,
            filter_label=filter_label,
            replies_list=replies_list,
            media=media_list,
        )
        if record is None:
            return False
        return append_index(data_dir, record)

    if not filter_enabled:
        for t in tweets:
            if _persist(t, None):
                saved += 1
        return saved, skipped, pending

    keywords = filt_cfg.get("keywords", DEFAULT_FILTER_KEYWORDS)
    domains = filt_cfg.get("domains", DEFAULT_FILTER_DOMAINS)
    model = filt_cfg.get("claude_model", "haiku")
    batch_size = int(filt_cfg.get("claude_batch_size", 30)) or 30

    need_claude = []
    for t in tweets:
        body, links = build_body_and_links(t)
        match = match_filter(body, links, keywords, domains)
        if match:
            if _persist(t, match):
                saved += 1
        else:
            need_claude.append(t)

    for i in range(0, len(need_claude), batch_size):
        batch = need_claude[i : i + batch_size]
        try:
            matched_ids = set(classify_batch_with_claude(batch, model))
        except Exception as e:
            print(f"警告: Claude判定に失敗したため保留します（次回sync冒頭で再判定します）: {e}")
            for t in batch:
                if append_pending(data_dir, t):
                    pending += 1
            continue

        for t in batch:
            tid = str(t.get("id"))
            if tid in matched_ids:
                if _persist(t, "claude"):
                    saved += 1
            else:
                fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
                if append_skipped(
                    data_dir,
                    {"id": tid, "reason": "claude", "fetched_at": fetched_at},
                ):
                    skipped += 1

    return saved, skipped, pending


# ---------------------------------------------------------------------------
# OAuth2 PKCE コールバック用ローカルサーバ
# ---------------------------------------------------------------------------


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        self.server.callback_result = qs
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            "<html><body>認証が完了しました。このウィンドウを閉じて構いません。</body></html>".encode(
                "utf-8"
            )
        )

    def log_message(self, format, *args):  # noqa: A002 - シグネチャ踏襲
        pass


# ---------------------------------------------------------------------------
# トークンのリフレッシュ
# ---------------------------------------------------------------------------


def refresh_access_token(home, cfg, token):
    """refresh_token でアクセストークンを更新する。

    戻り値: (new_token_or_None, status_code)
    成功時は新しい refresh_token で必ず上書き保存する（ローテーション対策）。
    """
    body = urllib.parse.urlencode(
        {
            "client_id": cfg["client_id"],
            "grant_type": "refresh_token",
            "refresh_token": token.get("refresh_token"),
        }
    ).encode("ascii")
    status, headers, resp_body = http_request(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )
    if status != 200:
        return None, status

    resp = json.loads(resp_body.decode("utf-8"))
    now = time.time()
    new_token = dict(token)
    new_token["access_token"] = resp["access_token"]
    # レスポンスにない場合は既存値を保持するが、通常は新しい値で必ず上書きされる
    new_token["refresh_token"] = resp.get("refresh_token", token.get("refresh_token"))
    new_token["expires_at"] = now + float(resp.get("expires_in", 7200))
    new_token["token_type"] = resp.get("token_type", token.get("token_type", "bearer"))
    save_token(home, new_token)
    return new_token, status


# ---------------------------------------------------------------------------
# auth サブコマンド
# ---------------------------------------------------------------------------


def cmd_auth(args):
    home = default_home_dir()
    home.mkdir(parents=True, exist_ok=True)

    cfg = load_config(home)
    if cfg is None:
        print("初回設定: X Developer Portal で取得した OAuth 2.0 Client ID を入力してください。")
        client_id = input("Client ID: ").strip()
        redirect_uri = (
            input(f"Redirect URI [{DEFAULT_REDIRECT_URI}]: ").strip()
            or DEFAULT_REDIRECT_URI
        )
        cfg = {"client_id": client_id, "redirect_uri": redirect_uri}
        save_config(home, cfg)

    client_id = cfg["client_id"]
    redirect_uri = cfg.get("redirect_uri", DEFAULT_REDIRECT_URI)

    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .decode("ascii")
        .rstrip("=")
    )
    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)

    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765

    httpd = http.server.HTTPServer((host, port), _CallbackHandler)
    httpd.callback_result = None
    print("ブラウザで X の認証画面を開きます...")
    webbrowser.open(auth_url)
    httpd.handle_request()
    httpd.server_close()

    qs = httpd.callback_result or {}
    if qs.get("error"):
        print(f"認証エラー: {qs.get('error')}")
        return 1

    returned_state = (qs.get("state") or [None])[0]
    code = (qs.get("code") or [None])[0]
    if not code or returned_state != state:
        print("認証コールバックが不正です（state 不一致、または code がありません）。")
        return 1

    token_body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
    ).encode("ascii")
    status, _headers, resp_body = http_request(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=token_body,
    )
    if status != 200:
        print(
            f"トークン取得に失敗しました (status={status}): "
            f"{resp_body.decode('utf-8', 'replace')}"
        )
        return 1

    token_resp = json.loads(resp_body.decode("utf-8"))
    now = time.time()
    token = {
        "access_token": token_resp["access_token"],
        "refresh_token": token_resp.get("refresh_token"),
        "expires_at": now + float(token_resp.get("expires_in", 7200)),
        "token_type": token_resp.get("token_type", "bearer"),
    }

    status2, _headers2, body2 = http_request(
        "GET",
        USERS_ME_URL,
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    if status2 != 200:
        print(f"users/me の取得に失敗しました (status={status2})")
        return 1
    me = json.loads(body2.decode("utf-8"))
    token["user_id"] = me["data"]["id"]

    save_token(home, token)
    print(f"認証に成功しました。user_id={token['user_id']}")
    return 0


# ---------------------------------------------------------------------------
# sync サブコマンド
# ---------------------------------------------------------------------------


def run_sync(cfg, token, home, data_dir, full=False, max_pages=10, page_size=None,
             no_filter=False, no_replies=False):
    """ブックマーク同期の中核処理（テストから直接呼び出し可能）。"""
    now = time.time()
    if token.get("expires_at", 0) - 60 <= now:
        new_token, status = refresh_access_token(home, cfg, token)
        if new_token is None:
            print(
                f"トークンのリフレッシュに失敗しました (status={status})。"
                "`python xbm.py auth` をやり直してください。"
            )
            return 1
        token = new_token

    # --page-size 明示時・--full 時は全ページ同じ件数。差分モードで未指定の場合だけ、
    # 新規が無い日の空振りコストを抑えるため 1ページ目は1件、2ページ目以降は20件にする。
    page_size_explicit = page_size is not None
    if page_size_explicit:
        adaptive_diff_page_size = False
    elif full:
        page_size = 100
        adaptive_diff_page_size = False
    else:
        adaptive_diff_page_size = True

    data_dir = Path(data_dir)
    filt_cfg = get_filter_config(cfg)
    filter_enabled = (not no_filter) and bool(filt_cfg.get("enabled", True))

    replies_cfg = get_replies_config(cfg)
    replies_enabled = (not no_replies) and bool(replies_cfg.get("enabled", True))
    replies_ctx = None
    if replies_enabled:
        replies_ctx = {
            "enabled": True,
            "remaining_budget": int(replies_cfg.get("max_posts_per_run", 50)),
            "state": {"mode": "all"},
            "search_count": 0,
        }

    total_saved = 0
    total_skipped = 0
    total_pending = 0

    # 前回 Claude 判定に失敗して保留になっている投稿を、今回の sync 冒頭で
    # 再判定する（X API を再度叩かず、既に取得済みのデータで判定し直す）。
    pending_tweets = load_pending_tweets(data_dir)
    if pending_tweets:
        clear_pending(data_dir)
        p_saved, p_skipped, p_pending = classify_and_persist(
            pending_tweets, data_dir, filt_cfg, filter_enabled,
            token=token, replies_ctx=replies_ctx,
        )
        total_saved += p_saved
        total_skipped += p_skipped
        total_pending += p_pending

    # 既知ID = index.jsonl ∪ skipped.jsonl ∪ pending.jsonl
    known = load_all_known_ids(data_dir)

    user_id = token.get("user_id")
    url_base = BOOKMARKS_URL_TMPL.format(id=user_id)

    total_fetched = 0
    next_token = None
    refreshed_on_401 = False

    for _page_num in range(max_pages):
        if adaptive_diff_page_size:
            current_page_size = 1 if _page_num == 0 else 20
        else:
            current_page_size = page_size

        params = {
            "tweet.fields": TWEET_FIELDS,
            "max_results": str(current_page_size),
            "expansions": MEDIA_EXPANSIONS,
            "media.fields": MEDIA_FIELDS,
        }
        if next_token:
            params["pagination_token"] = next_token
        url = url_base + "?" + urllib.parse.urlencode(params)

        status, headers, body = http_request(
            "GET", url, headers={"Authorization": f"Bearer {token['access_token']}"}
        )

        if status == 401 and not refreshed_on_401:
            refreshed_on_401 = True
            new_token, _rstatus = refresh_access_token(home, cfg, token)
            if new_token is None:
                print("認証エラー (401)。`python xbm.py auth` をやり直してください。")
                return 1
            token = new_token
            status, headers, body = http_request(
                "GET",
                url,
                headers={"Authorization": f"Bearer {token['access_token']}"},
            )

        if status == 429:
            reset = headers.get("x-rate-limit-reset")
            print(f"レート制限に達しました (429)。x-rate-limit-reset={reset}")
            return 1

        if status != 200:
            body_text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
            print(f"APIエラー (status={status}): {body_text}")
            if status == 401:
                print("`python xbm.py auth` をやり直してください。")
            return 1

        body_text = body.decode("utf-8") if isinstance(body, bytes) else body
        resp = json.loads(body_text)
        tweets = resp.get("data", [])
        total_fetched += len(tweets)

        media_by_key = {
            m.get("media_key"): m
            for m in ((resp.get("includes") or {}).get("media") or [])
        }
        for t in tweets:
            # includes.media は取得時にしか手に入らないため、この時点で
            # 各tweetに解決済みのmedia一覧を埋め込んでおく（pending化されても
            # 失われないように）。
            t["_media"] = build_media_list(t, media_by_key)

        new_in_page = []
        hit_known = False
        for t in tweets:
            tid = str(t.get("id"))
            if tid in known:
                hit_known = True
                if not full:
                    break
                else:
                    continue
            new_in_page.append(t)

        for t in new_in_page:
            known.add(str(t.get("id")))

        page_saved, page_skipped, page_pending = classify_and_persist(
            new_in_page, data_dir, filt_cfg, filter_enabled,
            token=token, replies_ctx=replies_ctx,
        )
        total_saved += page_saved
        total_skipped += page_skipped
        total_pending += page_pending

        next_token = (resp.get("meta") or {}).get("next_token")

        if not full and hit_known:
            break
        if not next_token:
            break

    bookmark_cost = total_fetched * COST_PER_TWEET_USD
    reply_count = replies_ctx["search_count"] if replies_ctx else 0
    reply_cost = reply_count * COST_PER_REPLY_SEARCH_USD
    total_cost = bookmark_cost + reply_cost
    print(
        f"取得リソース数: {total_fetched}件 / 保存数: {total_saved}件 / "
        f"除外数: {total_skipped}件 / 保留数: {total_pending}件 / "
        f"返信取得数: {reply_count}件 / 概算費用: ${total_cost:.3f}"
    )
    append_usage_log(
        data_dir, total_fetched, total_saved, bookmark_cost,
        skipped=total_skipped, pending=total_pending,
        reply_count=reply_count, reply_cost_usd=reply_cost,
    )
    return 0


def cmd_sync(args):
    home = default_home_dir()
    data_dir = default_data_dir()

    cfg = load_config(home)
    token = load_token(home)
    if cfg is None or token is None:
        print("認証情報がありません。先に `python xbm.py auth` を実行してください。")
        return 1

    return run_sync(
        cfg,
        token,
        home,
        data_dir,
        full=args.full,
        max_pages=args.max_pages,
        page_size=args.page_size,
        no_filter=args.no_filter,
        no_replies=args.no_replies,
    )


# ---------------------------------------------------------------------------
# replies サブコマンド（既存保存済み投稿へのリプライ後追い）
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


def _yaml_dunquote(s):
    s = (s or "").strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        inner = s[1:-1]
        inner = (
            inner.replace('\\n', '\n')
            .replace('\\t', '\t')
            .replace('\\"', '"')
            .replace('\\\\', '\\')
        )
        return inner
    return s


def _extract_fm_field(fm_text, key):
    m = re.search(rf"^{re.escape(key)}:\s*(.*)$", fm_text, re.MULTILINE)
    if not m:
        return None
    return _yaml_dunquote(m.group(1))


def _fm_has_key(fm_text, key):
    return re.search(rf"^{re.escape(key)}:", fm_text, re.MULTILINE) is not None


def find_backfill_candidates(data_dir):
    """`replies:` frontmatterが無い保存済みmdを新しい順(created_at降順)に列挙する。"""
    data_dir = Path(data_dir)
    bookmarks_dir = data_dir / "bookmarks"
    candidates = []
    if not bookmarks_dir.exists():
        return candidates

    for path in sorted(bookmarks_dir.glob("*/*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        m = _FRONTMATTER_RE.match(text)
        if not m:
            continue
        fm_text = m.group(1)
        if _fm_has_key(fm_text, "replies"):
            continue

        tid = _extract_fm_field(fm_text, "id")
        author_id = _extract_fm_field(fm_text, "author_id")
        # 既存mdに conversation_id が無い場合は id を conversation_id とみなす
        conversation_id = _extract_fm_field(fm_text, "conversation_id") or tid
        created_at = _extract_fm_field(fm_text, "created_at") or ""

        if not is_valid_tweet_id(tid):
            print(f"警告: 不正な投稿ID '{tid}' のためバックフィル対象から除外します: {path}")
            continue
        if not is_valid_tweet_id(author_id):
            print(f"警告: 不正なauthor_id '{author_id}' のためバックフィル対象から除外します: {path}")
            continue
        if not is_valid_tweet_id(conversation_id):
            print(f"警告: 不正なconversation_id '{conversation_id}' のためバックフィル対象から除外します: {path}")
            continue

        candidates.append({
            "path": path,
            "id": tid,
            "author_id": author_id,
            "conversation_id": conversation_id,
            "created_at": created_at,
        })

    # 新しい順（created_at 降順）。ISO8601形式なので文字列比較で正しく並ぶ。
    candidates.sort(key=lambda c: c["created_at"], reverse=True)
    return candidates


def backfill_replies_into_file(path, replies_list):
    """既存の保存済みmdに `replies: N` と返信本文を後付けする。"""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return False
    fm_text, raw_rest = m.group(1), m.group(2)

    body = raw_rest
    if body.startswith("\n"):
        body = body[1:]
    body = body.rstrip("\n")

    fm_lines = fm_text.split("\n")
    if not any(l.startswith("replies:") for l in fm_lines):
        replies_line = f"replies: {len(replies_list)}"
        insert_idx = None
        for idx, l in enumerate(fm_lines):
            if l.startswith("fetched_at:"):
                insert_idx = idx
                break
        if insert_idx is None:
            fm_lines.append(replies_line)
        else:
            fm_lines.insert(insert_idx, replies_line)

    reply_section = format_replies_section(replies_list)
    new_body = body
    if reply_section:
        new_body = body + "\n" + reply_section.rstrip("\n")

    new_text = "---\n" + "\n".join(fm_lines) + "\n---\n\n" + new_body + "\n"
    path.write_text(new_text, encoding="utf-8")
    return True


def run_replies_backfill(cfg, token, home, data_dir, limit=None):
    """`replies:` が無い保存済み投稿へ、投稿者本人のリプライを後追いで取得する。"""
    data_dir = Path(data_dir)
    replies_cfg = get_replies_config(cfg)
    if limit is None:
        limit = int(replies_cfg.get("max_posts_per_run", 50))

    now = time.time()
    if token.get("expires_at", 0) - 60 <= now:
        new_token, status = refresh_access_token(home, cfg, token)
        if new_token is None:
            print(
                f"トークンのリフレッシュに失敗しました (status={status})。"
                "`python xbm.py auth` をやり直してください。"
            )
            return 1
        token = new_token

    candidates = find_backfill_candidates(data_dir)
    targets = candidates[:limit] if limit is not None else candidates

    state = {"mode": "all"}
    total_search_count = 0
    processed = 0
    for c in targets:
        tweet_stub = {
            "id": c["id"],
            "author_id": c["author_id"],
            "conversation_id": c["conversation_id"],
        }
        replies_list, search_count, rate_limited = fetch_replies_for_tweet(token, tweet_stub, state)
        total_search_count += search_count
        if rate_limited:
            print("警告: レート制限のため、今回の実行での後追い処理を打ち切ります。")
            break
        if replies_list is None:
            # 検索失敗: 何も書き込まず、次回以降のバックフィル対象として残す
            continue
        backfill_replies_into_file(c["path"], replies_list)
        processed += 1

    cost = total_search_count * COST_PER_REPLY_SEARCH_USD
    print(
        f"リプライ後追い: 対象候補 {len(candidates)}件 / 処理数 {processed}件 / "
        f"返信取得数 {total_search_count}件 / 概算費用 ${cost:.3f}"
    )
    append_usage_log(
        data_dir, 0, 0, 0.0,
        reply_count=total_search_count, reply_cost_usd=cost,
    )
    return 0


def cmd_replies(args):
    home = default_home_dir()
    data_dir = default_data_dir()

    cfg = load_config(home)
    token = load_token(home)
    if cfg is None or token is None:
        print("認証情報がありません。先に `python xbm.py auth` を実行してください。")
        return 1

    return run_replies_backfill(cfg, token, home, data_dir, limit=args.limit)


# ---------------------------------------------------------------------------
# clean-replies サブコマンド（他人宛てノイズリプライの後処理クリーンアップ）
# ---------------------------------------------------------------------------


def _split_body_and_reply_section(full_body):
    """本文を「メイン本文」と「'## 投稿者のリプライ' 以降のセクション」に分割する。

    セクションが無ければ (full_body, None) を返す。
    """
    marker = "\n" + REPLIES_SECTION_HEADING
    idx = full_body.find(marker)
    if idx != -1:
        return full_body[:idx], full_body[idx + 1:]
    if full_body.startswith(REPLIES_SECTION_HEADING):
        return "", full_body
    return full_body, None


def _parse_reply_blocks(section_text):
    """'## 投稿者のリプライ' 見出しから始まるセクション本文をブロックに分割する。

    戻り値: [{"date_line": "### ...", "url_line": "https://...", "body": "..."}]
    """
    lines = section_text.split("\n")
    n = len(lines)
    idx = 1  # 0行目は見出し行なのでスキップ
    blocks = []
    while idx < n:
        if not lines[idx].startswith("### "):
            idx += 1
            continue
        date_line = lines[idx]
        url_line = lines[idx + 1] if idx + 1 < n else ""
        body_start = idx + 2
        if body_start < n and lines[body_start] == "":
            body_start += 1
        j = body_start
        while j < n and not lines[j].startswith("### "):
            j += 1
        body_lines = lines[body_start:j]
        while body_lines and body_lines[-1] == "":
            body_lines.pop()
        blocks.append({
            "date_line": date_line,
            "url_line": url_line,
            "body": "\n".join(body_lines),
        })
        idx = j
    return blocks


def clean_replies_in_file(path, dry_run=False):
    """1つのmdファイルから、本文が '@' で始まるリプライブロックを取り除く。

    dry_run=True の場合はファイルを書き換えず、削除される件数だけを返す。
    戻り値: 削除（対象となる）ブロック数（0ならファイルは変更不要）。
    """
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return 0
    fm_text, raw_rest = m.group(1), m.group(2)

    full_body = raw_rest[1:] if raw_rest.startswith("\n") else raw_rest
    full_body = full_body.rstrip("\n")

    main_part, section_part = _split_body_and_reply_section(full_body)
    if section_part is None:
        return 0

    blocks = _parse_reply_blocks(section_part)
    kept = [b for b in blocks if not b["body"].strip().startswith("@")]
    removed = len(blocks) - len(kept)
    if removed <= 0:
        return 0

    if dry_run:
        return removed

    if kept:
        parts = [REPLIES_SECTION_HEADING]
        for b in kept:
            parts.extend(["", b["date_line"], b["url_line"], "", b["body"]])
        new_section = "\n".join(parts)
        new_full_body = main_part + "\n" + new_section
    else:
        new_full_body = main_part.rstrip("\n")

    if re.search(r"^replies:.*$", fm_text, re.MULTILINE):
        new_fm = re.sub(
            r"^replies:.*$", f"replies: {len(kept)}", fm_text, count=1, flags=re.MULTILINE
        )
    else:
        new_fm = fm_text.rstrip("\n") + f"\nreplies: {len(kept)}"

    new_text = "---\n" + new_fm + "\n---\n\n" + new_full_body + "\n"
    path.write_text(new_text, encoding="utf-8")
    return removed


def run_clean_replies(data_dir, dry_run=False):
    """`data/bookmarks` 配下の全mdから、投稿者が他人宛てに書いたリプライ
    （本文が '@' で始まるブロック）を取り除く。

    実行前（--dry-run 以外）に `data/bookmarks` を丸ごと
    `data/backup-<YYYYMMDD-HHMMSS>/bookmarks` へバックアップする。
    """
    data_dir = Path(data_dir)
    bookmarks_dir = data_dir / "bookmarks"
    if not bookmarks_dir.exists():
        print("data/bookmarks が存在しません。何もしません。")
        return 0

    backup_dir = None
    if not dry_run:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = data_dir / f"backup-{ts}" / "bookmarks"
        shutil.copytree(bookmarks_dir, backup_dir)

    changed_files = 0
    removed_blocks_total = 0

    for path in sorted(bookmarks_dir.glob("*/*.md")):
        try:
            removed = clean_replies_in_file(path, dry_run=dry_run)
        except Exception as e:
            print(f"警告: {path} の処理中にエラーが発生しました: {e}")
            continue
        if removed <= 0:
            continue
        removed_blocks_total += removed
        changed_files += 1

    if dry_run:
        print(
            f"[dry-run] 変更対象ファイル数: {changed_files}件 / "
            f"削除対象ブロック数: {removed_blocks_total}件（書き込みは行いません）"
        )
    else:
        print(
            f"clean-replies: 変更ファイル数 {changed_files}件 / "
            f"削除ブロック数 {removed_blocks_total}件 / バックアップ: {backup_dir}"
        )
    return 0


def cmd_clean_replies(args):
    data_dir = default_data_dir()
    return run_clean_replies(data_dir, dry_run=args.dry_run)


# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="xbm.py", description="X (旧Twitter) ブックマークを Markdown として保存する CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="OAuth 2.0 (PKCE) で X に認証する")

    sync_p = sub.add_parser("sync", help="ブックマークを取得してローカルに保存する")
    sync_p.add_argument(
        "--full",
        action="store_true",
        help="既知IDで停止せず、next_token が尽きるまでフル取得する（最新800件まで）",
    )
    sync_p.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="安全弁: 取得する最大ページ数（既定: 10）",
    )
    sync_p.add_argument(
        "--page-size",
        type=int,
        default=None,
        help=(
            "1ページあたりの件数（既定: 差分モードは1ページ目1件/以降20件の可変、"
            "--full モードは全ページ100件。指定時は全ページこの件数で固定）"
        ),
    )
    sync_p.add_argument(
        "--no-filter",
        action="store_true",
        help="開発関連フィルタ（キーワード/ドメイン/Claude判定）を無効にし、全件保存する",
    )
    sync_p.add_argument(
        "--no-replies",
        action="store_true",
        help="保存する投稿についての投稿者本人リプライ取得を無効にする",
    )

    replies_p = sub.add_parser(
        "replies",
        help="frontmatterに replies: が無い既存の保存済み投稿へ、投稿者本人のリプライを後追いで取得する",
    )
    replies_p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="1回の実行で処理する投稿数の上限（既定: config.replies.max_posts_per_run、既定50）",
    )

    clean_p = sub.add_parser(
        "clean-replies",
        help=(
            "保存済みの投稿者リプライのうち、本文が '@' で始まる（他人宛ての）"
            "ブロックを取り除く。実行前に data/bookmarks をバックアップする"
        ),
    )
    clean_p.add_argument(
        "--dry-run",
        action="store_true",
        help="書き込みを行わず、変更対象ファイル数・削除対象ブロック数だけ表示する",
    )

    return parser


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "auth":
        return cmd_auth(args)
    if args.command == "sync":
        return cmd_sync(args)
    if args.command == "replies":
        return cmd_replies(args)
    if args.command == "clean-replies":
        return cmd_clean_replies(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

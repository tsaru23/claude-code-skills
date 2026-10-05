import json
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import xbm  # noqa: E402


def make_tweet(tid, text="hello", author_id="999", created_at="2026-09-01T12:00:00.000Z",
                entities=None, note_tweet=None, lang="ja"):
    tweet = {
        "id": tid,
        "text": text,
        "author_id": author_id,
        "created_at": created_at,
        "lang": lang,
    }
    if entities is not None:
        tweet["entities"] = entities
    if note_tweet is not None:
        tweet["note_tweet"] = note_tweet
    return tweet


def make_response(status, payload):
    body = json.dumps(payload).encode("utf-8")
    return status, {}, body


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.home = self.tmp / "home"
        self.data = self.tmp / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.cfg = {"client_id": "test-client-id", "redirect_uri": "http://127.0.0.1:8765/callback"}
        self.token = {
            "access_token": "AT-initial",
            "refresh_token": "RT-initial",
            "expires_at": time.time() + 3600,
            "token_type": "bearer",
            "user_id": "111",
        }

    def tearDown(self):
        self._tmpdir.cleanup()


class TestDiffModeStop(BaseTestCase):
    def test_stops_at_known_id_and_saves_only_new_ones(self):
        # 既知ID "100" を index.jsonl に登録しておく
        xbm.append_index(self.data, {
            "id": "100", "created_at": "2026-08-01T00:00:00.000Z", "author_id": "1",
            "text": "old", "path": "bookmarks/2026-08/100.md", "fetched_at": "x",
        })

        page1 = {
            "data": [
                make_tweet("103"),
                make_tweet("102"),
                make_tweet("101"),
                make_tweet("100"),  # 既知ID -> ここで停止
                make_tweet("99"),
            ],
            "meta": {"next_token": "should-not-be-used"},
        }

        call_count = {"n": 0}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            call_count["n"] += 1
            return make_response(200, page1)

        with patch.object(xbm, "http_request", side_effect=fake_http_request):
            # このテストはページング/停止ロジックの検証が目的なのでフィルタは無効化する
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10, no_filter=True, no_replies=True)

        self.assertEqual(rc, 0)
        self.assertEqual(call_count["n"], 1, "既知IDに当たったら以降のページは取得しないこと")

        # 103, 102, 101 のみ新規保存され、99 は保存されない
        for tid in ("103", "102", "101"):
            self.assertTrue((self.data / "bookmarks" / "2026-09" / f"{tid}.md").exists())
        self.assertFalse((self.data / "bookmarks" / "2026-09" / "99.md").exists())

        known = xbm.load_known_ids(self.data)
        self.assertIn("103", known)
        self.assertIn("102", known)
        self.assertIn("101", known)
        self.assertNotIn("99", known)

        # usage.log が追記されていること
        usage_log = self.data / "usage.log"
        self.assertTrue(usage_log.exists())
        content = usage_log.read_text(encoding="utf-8")
        self.assertIn("fetched=5", content)
        self.assertIn("new=3", content)
        self.assertIn("cost_usd=0.005", content)


class TestFullModeContinues(BaseTestCase):
    def test_full_mode_ignores_known_id_and_paginates(self):
        xbm.append_index(self.data, {
            "id": "50", "created_at": "2026-08-01T00:00:00.000Z", "author_id": "1",
            "text": "old", "path": "bookmarks/2026-08/50.md", "fetched_at": "x",
        })

        page1 = {
            "data": [make_tweet("60"), make_tweet("50"), make_tweet("40")],
            "meta": {"next_token": "tok2"},
        }
        page2 = {
            "data": [make_tweet("30"), make_tweet("20")],
            "meta": {},
        }
        responses = [make_response(200, page1), make_response(200, page2)]

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            return responses.pop(0)

        with patch.object(xbm, "http_request", side_effect=fake_http_request):
            # このテストはページング/停止ロジックの検証が目的なのでフィルタは無効化する
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=True, max_pages=10, no_filter=True, no_replies=True)

        self.assertEqual(rc, 0)

        # 50 は既知なので保存されないが、60/40/30/20 は保存される（2ページ目まで進む）
        for tid in ("60", "40", "30", "20"):
            self.assertTrue((self.data / "bookmarks" / "2026-09" / f"{tid}.md").exists())
        self.assertFalse((self.data / "bookmarks" / "2026-09" / "50.md").exists())


class TestMarkdownFormatting(BaseTestCase):
    def test_note_tweet_priority_tco_replace_self_link_exclusion_and_yaml_escape(self):
        # tweet id は "77"。自分自身の status(77) へのメディアリンクは除外し、
        # 他人/別投稿(status 12345)への引用ポストリンクは残す。
        entities = {
            "urls": [
                {"url": "https://t.co/abc123", "expanded_url": "https://example.com/article"},
                {"url": "https://t.co/selfmedia", "expanded_url": "https://x.com/someone/status/77/photo/1"},
                {"url": "https://t.co/quotepost", "expanded_url": "https://x.com/other/status/12345"},
            ]
        }
        note_text = (
            'これは "引用符" を含む\n複数行の本文です。 '
            "https://t.co/abc123 参照 https://t.co/selfmedia 引用 https://t.co/quotepost"
        )
        tweet = make_tweet(
            "77",
            text="短い text（使われないはず） https://t.co/abc123",
            entities=entities,
            note_tweet={"text": note_text},
        )

        path, record = xbm.save_tweet(self.data, tweet, fetched_at="2026-09-28T10:00:00+09:00")
        content = path.read_text(encoding="utf-8")

        # frontmatter 部分を抽出
        parts = content.split("---\n")
        # parts[0] == "", parts[1] == frontmatter body, parts[2:] == markdown body
        frontmatter = parts[1]
        body = "---\n".join(parts[2:])

        self.assertIn('id: "77"', frontmatter)
        self.assertIn('author_id: "999"', frontmatter)
        self.assertIn("https://example.com/article", frontmatter)
        # 自分自身(status/77)へのメディアリンクは links に含まれない
        # (frontmatter の url: 行自体は status/77 を含むため、links の行だけを検査する)
        links_section = frontmatter.split("links:", 1)[1]
        self.assertNotIn("status/77", links_section)
        # 他の投稿(引用ポストなど、status/12345)へのリンクは links に残る
        self.assertIn("https://x.com/other/status/12345", frontmatter)

        # note_tweet.text が優先して使われ、t.co はすべて展開されている
        self.assertIn("https://example.com/article", body)
        self.assertIn("https://x.com/someone/status/77/photo/1", body)
        self.assertIn("https://x.com/other/status/12345", body)
        self.assertNotIn("t.co", body)
        self.assertIn('"引用符"', body)  # 本文中のダブルクォートはそのまま
        self.assertIn("複数行の本文です", body)

        # index record の text は先頭100字
        self.assertTrue(record["text"].startswith("これは"))

    def test_extract_links_keeps_other_status_removes_own_status(self):
        entities = {
            "urls": [
                {"url": "https://t.co/a", "expanded_url": "https://x.com/me/status/555/video/1"},
                {"url": "https://t.co/b", "expanded_url": "https://twitter.com/foo/status/999"},
                {"url": "https://t.co/c", "expanded_url": "https://example.com/page"},
            ]
        }
        links = xbm.extract_links(entities, tweet_id="555")
        self.assertNotIn("https://x.com/me/status/555/video/1", links)
        self.assertIn("https://twitter.com/foo/status/999", links)
        self.assertIn("https://example.com/page", links)


class TestRefreshTokenRotation(BaseTestCase):
    def test_refresh_saves_new_refresh_token(self):
        new_token_payload = {
            "access_token": "AT-new",
            "refresh_token": "RT-new-rotated",
            "expires_in": 7200,
            "token_type": "bearer",
        }

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            self.assertEqual(url, xbm.TOKEN_URL)
            self.assertIn(b"grant_type=refresh_token", data)
            self.assertIn(b"RT-initial", data)
            return make_response(200, new_token_payload)

        with patch.object(xbm, "http_request", side_effect=fake_http_request):
            new_token, status = xbm.refresh_access_token(self.home, self.cfg, self.token)

        self.assertEqual(status, 200)
        self.assertEqual(new_token["access_token"], "AT-new")
        self.assertEqual(new_token["refresh_token"], "RT-new-rotated")

        saved = xbm.load_token(self.home)
        self.assertEqual(saved["refresh_token"], "RT-new-rotated")
        self.assertEqual(saved["access_token"], "AT-new")
        self.assertNotEqual(saved["refresh_token"], "RT-initial")


class TestUsageLog(BaseTestCase):
    def test_usage_log_appends_cost_line(self):
        xbm.append_usage_log(self.data, fetched=5, new=3, cost_usd=5 * xbm.COST_PER_TWEET_USD)
        log_path = self.data / "usage.log"
        self.assertTrue(log_path.exists())
        content = log_path.read_text(encoding="utf-8")
        self.assertIn("fetched=5", content)
        self.assertIn("new=3", content)
        self.assertIn("cost_usd=0.005", content)


class TestKeywordDomainFilter(BaseTestCase):
    def test_keyword_word_boundary_and_domain_match(self):
        filt = xbm.get_filter_config(self.cfg)

        # "GitHub" を含む本文はキーワードマッチ
        m1 = xbm.match_filter("GitHubでOSSを公開しました", [], filt["keywords"], filt["domains"])
        self.assertEqual(m1, "keyword:GitHub")

        # "rapid" は "API" に誤爆しない（単語境界判定）
        m2 = xbm.match_filter("rapid transit is convenient", [], filt["keywords"], filt["domains"])
        self.assertIsNone(m2)

        # "API" 単体は正しくマッチする
        m3 = xbm.match_filter("We released a new API today", [], filt["keywords"], filt["domains"])
        self.assertEqual(m3, "keyword:API")

        # ドメインマッチ（github.com）
        m4 = xbm.match_filter("見て", ["https://github.com/foo/bar"], filt["keywords"], filt["domains"])
        self.assertEqual(m4, "domain:github.com")

        # ドメインマッチ（zenn.dev。サブドメイン重複のない独立ドメインで確認）
        m5 = xbm.match_filter("見て", ["https://zenn.dev/articles/foo"], filt["keywords"], filt["domains"])
        self.assertEqual(m5, "domain:zenn.dev")

        # 開発と無関係な本文・リンクはどちらにもマッチしない
        m6 = xbm.match_filter("今日は良い天気でした", ["https://example.com/weather"], filt["keywords"], filt["domains"])
        self.assertIsNone(m6)

    def test_run_sync_saves_keyword_matched_tweet_with_filter_frontmatter(self):
        tweet = make_tweet("801", text="GitHubに新しいリポジトリをpushした")
        page = {"data": [tweet], "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            return make_response(200, page)

        def fail_if_called(prompt, model="haiku"):
            raise AssertionError("キーワードマッチした投稿では run_claude が呼ばれてはいけない")

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "run_claude", side_effect=fail_if_called):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10, no_replies=True)

        self.assertEqual(rc, 0)
        path = self.data / "bookmarks" / "2026-09" / "801.md"
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertIn('filter: "keyword:GitHub"', content)


class TestClaudeFilterFlow(BaseTestCase):
    def test_unmatched_tweets_go_to_claude_and_only_matched_ids_are_saved(self):
        tweets = [
            make_tweet("201", text="今日は良い天気でした"),   # 開発と無関係 -> claude判定へ
            make_tweet("202", text="猫がかわいい"),           # 開発と無関係 -> claude判定へ
            make_tweet("203", text="GitHubにpushした"),       # キーワードマッチ -> claudeに渡らない
        ]
        page = {"data": tweets, "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            return make_response(200, page)

        captured_calls = []

        def fake_run_claude(prompt, model="haiku"):
            captured_calls.append((prompt, model))
            # 201のみ該当と判定させる
            return json.dumps(["201"])

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "run_claude", side_effect=fake_run_claude):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10, no_replies=True)

        self.assertEqual(rc, 0)
        self.assertEqual(len(captured_calls), 1)
        prompt_text, model = captured_calls[0]
        self.assertEqual(model, "haiku")
        # claude には未マッチの2件(201, 202)だけが渡り、203(キーワードマッチ済み)は渡らない
        self.assertIn('"201"', prompt_text)
        self.assertIn('"202"', prompt_text)
        self.assertNotIn('"203"', prompt_text)

        # 201: claude判定で該当 -> 保存される
        self.assertTrue((self.data / "bookmarks" / "2026-09" / "201.md").exists())
        # 203: キーワードマッチで保存される
        self.assertTrue((self.data / "bookmarks" / "2026-09" / "203.md").exists())
        # 202: 非該当 -> 保存されない
        self.assertFalse((self.data / "bookmarks" / "2026-09" / "202.md").exists())

        # skipped.jsonl には id のみ記録され、本文は含まれない
        skipped_path = self.data / "skipped.jsonl"
        self.assertTrue(skipped_path.exists())
        skipped_records = [
            json.loads(line) for line in skipped_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        ids = {r["id"] for r in skipped_records}
        self.assertIn("202", ids)
        for r in skipped_records:
            self.assertEqual(set(r.keys()), {"id", "reason", "fetched_at"})
            self.assertEqual(r["reason"], "claude")


class TestPendingRetry(BaseTestCase):
    def test_claude_failure_defers_to_pending_and_is_retried_next_sync(self):
        tweet = make_tweet("301", text="今日はいい天気でしたね")
        page = {"data": [tweet], "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            return make_response(200, page)

        def failing_run_claude(prompt, model="haiku"):
            raise RuntimeError("claude コマンドが見つかりません")

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "run_claude", side_effect=failing_run_claude):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10, no_replies=True)

        self.assertEqual(rc, 0)
        # 保存も破棄もされない
        self.assertFalse((self.data / "bookmarks" / "2026-09" / "301.md").exists())
        skipped_path = self.data / "skipped.jsonl"
        if skipped_path.exists():
            skipped_ids = {json.loads(l)["id"] for l in skipped_path.read_text(encoding="utf-8").splitlines() if l.strip()}
            self.assertNotIn("301", skipped_ids)

        pending_path = self.data / "pending.jsonl"
        self.assertTrue(pending_path.exists())
        pending_tweets = [
            json.loads(line) for line in pending_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.assertEqual(len(pending_tweets), 1)
        self.assertEqual(str(pending_tweets[0]["id"]), "301")

        # 次回 sync: API から新規に何も返らなくても、冒頭で pending が再判定され、
        # 今度は成功して保存される（API再取得は発生しない）
        def fake_http_request_empty(method, url, headers=None, data=None, timeout=30):
            return make_response(200, {"data": [], "meta": {}})

        def succeeding_run_claude(prompt, model="haiku"):
            return json.dumps(["301"])

        with patch.object(xbm, "http_request", side_effect=fake_http_request_empty), \
             patch.object(xbm, "run_claude", side_effect=succeeding_run_claude):
            rc2 = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10, no_replies=True)

        self.assertEqual(rc2, 0)
        self.assertTrue((self.data / "bookmarks" / "2026-09" / "301.md").exists())
        if pending_path.exists():
            self.assertEqual(pending_path.read_text(encoding="utf-8").strip(), "")


class TestDiffModeStopsOnSkippedOrPending(BaseTestCase):
    def test_diff_mode_stops_when_page_contains_skipped_or_pending_id(self):
        # skipped.jsonl に "500" を、pending.jsonl に "600" を事前登録しておく
        xbm.append_skipped(self.data, {"id": "500", "reason": "claude", "fetched_at": "x"})
        xbm.append_pending(self.data, make_tweet("600", text="保留中の投稿"))

        page = {
            "data": [
                make_tweet("503", text="GitHubの新機能を試した"),
                make_tweet("502", text="GitHubの新機能について書いた"),
                make_tweet("500", text="dummy"),  # skipped済みID -> ここで停止
            ],
            "meta": {"next_token": "unused"},
        }
        call_count = {"n": 0}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            call_count["n"] += 1
            return make_response(200, page)

        def fake_run_claude(prompt, model="haiku"):
            # pending("600")の再判定用。非該当とする
            return json.dumps([])

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "run_claude", side_effect=fake_run_claude):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10, no_replies=True)

        self.assertEqual(rc, 0)
        self.assertEqual(call_count["n"], 1, "skipped/pendingのIDに当たったら以降のページは取得しないこと")

        # 503, 502 はキーワードマッチで新規保存される
        self.assertTrue((self.data / "bookmarks" / "2026-09" / "503.md").exists())
        self.assertTrue((self.data / "bookmarks" / "2026-09" / "502.md").exists())
        # 600(pending) は再判定の結果非該当 -> 保存されない
        self.assertFalse((self.data / "bookmarks" / "2026-09" / "600.md").exists())
        # pending.jsonl は再判定処理により空になっている
        pending_path = self.data / "pending.jsonl"
        if pending_path.exists():
            self.assertEqual(pending_path.read_text(encoding="utf-8").strip(), "")


class TestNoFilterFlag(BaseTestCase):
    def test_no_filter_saves_everything_without_calling_claude(self):
        tweets = [make_tweet("701", text="猫のかわいい写真"), make_tweet("702", text="今日のランチ")]
        page = {"data": tweets, "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            return make_response(200, page)

        def fail_if_called(prompt, model="haiku"):
            raise AssertionError("--no-filter 時に run_claude が呼ばれてはいけない")

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "run_claude", side_effect=fail_if_called):
            rc = xbm.run_sync(
                self.cfg, self.token, self.home, self.data,
                full=False, max_pages=10, no_filter=True, no_replies=True,
            )

        self.assertEqual(rc, 0)
        self.assertTrue((self.data / "bookmarks" / "2026-09" / "701.md").exists())
        self.assertTrue((self.data / "bookmarks" / "2026-09" / "702.md").exists())
        # --no-filter では filter: 行は付かない
        content701 = (self.data / "bookmarks" / "2026-09" / "701.md").read_text(encoding="utf-8")
        self.assertNotIn("filter:", content701)


class TestReplyFetchOnlyForSaved(BaseTestCase):
    def test_search_called_only_for_saved_tweets(self):
        tweets = [
            make_tweet("901", text="GitHubにリポジトリを公開した"),   # キーワードマッチ -> 保存 -> 検索対象
            make_tweet("902", text="今日は良い天気でした"),           # 非該当 -> 除外 -> 検索対象外
        ]
        bookmarks_page = {"data": tweets, "meta": {}}
        search_calls = []

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                return make_response(200, bookmarks_page)
            if url.startswith(xbm.SEARCH_ALL_URL) or url.startswith(xbm.SEARCH_RECENT_URL):
                search_calls.append(url)
                return make_response(200, {"data": [], "meta": {}})
            raise AssertionError(f"unexpected url: {url}")

        def fake_run_claude(prompt, model="haiku"):
            return json.dumps([])  # 902 は非該当

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "run_claude", side_effect=fake_run_claude), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        self.assertEqual(len(search_calls), 1, "保存された投稿(901)についてのみ検索されること")
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(search_calls[0]).query)
        self.assertIn("901", qs["query"][0])
        self.assertNotIn("902", qs["query"][0])


class TestReplyAppendedToMarkdown(BaseTestCase):
    def test_replies_appended_ascending_self_excluded_links_merged_and_count_set(self):
        tweet = make_tweet(
            "1001",
            text="GitHubで新しいツールを作った https://t.co/orig",
            entities={"urls": [{"url": "https://t.co/orig", "expanded_url": "https://example.com/original"}]},
        )
        bookmarks_page = {"data": [tweet], "meta": {}}

        reply_new = {
            "id": "1003", "author_id": "999", "created_at": "2026-09-02T10:00:00.000Z",
            "text": "続報です https://t.co/r2", "conversation_id": "1001",
            "in_reply_to_user_id": "999",
            "entities": {"urls": [{"url": "https://t.co/r2", "expanded_url": "https://example.com/followup"}]},
        }
        reply_old = {
            "id": "1002", "author_id": "999", "created_at": "2026-09-01T13:00:00.000Z",
            "text": "補足です", "conversation_id": "1001", "in_reply_to_user_id": "999",
        }
        self_tweet_in_results = dict(tweet)  # 検索結果に投稿自身が混ざるケースも模す
        search_results = {"data": [reply_new, self_tweet_in_results, reply_old], "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                return make_response(200, bookmarks_page)
            return make_response(200, search_results)

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        path = self.data / "bookmarks" / "2026-09" / "1001.md"
        content = path.read_text(encoding="utf-8")

        self.assertIn("replies: 2", content)
        # created_at 昇順（古い方の1002が先、新しい方の1003が後）で出現する
        idx_1002 = content.index("補足です")
        idx_1003 = content.index("続報です")
        self.assertLess(idx_1002, idx_1003)
        # 投稿自身(1001)はリプライ本文として重複掲載されない
        self.assertEqual(content.count("### 2026-09-01T12:00:00.000Z"), 0)
        # リプライ内のリンクが frontmatter の links にマージされる
        self.assertIn("https://example.com/followup", content)
        self.assertIn("https://example.com/original", content)


class TestSearchAllFallbackToRecent(BaseTestCase):
    def test_falls_back_to_recent_after_403_and_stays_on_recent(self):
        tweets = [
            make_tweet("1101", text="GitHubで公開1"),
            make_tweet("1102", text="GitHubで公開2"),
        ]
        bookmarks_page = {"data": tweets, "meta": {}}
        all_calls = []
        recent_calls = []

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                return make_response(200, bookmarks_page)
            if url.startswith(xbm.SEARCH_ALL_URL):
                all_calls.append(url)
                return 403, {}, json.dumps({"title": "not authorized"}).encode("utf-8")
            if url.startswith(xbm.SEARCH_RECENT_URL):
                recent_calls.append(url)
                return make_response(200, {"data": [], "meta": {}})
            raise AssertionError(f"unexpected url: {url}")

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        # search/all は最初の1回だけ試され、403を受けた後は以降このsync実行中ずっと recent に固定される
        self.assertEqual(len(all_calls), 1)
        self.assertEqual(len(recent_calls), 2)  # 1件目のフォールバック分 + 2件目(最初からrecent)


class TestRepliesBackfillCommand(BaseTestCase):
    def test_backfill_processes_only_missing_replies_newest_first_and_respects_limit(self):
        # 新しい順の候補（replies: 無し）
        t_new = make_tweet("2003", text="新しい投稿", created_at="2026-09-03T00:00:00.000Z")
        t_mid = make_tweet("2002", text="中間の投稿", created_at="2026-09-02T00:00:00.000Z")
        xbm.save_tweet(self.data, t_new, fetched_at="x")
        xbm.save_tweet(self.data, t_mid, fetched_at="x")

        # 既に replies: 済みのファイル（対象外になるはず）
        t_done = make_tweet("2004", text="処理済み投稿", created_at="2026-09-04T00:00:00.000Z")
        xbm.save_tweet(self.data, t_done, fetched_at="x", replies_list=[])

        # conversation_id が無い旧形式ファイル（最も古い）
        old_dir = self.data / "bookmarks" / "2026-08"
        old_dir.mkdir(parents=True, exist_ok=True)
        old_content = (
            '---\n'
            'id: "2001"\n'
            'url: https://x.com/i/status/2001\n'
            'author_id: "111"\n'
            'created_at: 2026-08-01T00:00:00.000Z\n'
            'lang: ja\n'
            'fetched_at: 2026-08-01T00:00:00+09:00\n'
            'links: []\n'
            '---\n'
            '\n'
            '古い形式の本文です\n'
        )
        (old_dir / "2001.md").write_text(old_content, encoding="utf-8")

        search_queries = []

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            search_queries.append(qs.get("query", [""])[0])
            return make_response(200, {"data": [], "meta": {}})

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_replies_backfill(self.cfg, self.token, self.home, self.data, limit=2)

        self.assertEqual(rc, 0)
        # 上限2件: 新しい順で 2003, 2002 のみ処理される（2001は次回以降に回る）
        self.assertEqual(len(search_queries), 2)
        self.assertIn("conversation_id:2003", search_queries[0])
        self.assertIn("conversation_id:2002", search_queries[1])

        content_2003 = (self.data / "bookmarks" / "2026-09" / "2003.md").read_text(encoding="utf-8")
        self.assertIn("replies: 0", content_2003)
        content_2002 = (self.data / "bookmarks" / "2026-09" / "2002.md").read_text(encoding="utf-8")
        self.assertIn("replies: 0", content_2002)

        # 2001（旧形式・conversation_id無し）はまだ未処理のまま残る
        content_2001 = (old_dir / "2001.md").read_text(encoding="utf-8")
        self.assertNotIn("replies:", content_2001)

        # 既に replies: 済みの2004は検索対象外
        self.assertFalse(any("2004" in q for q in search_queries))

        # 2回目の実行: 残りの2001が処理され、conversation_id が無くても id で検索できる
        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc2 = xbm.run_replies_backfill(self.cfg, self.token, self.home, self.data, limit=10)

        self.assertEqual(rc2, 0)
        self.assertIn("conversation_id:2001", search_queries[-1])
        content_2001_after = old_dir.joinpath("2001.md").read_text(encoding="utf-8")
        self.assertIn("replies: 0", content_2001_after)


class TestMediaFrontmatter(BaseTestCase):
    def test_build_media_list_selects_highest_bitrate_mp4_and_handles_photo(self):
        tweet_video = {"id": "3001", "attachments": {"media_keys": ["m1"]}}
        media_by_key = {
            "m1": {
                "media_key": "m1",
                "type": "video",
                "preview_image_url": "https://pbs.twimg.com/preview1.jpg",
                "duration_ms": 12345,
                "variants": [
                    {"content_type": "video/mp4", "bit_rate": 832000, "url": "https://video.twimg.com/low.mp4"},
                    {"content_type": "video/mp4", "bit_rate": 2176000, "url": "https://video.twimg.com/high.mp4"},
                    {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/playlist.m3u8"},
                ],
            }
        }
        media = xbm.build_media_list(tweet_video, media_by_key)
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["type"], "video")
        self.assertEqual(media[0]["video_url"], "https://video.twimg.com/high.mp4")
        self.assertEqual(media[0]["duration_ms"], 12345)

        tweet_photo = {"id": "3002", "attachments": {"media_keys": ["m2"]}}
        media_by_key2 = {"m2": {"media_key": "m2", "type": "photo", "url": "https://pbs.twimg.com/photo1.jpg"}}
        media2 = xbm.build_media_list(tweet_photo, media_by_key2)
        self.assertEqual(media2, [{"type": "photo", "url": "https://pbs.twimg.com/photo1.jpg"}])

        tweet_none = {"id": "3003"}
        self.assertEqual(xbm.build_media_list(tweet_none, {}), [])

    def test_run_sync_embeds_media_into_frontmatter(self):
        tweet = make_tweet("3101", text="GitHubで動画を公開")
        tweet["attachments"] = {"media_keys": ["mkey1"]}
        bookmarks_page = {
            "data": [tweet],
            "includes": {
                "media": [
                    {
                        "media_key": "mkey1",
                        "type": "video",
                        "preview_image_url": "https://pbs.twimg.com/preview.jpg",
                        "duration_ms": 9999,
                        "variants": [
                            {"content_type": "video/mp4", "bit_rate": 500000, "url": "https://video.twimg.com/a.mp4"},
                            {"content_type": "video/mp4", "bit_rate": 1500000, "url": "https://video.twimg.com/b.mp4"},
                        ],
                    }
                ]
            },
            "meta": {},
        }

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                self.assertIn("expansions=", url)
                return make_response(200, bookmarks_page)
            return make_response(200, {"data": [], "meta": {}})

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        content = (self.data / "bookmarks" / "2026-09" / "3101.md").read_text(encoding="utf-8")
        self.assertIn('type: "video"', content)
        self.assertIn('video_url: "https://video.twimg.com/b.mp4"', content)
        self.assertIn("duration_ms: 9999", content)

    def test_media_empty_list_when_no_attachments(self):
        tweet = make_tweet("3201", text="GitHubで記事を書いた")
        bookmarks_page = {"data": [tweet], "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                return make_response(200, bookmarks_page)
            return make_response(200, {"data": [], "meta": {}})

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        content = (self.data / "bookmarks" / "2026-09" / "3201.md").read_text(encoding="utf-8")
        self.assertIn("media: []", content)


class TestReplyCostInUsageLog(BaseTestCase):
    def test_usage_log_includes_reply_count_and_cost(self):
        tweet = make_tweet("4001", text="GitHubで新規公開")
        bookmarks_page = {"data": [tweet], "meta": {}}
        search_result = {
            "data": [
                {"id": "4002", "author_id": "999", "created_at": "2026-09-01T13:00:00.000Z", "text": "リプライ1"},
                {"id": "4003", "author_id": "999", "created_at": "2026-09-01T14:00:00.000Z", "text": "リプライ2"},
            ],
            "meta": {},
        }

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                return make_response(200, bookmarks_page)
            return make_response(200, search_result)

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        content = (self.data / "usage.log").read_text(encoding="utf-8")
        self.assertIn("replies=2", content)
        # 2件 x $0.005 = $0.010 が reply_cost_usd として計上される
        self.assertIn("reply_cost_usd=0.010", content)


class TestReplySearchFailureHandling(BaseTestCase):
    def test_failed_search_does_not_write_replies_key_and_remains_backfill_candidate(self):
        tweet = make_tweet("7001", text="GitHubで公開した投稿")
        bookmarks_page = {"data": [tweet], "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                return make_response(200, bookmarks_page)
            # 検索は(all/recentどちらでも)失敗させる
            return 500, {}, b'{"title": "internal error"}'

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        path = self.data / "bookmarks" / "2026-09" / "7001.md"
        self.assertTrue(path.exists())
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("replies:", content)
        self.assertNotIn("投稿者のリプライ", content)

        # replies: が無いので後日のバックフィル対象として残る
        candidates = xbm.find_backfill_candidates(self.data)
        ids = {c["id"] for c in candidates}
        self.assertIn("7001", ids)

    def test_429_stops_remaining_reply_searches_this_run(self):
        tweets = [
            make_tweet("7101", text="GitHubで公開1"),
            make_tweet("7102", text="GitHubで公開2"),
        ]
        bookmarks_page = {"data": tweets, "meta": {}}
        search_call_count = {"n": 0}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                return make_response(200, bookmarks_page)
            search_call_count["n"] += 1
            return 429, {"x-rate-limit-reset": "9999999999"}, b'{"title": "rate limited"}'

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        # 1件目の検索で429 -> このsync実行での以降のリプライ検索は一切行われない
        self.assertEqual(search_call_count["n"], 1)

        content_7101 = (self.data / "bookmarks" / "2026-09" / "7101.md").read_text(encoding="utf-8")
        content_7102 = (self.data / "bookmarks" / "2026-09" / "7102.md").read_text(encoding="utf-8")
        self.assertNotIn("replies:", content_7101)
        self.assertNotIn("replies:", content_7102)


class TestTweetIdValidation(BaseTestCase):
    def test_save_tweet_rejects_invalid_id(self):
        bad_tweet = make_tweet("12a3", text="不正なID")
        path, record = xbm.save_tweet(self.data, bad_tweet, fetched_at="x")
        self.assertIsNone(path)
        self.assertIsNone(record)
        bookmarks_dir = self.data / "bookmarks"
        if bookmarks_dir.exists():
            self.assertEqual(list(bookmarks_dir.rglob("12a3.md")), [])

    def test_append_functions_reject_invalid_id(self):
        ok1 = xbm.append_index(self.data, {
            "id": "abc", "created_at": "x", "author_id": "1",
            "text": "t", "path": "p", "fetched_at": "x",
        })
        self.assertFalse(ok1)
        self.assertFalse((self.data / "index.jsonl").exists())

        ok2 = xbm.append_skipped(self.data, {"id": "12-34", "reason": "claude", "fetched_at": "x"})
        self.assertFalse(ok2)
        self.assertFalse((self.data / "skipped.jsonl").exists())

        ok3 = xbm.append_pending(self.data, make_tweet("xyz", text="t"))
        self.assertFalse(ok3)
        self.assertFalse((self.data / "pending.jsonl").exists())

        # 正常なIDは通常どおり書き込まれる
        ok4 = xbm.append_index(self.data, {
            "id": "123", "created_at": "x", "author_id": "1",
            "text": "t", "path": "p", "fetched_at": "x",
        })
        self.assertTrue(ok4)
        self.assertTrue((self.data / "index.jsonl").exists())

    def test_backfill_skips_md_with_invalid_conversation_id(self):
        bad_dir = self.data / "bookmarks" / "2026-09"
        bad_dir.mkdir(parents=True, exist_ok=True)
        content = (
            '---\n'
            'id: "9001"\n'
            'url: https://x.com/i/status/9001\n'
            'author_id: "111"\n'
            'created_at: 2026-09-01T00:00:00.000Z\n'
            'conversation_id: "not-a-number"\n'
            'lang: ja\n'
            'fetched_at: 2026-09-01T00:00:00+09:00\n'
            'links: []\n'
            '---\n'
            '\n'
            '本文です\n'
        )
        (bad_dir / "9001.md").write_text(content, encoding="utf-8")

        candidates = xbm.find_backfill_candidates(self.data)
        self.assertEqual(candidates, [])

    def test_run_sync_skips_tweet_with_invalid_id(self):
        tweets = [
            make_tweet("12a3", text="GitHubの不正ID投稿"),
            make_tweet("6002", text="GitHubの正常投稿"),
        ]
        page = {"data": tweets, "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            return make_response(200, page)

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10, no_replies=True)

        self.assertEqual(rc, 0)
        self.assertFalse((self.data / "bookmarks" / "2026-09" / "12a3.md").exists())
        self.assertTrue((self.data / "bookmarks" / "2026-09" / "6002.md").exists())
        known = xbm.load_known_ids(self.data)
        self.assertNotIn("12a3", known)
        self.assertIn("6002", known)


class TestAdaptivePageSize(BaseTestCase):
    def test_diff_mode_uses_1_then_20_when_page_size_unspecified(self):
        page1 = {"data": [make_tweet("5001", text="今日は天気")], "meta": {"next_token": "tok2"}}
        page2 = {"data": [], "meta": {}}
        responses = [make_response(200, page1), make_response(200, page2)]
        captured_max_results = []

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                captured_max_results.append(qs.get("max_results", [None])[0])
                return responses.pop(0)
            return make_response(200, {"data": [], "meta": {}})

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(
                self.cfg, self.token, self.home, self.data,
                full=False, max_pages=10, no_filter=True, no_replies=True,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(captured_max_results, ["1", "20"])

    def test_diff_mode_stops_immediately_when_first_item_is_known(self):
        xbm.append_index(self.data, {
            "id": "5100", "created_at": "2026-08-01T00:00:00.000Z", "author_id": "1",
            "text": "old", "path": "bookmarks/2026-08/5100.md", "fetched_at": "x",
        })
        page1 = {"data": [make_tweet("5100")], "meta": {"next_token": "should-not-be-used"}}
        call_count = {"n": 0}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            call_count["n"] += 1
            return make_response(200, page1)

        with patch.object(xbm, "http_request", side_effect=fake_http_request):
            rc = xbm.run_sync(
                self.cfg, self.token, self.home, self.data,
                full=False, max_pages=10, no_filter=True, no_replies=True,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(call_count["n"], 1, "1件目(max_results=1)が既知ならそこで停止すること")

    def test_explicit_page_size_used_uniformly_across_pages(self):
        page1 = {"data": [make_tweet("5201", text="今日は天気")], "meta": {"next_token": "tok2"}}
        page2 = {"data": [], "meta": {}}
        responses = [make_response(200, page1), make_response(200, page2)]
        captured_max_results = []

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                captured_max_results.append(qs.get("max_results", [None])[0])
                return responses.pop(0)
            return make_response(200, {"data": [], "meta": {}})

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(
                self.cfg, self.token, self.home, self.data,
                full=False, max_pages=10, page_size=7, no_filter=True, no_replies=True,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(captured_max_results, ["7", "7"])

    def test_full_mode_default_page_size_uniform_100(self):
        page1 = {"data": [make_tweet("5301", text="今日は天気")], "meta": {"next_token": "tok2"}}
        page2 = {"data": [], "meta": {}}
        responses = [make_response(200, page1), make_response(200, page2)]
        captured_max_results = []

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                captured_max_results.append(qs.get("max_results", [None])[0])
                return responses.pop(0)
            return make_response(200, {"data": [], "meta": {}})

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(
                self.cfg, self.token, self.home, self.data,
                full=True, max_pages=10, no_filter=True, no_replies=True,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(captured_max_results, ["100", "100"])


class TestReplyExcludesRepliesToOthers(BaseTestCase):
    def test_reply_to_third_party_is_filtered_out(self):
        tweet = make_tweet("1201", text="GitHubで新しいツールを作った")
        bookmarks_page = {"data": [tweet], "meta": {}}

        reply_to_self_thread = {
            "id": "1202", "author_id": "999", "created_at": "2026-09-01T13:00:00.000Z",
            "text": "続報です", "conversation_id": "1201", "in_reply_to_user_id": "999",
        }
        reply_to_other_user = {
            "id": "1203", "author_id": "999", "created_at": "2026-09-01T14:00:00.000Z",
            "text": "@someone DMでお送りしました", "conversation_id": "1201",
            "in_reply_to_user_id": "555",  # 投稿者自身ではなく第三者への返信 -> 除外対象
        }
        search_results = {"data": [reply_to_self_thread, reply_to_other_user], "meta": {}}

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            if "/bookmarks" in url:
                return make_response(200, bookmarks_page)
            return make_response(200, search_results)

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            rc = xbm.run_sync(self.cfg, self.token, self.home, self.data, full=False, max_pages=10)

        self.assertEqual(rc, 0)
        content = (self.data / "bookmarks" / "2026-09" / "1201.md").read_text(encoding="utf-8")
        # 投稿者自身への（スレッドの続きの）リプライだけが残る
        self.assertIn("replies: 1", content)
        self.assertIn("続報です", content)
        self.assertNotIn("DMでお送りしました", content)

    def test_fetch_replies_for_tweet_filters_by_in_reply_to_user_id(self):
        tweet = {"id": "1301", "author_id": "999", "conversation_id": "1301"}
        search_results = {
            "data": [
                {"id": "1302", "created_at": "2026-09-01T13:00:00.000Z",
                 "in_reply_to_user_id": "999", "text": "自分の続き"},
                {"id": "1303", "created_at": "2026-09-01T14:00:00.000Z",
                 "in_reply_to_user_id": "777", "text": "他人への返信"},
            ],
            "meta": {},
        }

        def fake_http_request(method, url, headers=None, data=None, timeout=30):
            return make_response(200, search_results)

        with patch.object(xbm, "http_request", side_effect=fake_http_request), \
             patch.object(xbm, "sleep_seconds"):
            replies, fetched_count, rate_limited = xbm.fetch_replies_for_tweet(
                self.token, tweet, {"mode": "all"}
            )

        self.assertFalse(rate_limited)
        # fetched_count は返却件数全体（フィルタ前）を維持する
        self.assertEqual(fetched_count, 2)
        # フィルタ後は投稿者自身への返信のみ
        self.assertEqual([r["id"] for r in replies], ["1302"])


class TestCleanReplies(BaseTestCase):
    def _seed_files(self):
        tweet1 = make_tweet("8001", text="GitHubで公開した投稿")
        replies1 = [
            {"id": "8002", "author_id": "999", "created_at": "2026-09-01T13:00:00.000Z",
             "text": "@someone DMでお送りしました", "in_reply_to_user_id": "555"},
            {"id": "8003", "author_id": "999", "created_at": "2026-09-01T14:00:00.000Z",
             "text": "続報です"},
        ]
        xbm.save_tweet(self.data, tweet1, fetched_at="x", replies_list=replies1)

        tweet2 = make_tweet("8101", text="GitHubで別の投稿")
        replies2 = [
            {"id": "8102", "author_id": "999", "created_at": "2026-09-02T10:00:00.000Z",
             "text": "@aaa ありがとうございます"},
        ]
        xbm.save_tweet(self.data, tweet2, fetched_at="x", replies_list=replies2)

        tweet3 = make_tweet("8201", text="GitHubでリプライ無し投稿")
        xbm.save_tweet(self.data, tweet3, fetched_at="x", replies_list=[])

        return (
            self.data / "bookmarks" / "2026-09" / "8001.md",
            self.data / "bookmarks" / "2026-09" / "8101.md",
            self.data / "bookmarks" / "2026-09" / "8201.md",
        )

    def test_clean_replies_removes_at_blocks_updates_count_and_backs_up(self):
        path1, path2, path3 = self._seed_files()
        before1 = path1.read_text(encoding="utf-8")
        before3 = path3.read_text(encoding="utf-8")
        self.assertIn("replies: 2", before1)
        self.assertIn("@someone", before1)

        rc = xbm.run_clean_replies(self.data, dry_run=False)
        self.assertEqual(rc, 0)

        after1 = path1.read_text(encoding="utf-8")
        self.assertIn("replies: 1", after1)
        self.assertNotIn("@someone", after1)
        self.assertIn("続報です", after1)
        # links には触れない（元のfrontmatterのlinks行がそのまま残る）
        self.assertIn("links: []", after1)

        # 全ブロックが @ だったファイルは節ごと削除され replies: 0 になる
        after2 = path2.read_text(encoding="utf-8")
        self.assertIn("replies: 0", after2)
        self.assertNotIn(xbm.REPLIES_SECTION_HEADING, after2)
        self.assertNotIn("@aaa", after2)

        # 元々リプライが無いファイルは変更されない
        after3 = path3.read_text(encoding="utf-8")
        self.assertEqual(before3, after3)

        # バックアップが作られ、元の内容が保持されている
        backups = list(self.data.glob("backup-*/bookmarks/2026-09/8001.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), before1)
        backup_2004 = list(self.data.glob("backup-*/bookmarks/2026-09/8101.md"))
        self.assertEqual(len(backup_2004), 1)

    def test_clean_replies_dry_run_does_not_write_or_backup(self):
        path1, path2, path3 = self._seed_files()
        before1 = path1.read_text(encoding="utf-8")
        before2 = path2.read_text(encoding="utf-8")

        rc = xbm.run_clean_replies(self.data, dry_run=True)
        self.assertEqual(rc, 0)

        self.assertEqual(path1.read_text(encoding="utf-8"), before1)
        self.assertEqual(path2.read_text(encoding="utf-8"), before2)
        # dry-run ではバックアップも作らない
        self.assertEqual(list(self.data.glob("backup-*")), [])



# ---------------------------------------------------------------------------
# 引用ポスト・メディアダウンロード
# ---------------------------------------------------------------------------


def quote_tweet(tid, qid, text="引用コメント GitHub", author_id="999"):
    t = make_tweet(tid, text=text, author_id=author_id)
    t["referenced_tweets"] = [{"type": "quoted", "id": qid}]
    return t


def run_sync_with_page(test, page, **kwargs):
    urls = []

    def fake_http_request(method, url, headers=None, data=None, timeout=30):
        urls.append(url)
        if "/bookmarks" in url:
            return make_response(200, page)
        return make_response(200, {"data": [], "meta": {}})

    kwargs.setdefault("no_replies", True)
    with patch.object(xbm, "http_request", side_effect=fake_http_request), \
         patch.object(xbm, "sleep_seconds"):
        rc = xbm.run_sync(test.cfg, test.token, test.home, test.data, full=False, max_pages=10, **kwargs)
    test.assertEqual(rc, 0)
    return urls


class TestQuotedSync(BaseTestCase):
    def _md(self, tid):
        return (self.data / "bookmarks" / "2026-09" / f"{tid}.md").read_text(encoding="utf-8")

    def test_quoted_resolved_into_frontmatter_and_body_before_replies(self):
        qt = make_tweet("5002", text="引用先本文 &amp; 1行目\n2行目 https://t.co/x",
                        author_id="456", created_at="2026-08-31T00:00:00.000Z",
                        entities={"urls": [{"url": "https://t.co/x", "expanded_url": "https://example.com/a"}]})
        page = {"data": [quote_tweet("5001", "5002")], "includes": {"tweets": [qt]}, "meta": {}}
        urls = run_sync_with_page(self, page)
        self.assertIn("referenced_tweets.id.attachments.media_keys", urllib.parse.unquote(urls[0]))
        content = self._md("5001")
        self.assertIn('quoted:\n  id: "5002"\n  url: https://x.com/i/status/5002', content)
        self.assertIn('  author_id: "456"', content)
        self.assertIn("  created_at: 2026-08-31T00:00:00.000Z", content)
        self.assertIn('  text: "引用先本文 & 1行目\\n2行目 https://example.com/a"', content)
        self.assertIn("  media: []", content)
        self.assertIn(
            "\n\n## 引用元の投稿\nhttps://x.com/i/status/5002\n\n> 引用先本文 & 1行目\n> 2行目 https://example.com/a",
            content,
        )

        # リプライ節より前に入ること
        tweet = quote_tweet("5003", "5002")
        xbm.save_tweet(self.data, tweet, "x", filter_label="x", replies_list=[
            {"id": "5004", "author_id": "999", "created_at": "2026-09-01T13:00:00.000Z", "text": "続報"}],
            quoted={"id": "5002", "text": "q", "media": []})
        c3 = self._md("5003")
        self.assertLess(c3.index("## 引用元の投稿"), c3.index("## 投稿者のリプライ"))

    def test_quoted_video_selects_max_bitrate_mp4(self):
        qt = make_tweet("5102", text="動画つき", author_id="456")
        qt["attachments"] = {"media_keys": ["qm1"]}
        page = {
            "data": [quote_tweet("5101", "5102")],
            "includes": {
                "tweets": [qt],
                "media": [{
                    "media_key": "qm1", "type": "video", "duration_ms": 1234,
                    "preview_image_url": "https://pbs.twimg.com/p.jpg",
                    "variants": [
                        {"content_type": "video/mp4", "bit_rate": 100, "url": "https://video.twimg.com/low.mp4"},
                        {"content_type": "video/mp4", "bit_rate": 900, "url": "https://video.twimg.com/high.mp4"},
                        {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/x.m3u8"},
                    ],
                }],
            },
            "meta": {},
        }
        run_sync_with_page(self, page)
        content = self._md("5101")
        self.assertIn('  media:\n    - type: "video"', content)
        self.assertIn('      video_url: "https://video.twimg.com/high.mp4"', content)
        self.assertNotIn("low.mp4", content)

    def test_no_quote_has_no_quoted_and_unavailable_quote(self):
        page = {"data": [make_tweet("5201", text="GitHub plain"), quote_tweet("5202", "5299")],
                "includes": {"tweets": []}, "meta": {}}
        run_sync_with_page(self, page)
        plain = self._md("5201")
        self.assertNotIn("quoted:", plain)
        self.assertNotIn("引用元の投稿", plain)
        un = self._md("5202")
        self.assertIn('quoted:\n  id: "5299"\n  url: https://x.com/i/status/5299\n  unavailable: true', un)
        self.assertNotIn("  text:", un)
        self.assertIn("## 引用元の投稿\nhttps://x.com/i/status/5299", un)

    def test_keyword_only_in_quoted_text_is_saved(self):
        qt = make_tweet("5302", text="Claude Code の使い方", author_id="456")
        t = quote_tweet("5301", "5302", text="これ見て")
        page = {"data": [t], "includes": {"tweets": [qt]}, "meta": {}}
        with patch.object(xbm, "run_claude", side_effect=AssertionError("claude must not be called")):
            run_sync_with_page(self, page)
        content = self._md("5301")
        self.assertIn('filter: "keyword:Claude"', content)

    def test_no_quoted_flag_omits_referenced_tweets_expansions(self):
        qt = make_tweet("5402", text="q")
        page = {"data": [quote_tweet("5401", "5402")], "includes": {"tweets": [qt]}, "meta": {}}
        urls = run_sync_with_page(self, page, no_quoted=True)
        self.assertNotIn("referenced_tweets", urllib.parse.unquote(urls[0]))
        self.assertNotIn("quoted:", self._md("5401"))

    def test_cost_logged_for_quoted(self):
        qt = make_tweet("5502", text="q")
        page = {"data": [quote_tweet("5501", "5502")], "includes": {"tweets": [qt]}, "meta": {}}
        run_sync_with_page(self, page)
        log = (self.data / "usage.log").read_text(encoding="utf-8")
        self.assertIn("quoted=1", log)
        self.assertIn("quoted_cost_usd=0.005", log)

    def test_invalid_quoted_id_is_not_saved(self):
        page = {"data": [quote_tweet("5601", "abc/../x")], "includes": {}, "meta": {}}
        run_sync_with_page(self, page)
        self.assertNotIn("quoted:", self._md("5601"))

    def test_pending_retains_quoted_and_is_applied_on_retry(self):
        qt = make_tweet("5702", text="引用先の本文", author_id="456")
        t = quote_tweet("5701", "5702", text="雑談です")
        page = {"data": [t], "includes": {"tweets": [qt]}, "meta": {}}
        with patch.object(xbm, "run_claude", side_effect=RuntimeError("boom")):
            run_sync_with_page(self, page)
        pending = xbm.load_pending_tweets(self.data)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["_quoted"]["id"], "5702")
        self.assertEqual(pending[0]["_quoted"]["text"], "引用先の本文")

        seen = {}

        def fake_claude(prompt, model="haiku"):
            seen["prompt"] = prompt
            return json.dumps(["5701"])

        def fake_http(method, url, headers=None, data=None, timeout=30):
            return make_response(200, {"data": [], "meta": {}})

        with patch.object(xbm, "run_claude", side_effect=fake_claude), \
             patch.object(xbm, "http_request", side_effect=fake_http), \
             patch.object(xbm, "sleep_seconds"):
            xbm.run_sync(self.cfg, self.token, self.home, self.data, no_replies=True)
        self.assertIn("引用先の本文", seen["prompt"])
        content = self._md("5701")
        self.assertIn('quoted:\n  id: "5702"', content)
        self.assertIn("> 引用先の本文", content)


class TestQuotedBackfill(BaseTestCase):
    def _save(self, tid, links, replies=None):
        t = make_tweet(tid, text="GitHub post")
        t["entities"] = {"urls": [{"url": f"https://t.co/{tid}", "expanded_url": u} for u in links]}
        xbm.save_tweet(self.data, t, "x", filter_label="keyword:GitHub", replies_list=replies, media=[])
        return self.data / "bookmarks" / "2026-09" / f"{tid}.md"

    def _seed(self):
        a = self._save("6001", ["https://x.com/someone/status/6101"],
                       replies=[{"id": "6002", "author_id": "999", "created_at": "2026-09-01T13:00:00.000Z", "text": "続報"}])
        b = self._save("6003", ["https://x.com/someone/status/6103"])
        self._save("6005", ["https://x.com/me/status/6005/photo/1"])  # 自己リンクのみ
        self._save("6006", ["https://example.com/a"])
        return a, b

    def test_candidates_only_missing_quoted_with_foreign_status_link(self):
        self._seed()
        ids = sorted(c["id"] for c in xbm.find_quoted_candidates(self.data))
        self.assertEqual(ids, ["6001", "6003"])
        # quoted: null が付いたら再対象化されない
        xbm.backfill_quoted_into_file(self.data / "bookmarks" / "2026-09" / "6003.md", None)
        ids = sorted(c["id"] for c in xbm.find_quoted_candidates(self.data))
        self.assertEqual(ids, ["6001"])

    def test_non_interactive_without_yes_does_not_run(self):
        self._seed()
        with patch.object(xbm, "http_request", side_effect=AssertionError("no http")), \
             patch.object(xbm, "stdin_is_tty", return_value=False):
            rc = xbm.run_quoted_backfill(self.cfg, self.token, self.home, self.data, yes=False)
        self.assertEqual(rc, 1)
        self.assertNotIn("quoted:", (self.data / "bookmarks" / "2026-09" / "6001.md").read_text(encoding="utf-8"))

    def test_interactive_decline_does_not_run(self):
        self._seed()
        with patch.object(xbm, "http_request", side_effect=AssertionError("no http")), \
             patch.object(xbm, "stdin_is_tty", return_value=True), \
             patch.object(xbm, "confirm_prompt", return_value=False):
            rc = xbm.run_quoted_backfill(self.cfg, self.token, self.home, self.data, yes=False)
        self.assertEqual(rc, 0)

    def test_backfill_writes_quoted_and_null_and_logs_cost(self):
        a, b = self._seed()
        t1 = quote_tweet("6001", "6101")
        t3 = make_tweet("6003")  # 引用ではない
        qt = make_tweet("6101", text="引用先 本文", author_id="456")
        payload = {"data": [t1, t3], "includes": {"tweets": [qt]}}
        urls = []

        def fake_http(method, url, headers=None, data=None, timeout=30):
            urls.append(url)
            return make_response(200, payload)

        with patch.object(xbm, "http_request", side_effect=fake_http):
            rc = xbm.run_quoted_backfill(self.cfg, self.token, self.home, self.data, yes=True)
        self.assertEqual(rc, 0)
        self.assertEqual(len(urls), 1)
        u = urllib.parse.unquote(urls[0])
        self.assertIn("/2/tweets?ids=", u)
        self.assertIn("6001", u)
        self.assertIn("6003", u)
        self.assertIn("referenced_tweets.id.attachments.media_keys", u)

        ca = a.read_text(encoding="utf-8")
        self.assertIn('quoted:\n  id: "6101"', ca)
        self.assertIn('  text: "引用先 本文"', ca)
        # 本文節はリプライ節より前
        self.assertLess(ca.index("## 引用元の投稿"), ca.index("## 投稿者のリプライ"))
        self.assertIn("> 引用先 本文", ca)
        self.assertIn("quoted: null", b.read_text(encoding="utf-8"))
        self.assertNotIn("引用元の投稿", b.read_text(encoding="utf-8"))

        log = (self.data / "usage.log").read_text(encoding="utf-8")
        self.assertIn("quoted=1", log)
        self.assertIn("cost_usd=0.015", log)  # 投稿2件 + 引用先1件

        # 書き換え後も frontmatter が壊れていない
        self.assertEqual(xbm.find_quoted_candidates(self.data), [])

    def test_api_failure_writes_nothing(self):
        a, b = self._seed()
        before = (a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8"))
        with patch.object(xbm, "http_request", return_value=make_response(500, {"error": "x"})):
            rc = xbm.run_quoted_backfill(self.cfg, self.token, self.home, self.data, yes=True)
        self.assertEqual(rc, 0)
        self.assertEqual((a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")), before)

    def test_limit_applies(self):
        self._seed()
        with patch.object(xbm, "http_request", return_value=make_response(200, {"data": []})) as m:
            xbm.run_quoted_backfill(self.cfg, self.token, self.home, self.data, yes=True, limit=1)
        ids_param = urllib.parse.unquote(m.call_args[0][1]).split("ids=")[1].split("&")[0]
        self.assertEqual(len(ids_param.split(",")), 1)


class _FakeResponse:
    def __init__(self, data, length=None):
        self._data = data
        self._pos = 0
        self.headers = {"Content-Length": str(length)} if length is not None else {}

    def read(self, n=-1):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDownloadMedia(BaseTestCase):
    def _seed(self):
        t = make_tweet("7001", text="GitHub video")
        media = [
            {"type": "video", "preview": "https://pbs.twimg.com/p.jpg", "duration_ms": 10,
             "video_url": "https://video.twimg.com/a/b.mp4?tag=12"},
            {"type": "photo", "url": "https://pbs.twimg.com/media/x.png"},
            {"type": "video", "preview": "https://pbs.twimg.com/p2.jpg"},  # video_url 無し -> 対象外
        ]
        quoted = {"id": "7002", "text": "q", "author_id": "456", "created_at": "2026-08-01T00:00:00.000Z",
                  "media": [{"type": "video", "video_url": "https://video.twimg.com/q.mp4"},
                            {"type": "photo", "url": "https://evil.example.com/x.jpg"}]}
        xbm.save_tweet(self.data, t, "x", filter_label="x", media=media, quoted=quoted)

    def test_parse_fm_media_roundtrip(self):
        self._seed()
        text = (self.data / "bookmarks" / "2026-09" / "7001.md").read_text(encoding="utf-8")
        parsed = xbm.parse_fm_media(xbm._FRONTMATTER_RE.match(text).group(1))
        self.assertEqual(len(parsed["media"]), 3)
        self.assertEqual(parsed["media"][0]["video_url"], "https://video.twimg.com/a/b.mp4?tag=12")
        self.assertEqual(parsed["media"][0]["duration_ms"], 10)
        self.assertEqual(len(parsed["quoted"]), 2)
        self.assertEqual(parsed["quoted"][0]["video_url"], "https://video.twimg.com/q.mp4")

    def test_download_paths_host_rejection_and_existing_skip(self):
        self._seed()
        calls = []

        def fake_open(url, timeout=30):
            calls.append(url)
            return _FakeResponse(b"DATA", length=4)

        with patch.object(xbm, "open_stream", side_effect=fake_open):
            rc = xbm.run_download_media(self.data, ["7001"])
        media = self.data / "media"
        self.assertEqual(rc, 1)  # evil ホストは拒否（失敗扱い）
        self.assertEqual(sorted(p.name for p in media.iterdir()), ["7001-1.mp4", "7001-2.png", "7001-q-1.mp4"])
        self.assertNotIn("https://evil.example.com/x.jpg", calls)
        self.assertEqual(len(calls), 3)
        self.assertEqual((media / "7001-1.mp4").read_bytes(), b"DATA")

        # 2回目は既存をスキップ（ダウンロードしない）
        with patch.object(xbm, "open_stream", side_effect=AssertionError("no download")):
            xbm.run_download_media(self.data, ["7001"])

    def test_missing_tweet_and_invalid_id(self):
        rc = xbm.run_download_media(self.data, ["9999", "../x"])
        self.assertEqual(rc, 1)

    def test_allowed_url_check(self):
        self.assertTrue(xbm.is_allowed_download_url("https://video.twimg.com/a.mp4"))
        self.assertTrue(xbm.is_allowed_download_url("https://pbs.twimg.com/a.jpg"))
        self.assertFalse(xbm.is_allowed_download_url("https://twimg.com/a.jpg"))
        self.assertFalse(xbm.is_allowed_download_url("https://video.twimg.com.evil.com/a.mp4"))
        self.assertFalse(xbm.is_allowed_download_url("http://video.twimg.com/a.mp4"))

    def test_size_limit_by_content_length_and_streaming_removes_partial(self):
        dest = self.tmp / "media" / "x.mp4"
        with patch.object(xbm, "open_stream", return_value=_FakeResponse(b"x", length=1000)):
            with self.assertRaises(ValueError):
                xbm.download_file("https://video.twimg.com/x.mp4", dest, max_bytes=100)
        self.assertFalse(dest.exists())
        self.assertEqual(list(dest.parent.glob("*")), [])

        # Content-Length 無し・読み込み中に超過 -> 部分ファイルを削除
        with patch.object(xbm, "open_stream", return_value=_FakeResponse(b"y" * 500)), \
             patch.object(xbm, "DOWNLOAD_CHUNK_BYTES", 50):
            with self.assertRaises(ValueError):
                xbm.download_file("https://video.twimg.com/x.mp4", dest, max_bytes=100)
        self.assertFalse(dest.exists())
        self.assertEqual(list(dest.parent.glob("*")), [])



# ---------------------------------------------------------------------------
# X 記事（Article）対応 / enrich
# ---------------------------------------------------------------------------


def article_tweet(tid, title="記事タイトル", plain="記事の本文です。\n2段落目。", preview="プレビュー",
                  text=None, author_id="456"):
    t = make_tweet(tid, text=text or "http://x.com/i/article/" + tid, author_id=author_id)
    art = {}
    if title is not None:
        art["title"] = title
    if plain is not None:
        art["plain_text"] = plain
    if preview is not None:
        art["preview_text"] = preview
    t["article"] = art
    return t


class TestArticle(BaseTestCase):
    def _md(self, tid):
        return (self.data / "bookmarks" / "2026-09" / f"{tid}.md").read_text(encoding="utf-8")

    def test_own_article_saved_in_frontmatter_and_body(self):
        page = {"data": [article_tweet("8301", text="GitHub http://x.com/i/article/8301")], "meta": {}}
        urls = run_sync_with_page(self, page)
        self.assertIn("article", urllib.parse.parse_qs(urllib.parse.urlparse(urls[0]).query)["tweet.fields"][0].split(","))
        c = self._md("8301")
        self.assertIn('article:\n  title: "記事タイトル"\n  preview_text: "プレビュー"', c)
        self.assertIn("\n\n## 記事本文\n\n### 記事タイトル\n\n記事の本文です。\n2段落目。", c)

    def test_title_only_article_is_saved(self):
        page = {"data": [article_tweet("8302", plain=None, preview=None, text="GitHub x")], "meta": {}}
        run_sync_with_page(self, page)
        c = self._md("8302")
        self.assertIn('article:\n  title: "記事タイトル"', c)
        self.assertIn("## 記事本文\n\n### 記事タイトル", c)

    def test_quoted_article_in_frontmatter_and_quote_section(self):
        qt = article_tweet("8402")
        page = {"data": [quote_tweet("8401", "8402")], "includes": {"tweets": [qt]}, "meta": {}}
        run_sync_with_page(self, page)
        c = self._md("8401")
        self.assertIn('  article:\n    title: "記事タイトル"\n    preview_text: "プレビュー"', c)
        self.assertIn("> http://x.com/i/article/8402\n\n### 記事: 記事タイトル\n\n記事の本文です。", c)
        # 記事は plain_text も含めて pending 用 _quoted に保持される形で解決されている
        q = xbm.resolve_quoted(quote_tweet("8401", "8402"), {"8402": qt}, {})
        self.assertEqual(q["article"]["plain_text"], "記事の本文です。\n2段落目。")

    def test_keyword_only_in_article_body_passes_filter(self):
        t = article_tweet("8501", title="日記", plain="今日は Claude Code を使った", preview=None,
                          text="これ読んで")
        page = {"data": [t], "meta": {}}
        with patch.object(xbm, "run_claude", side_effect=AssertionError("no claude")):
            run_sync_with_page(self, page)
        self.assertIn('filter: "keyword:Claude"', self._md("8501"))

    def test_keyword_only_in_quoted_article_passes_filter(self):
        qt = article_tweet("8602", title="日記", plain="Python の話", preview=None)
        page = {"data": [quote_tweet("8601", "8602", text="これ")], "includes": {"tweets": [qt]}, "meta": {}}
        with patch.object(xbm, "run_claude", side_effect=AssertionError("no claude")):
            run_sync_with_page(self, page)
        self.assertIn('filter: "keyword:Python"', self._md("8601"))

    def test_claude_prompt_truncates_article_plain_text(self):
        t = article_tweet("8701", plain="あ" * 5000)
        prompt = xbm.build_claude_prompt([t])
        self.assertIn("あ" * 2000, prompt)
        self.assertNotIn("あ" * 2001, prompt)


class TestEnrich(BaseTestCase):
    def _old_md(self, tid, text="GitHub post", extra_fm="", body_extra=""):
        d = self.data / "bookmarks" / "2026-09"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{tid}.md"
        p.write_text(
            f'---\nid: "{tid}"\nurl: https://x.com/i/status/{tid}\nauthor_id: "999"\n'
            f'created_at: 2026-09-01T12:00:00.000Z\nconversation_id: "{tid}"\nlang: ja\n'
            f'filter: "keyword:GitHub"\nreplies: 0\nfetched_at: x\nlinks: []\n{extra_fm}---\n\n{text}{body_extra}\n',
            encoding="utf-8")
        return p

    def _run(self, payload, **kw):
        urls = []

        def fake_http(method, url, headers=None, data=None, timeout=30):
            urls.append(url)
            return make_response(200, payload)

        with patch.object(xbm, "http_request", side_effect=fake_http):
            rc = xbm.run_enrich_backfill(self.cfg, self.token, self.home, self.data, yes=True, **kw)
        self.assertEqual(rc, 0)
        return urls

    def test_enrich_adds_media_article_and_quoted_then_is_idempotent(self):
        p1 = self._old_md("9001")  # media 無し
        p2 = self._old_md("9002", text="記事 http://x.com/i/article/9002", extra_fm="media: []\nquoted: null\n")
        p3 = self._old_md("9003", extra_fm="media: []\nquoted: null\n")  # 対象外
        p4 = self._old_md(
            "9004", text="引用", extra_fm=(
                'media: []\nquoted:\n  id: "9104"\n  url: https://x.com/i/status/9104\n'
                '  author_id: "456"\n  text: "http://x.com/i/article/9104"\n  media: []\n'),
            body_extra="\n\n## 引用元の投稿\nhttps://x.com/i/status/9104\n\n> http://x.com/i/article/9104"
                       "\n## 投稿者のリプライ\n\n### 2026-09-01T13:00:00.000Z\nhttps://x.com/i/status/9199\n\nrep")
        self.assertEqual(sorted(c["id"] for c in xbm.find_enrich_candidates(self.data)), ["9001", "9002", "9004"])

        t1 = quote_tweet("9001", "9101", text="GitHub post")
        t1["attachments"] = {"media_keys": ["m1"]}
        t2 = article_tweet("9002", text="記事 http://x.com/i/article/9002")
        t4 = quote_tweet("9004", "9104", text="引用")
        payload = {
            "data": [t1, t2, t4],
            "includes": {
                "tweets": [make_tweet("9101", text="引用先 本文", author_id="456"), article_tweet("9104")],
                "media": [{"media_key": "m1", "type": "photo", "url": "https://pbs.twimg.com/a.jpg"}],
            },
        }
        urls = self._run(payload)
        u = urllib.parse.unquote(urls[0])
        self.assertIn("attachments.media_keys,referenced_tweets.id", u)
        self.assertIn("article", u.split("tweet.fields=")[1].split("&")[0].split(","))

        c1 = p1.read_text(encoding="utf-8")
        self.assertIn('media:\n  - type: "photo"\n    url: "https://pbs.twimg.com/a.jpg"', c1)
        self.assertIn('quoted:\n  id: "9101"', c1)
        self.assertIn("> 引用先 本文", c1)

        c2 = p2.read_text(encoding="utf-8")
        self.assertIn('article:\n  title: "記事タイトル"', c2)
        self.assertIn("## 記事本文\n\n### 記事タイトル\n\n記事の本文です。", c2)

        c4 = p4.read_text(encoding="utf-8")
        self.assertIn('  article:\n    title: "記事タイトル"', c4)
        self.assertLess(c4.index("### 記事: 記事タイトル"), c4.index("## 投稿者のリプライ"))
        self.assertGreater(c4.index("### 記事: 記事タイトル"), c4.index("> http://x.com/i/article/9104"))
        self.assertEqual(c4.count('quoted:'), 1)  # 既存 quoted は書き換えない
        self.assertEqual(p3.read_text(encoding="utf-8").count("quoted"), 1)

        # 冪等: 2回目は対象0件
        self.assertEqual(xbm.find_enrich_candidates(self.data), [])
        with patch.object(xbm, "http_request", side_effect=AssertionError("no http")):
            self.assertEqual(xbm.run_enrich_backfill(self.cfg, self.token, self.home, self.data, yes=True), 0)

        log = (self.data / "usage.log").read_text(encoding="utf-8")
        self.assertIn("quoted=2", log)

    def test_non_article_link_gets_article_null_and_not_retargeted(self):
        self._old_md("9201", text="x http://x.com/i/article/1", extra_fm="media: []\nquoted: null\n")
        self._run({"data": [make_tweet("9201")]})
        c = (self.data / "bookmarks" / "2026-09" / "9201.md").read_text(encoding="utf-8")
        self.assertIn("article: null", c)
        self.assertEqual(xbm.find_enrich_candidates(self.data), [])

    def test_failure_writes_nothing_and_non_interactive_needs_yes(self):
        p = self._old_md("9301")
        before = p.read_text(encoding="utf-8")
        with patch.object(xbm, "http_request", return_value=make_response(500, {})):
            self.assertEqual(xbm.run_enrich_backfill(self.cfg, self.token, self.home, self.data, yes=True), 0)
        self.assertEqual(p.read_text(encoding="utf-8"), before)
        with patch.object(xbm, "http_request", side_effect=AssertionError("no http")), \
             patch.object(xbm, "stdin_is_tty", return_value=False):
            self.assertEqual(xbm.run_enrich_backfill(self.cfg, self.token, self.home, self.data, yes=False), 1)
        self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_429_stops_run(self):
        p = self._old_md("9401")
        before = p.read_text(encoding="utf-8")
        with patch.object(xbm, "http_request", return_value=make_response(429, {})):
            xbm.run_enrich_backfill(self.cfg, self.token, self.home, self.data, yes=True)
        self.assertEqual(p.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()

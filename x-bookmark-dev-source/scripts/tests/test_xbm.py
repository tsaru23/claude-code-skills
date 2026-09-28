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


if __name__ == "__main__":
    unittest.main()

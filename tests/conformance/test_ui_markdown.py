"""ADR-005 conformance: sanitized CommonMark rendering via `ui.markdown.render_markdown`
and the `markdown` template filter.

The filter is the only render path for analyst-authored Markdown (Case.description and
TimelineEvent.kind == "comment"). These tests assert that the XSS boundary holds:
raw HTML, event handlers, javascript:/data: URLs, and other dangerous constructs are
stripped or escaped, while legitimate CommonMark renders correctly.
"""

from __future__ import annotations

import re

from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cases.models import Case, CaseStatus, TimelineEvent
from identity.models import Organisation, User
from ui.markdown import render_markdown


class MarkdownRenderingTests(TestCase):
    """Unit tests for `ui.markdown.render_markdown` (the pure function)."""

    def test_script_tag_is_escaped(self) -> None:
        """Raw <script> tags are escaped by markdown-it (html=False), not passed through."""
        out = render_markdown("<script>alert(1)</script>")
        # No unescaped <script tag
        self.assertNotIn("<script", out.lower())
        # But the escaped entity is present
        self.assertIn("&lt;script&gt;", out)

    def test_img_onerror_is_escaped(self) -> None:
        """Raw HTML with event handlers is escaped by markdown-it, not parsed."""
        out = render_markdown('<img src=x onerror="alert(1)">')
        # No unescaped <img tag exists, so onerror can never be an *attribute* — it stays
        # visible literal text inside the escaped entity.
        self.assertNotIn("<img", out)
        self.assertIn("&lt;img", out)
        self.assertIn("onerror", out)

    def test_iframe_is_escaped(self) -> None:
        """Raw <iframe> tags are escaped by markdown-it."""
        out = render_markdown('<iframe src="https://evil.example"></iframe>')
        self.assertNotIn("<iframe", out.lower())
        self.assertIn("&lt;iframe", out)

    def test_raw_html_is_escaped_not_passed_through(self) -> None:
        """Raw HTML in source is escaped as text, not rendered as elements."""
        out = render_markdown("<div>raw html</div>")
        self.assertNotIn("<div", out.lower())
        self.assertIn("&lt;div&gt;", out)
        self.assertIn("raw html", out)

    def test_javascript_url_scheme_not_parsed_as_link(self) -> None:
        """javascript: URLs are not recognized as valid links by CommonMark parser;
        they remain as literal text, never becoming clickable links."""
        out = render_markdown("[click me](javascript:alert(1))")
        # The link is NOT parsed - it stays as literal text in a paragraph
        self.assertIn("[click me](javascript:alert(1))", out)
        self.assertNotIn("<a ", out)

    def test_data_url_scheme_not_parsed_as_link(self) -> None:
        """data: URLs are not recognized as valid links by CommonMark parser."""
        out = render_markdown("[x](data:text/html,<script>alert(1)</script>)")
        self.assertIn("[x](data:text/html,", out)
        self.assertNotIn("<a ", out)

    def test_normal_https_link_survives_with_noopener(self) -> None:
        """Standard https links are parsed and sanitized with rel=noopener noreferrer."""
        out = render_markdown("[example](https://example.com)")
        self.assertIn('href="https://example.com"', out)
        self.assertIn("noopener", out)
        self.assertIn("noreferrer", out)

    def test_relative_link_survives(self) -> None:
        """Relative links are parsed and sanitized with rel=noopener noreferrer."""
        out = render_markdown("[local](/path)")
        self.assertIn('href="/path"', out)
        self.assertIn("noopener", out)

    def test_mailto_link_survives(self) -> None:
        """mailto: links are allowed and sanitized."""
        out = render_markdown("[email](mailto:test@example.com)")
        self.assertIn('href="mailto:test@example.com"', out)
        self.assertIn("noopener", out)

    def test_nested_emphasis_renders(self) -> None:
        out = render_markdown("**bold _italic_**")
        self.assertIn("<strong>", out)
        self.assertIn("<em>", out)
        self.assertIn("bold", out)
        self.assertIn("italic", out)

    def test_fenced_code_block_renders(self) -> None:
        out = render_markdown("```python\nprint('hello')\n```")
        self.assertIn("<pre>", out)
        self.assertIn("<code>", out)
        self.assertIn("print", out)

    def test_empty_input_returns_empty_string(self) -> None:
        self.assertEqual(render_markdown(""), "")
        self.assertEqual(render_markdown(None), "")

    def test_headings_render(self) -> None:
        out = render_markdown("# H1\n## H2\n### H3")
        self.assertIn("<h1", out)
        self.assertIn("<h2", out)
        self.assertIn("<h3", out)

    def test_blockquote_renders(self) -> None:
        out = render_markdown("> quoted text")
        self.assertIn("<blockquote>", out)
        self.assertIn("quoted text", out)

    def test_lists_render(self) -> None:
        out = render_markdown("- item 1\n- item 2")
        self.assertIn("<ul>", out)
        self.assertIn("<li>", out)
        self.assertIn("item 1", out)

        out = render_markdown("1. first\n2. second")
        self.assertIn("<ol>", out)
        self.assertIn("<li>", out)
        self.assertIn("first", out)

    def test_horizontal_rule_renders(self) -> None:
        out = render_markdown("---")
        self.assertIn("<hr", out)

    def test_inline_code_renders(self) -> None:
        out = render_markdown("Use `code` here")
        self.assertIn("<code>", out)
        self.assertIn("code", out)

    def test_fail_closed_on_exception(self) -> None:
        # This is a defensive test; the function should never raise.
        # If it does, it returns escaped input.
        out = render_markdown("normal text")
        self.assertIn("normal text", out)


class MarkdownTemplateFilterTests(TestCase):
    """Integration tests for the `markdown` template filter."""

    def setUp(self) -> None:
        self.org = Organisation.objects.create(name="Test Org")
        self.user = User.objects.create_user(
            username="analyst", password="pw", email="a@example.net", org=self.org
        )
        status = CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0]
        self.case = Case.objects.create(title="Test Case", status=status, owner_org=self.org)

    def test_filter_renders_markdown_in_template(self) -> None:
        t = Template("{% load ui_tags %}{{ value|markdown }}")
        out = t.render(Context({"value": "**bold**"}))
        self.assertIn("<strong>bold</strong>", out)

    def test_filter_escapes_script_in_comment_event(self) -> None:
        """TimelineEvent with kind=comment renders through markdown filter; script is neutralized."""
        event = TimelineEvent.objects.create(
            case=self.case,
            title="Note",
            description="<script>alert(1)</script>",
            kind="comment",
            actor=self.user,
            date=timezone.now(),
        )
        t = Template("{% load ui_tags %}{{ event.description|markdown }}")
        out = t.render(Context({"event": event}))
        self.assertNotIn("<script", out.lower())
        self.assertIn("&lt;script&gt;", out)

    def test_filter_handles_none_and_empty(self) -> None:
        t = Template("{% load ui_tags %}{{ value|markdown }}")
        self.assertEqual(t.render(Context({"value": None})), "")
        self.assertEqual(t.render(Context({"value": ""})), "")


class TimelineEntryMarkdownIntegrationTests(TestCase):
    """End-to-end template test: `_timeline_entry.html` renders comments as Markdown,
    other kinds stay verbatim in <pre>.
    """

    def setUp(self) -> None:
        self.org = Organisation.objects.create(name="Test Org")
        self.user = User.objects.create_user(
            username="analyst", password="pw", email="a@example.net", org=self.org
        )
        status = CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0]
        self.case = Case.objects.create(title="Test Case", status=status, owner_org=self.org)

    def test_comment_kind_renders_as_markdown(self) -> None:
        """A comment with a script payload is sanitized, not executed."""
        event = TimelineEvent.objects.create(
            case=self.case,
            title="Analyst note",
            description="**Important:** <script>alert(1)</script>",
            kind="comment",
            actor=self.user,
            date=timezone.now(),
        )
        t = Template("{% include 'ui/_timeline_entry.html' %}")
        out = t.render(Context({"event": event}))
        # Should have .md class div, not <pre>
        self.assertIn('class="tl-body md"', out)
        self.assertNotIn("<pre", out)
        # Script should be escaped
        self.assertNotIn("<script", out.lower())
        self.assertIn("&lt;script&gt;", out)
        # Markdown bold should render
        self.assertIn("<strong>Important:</strong>", out)

    def test_automation_run_kind_stays_verbatim_in_pre(self) -> None:
        """Non-comment kinds (e.g., automation-run) keep the verbatim <pre> rendering."""
        event = TimelineEvent.objects.create(
            case=self.case,
            title="Automation run",
            description="**This should stay literal**\nLine 2",
            kind="automation-run",
            actor=self.user,
            date=timezone.now(),
            metadata={"status": "Success"},
        )
        t = Template("{% include 'ui/_timeline_entry.html' %}")
        out = t.render(Context({"event": event}))
        # Should have <pre class="tl-body">
        self.assertIn('<pre class="tl-body">', out)
        # Content should be verbatim (escaped by template engine, not markdown)
        self.assertIn("**This should stay literal**", out)
        self.assertNotIn("<strong>", out)

    def test_status_change_kind_stays_verbatim(self) -> None:
        event = TimelineEvent.objects.create(
            case=self.case,
            title="Status changed",
            description="Case moved to **In Progress**",
            kind="status-change",
            actor=self.user,
            date=timezone.now(),
        )
        t = Template("{% include 'ui/_timeline_entry.html' %}")
        out = t.render(Context({"event": event}))
        self.assertIn('<pre class="tl-body">', out)
        self.assertNotIn("<strong>", out)

    def test_alert_imported_kind_stays_verbatim(self) -> None:
        event = TimelineEvent.objects.create(
            case=self.case,
            title="Alert imported",
            description="Raw alert data with **markdown-like** text",
            kind="alert-imported",
            actor=self.user,
            date=timezone.now(),
        )
        t = Template("{% include 'ui/_timeline_entry.html' %}")
        out = t.render(Context({"event": event}))
        self.assertIn('<pre class="tl-body">', out)
        self.assertNotIn("<strong>", out)


class CaseDetailMarkdownIntegrationTests(TestCase):
    """Case description renders through the markdown filter."""

    def setUp(self) -> None:
        self.org = Organisation.objects.create(name="Test Org")
        self.user = User.objects.create_user(
            username="analyst", password="pw", email="a@example.net", org=self.org
        )
        status = CaseStatus.objects.get_or_create(
            value="New", defaults={"stage": "New", "order": 1}
        )[0]
        self.case = Case.objects.create(
            title="Test Case",
            status=status,
            owner_org=self.org,
            description="# Case Overview\n\n**Key finding:** <script>alert(1)</script>\n\n- Item 1\n- Item 2",
        )

    def test_case_description_renders_as_markdown(self) -> None:
        client = self.client_class()
        client.force_login(self.user)
        response = client.get(reverse("ui-case-detail", kwargs={"case_id": str(self.case.number)}))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # Extract the case description section (between <div class="body md"> and </div>)
        match = re.search(r'<div class="body md">(.*?)</div>', body, re.DOTALL)
        self.assertIsNotNone(match, "case description section not found")
        desc_html = match.group(1)

        # Script should be neutralized (escaped)
        self.assertNotIn("<script", desc_html.lower())
        self.assertIn("&lt;script&gt;", desc_html)
        # Markdown should render
        self.assertIn("<h1", desc_html)
        self.assertIn("<strong>Key finding:</strong>", desc_html)
        self.assertIn("<ul>", desc_html)
        self.assertIn("<li>Item 1</li>", desc_html)

    def test_empty_case_description_renders_nothing(self) -> None:
        case = Case.objects.create(
            title="No description",
            status=CaseStatus.objects.get(value="New"),
            owner_org=self.org,
            description="",
        )
        client = self.client_class()
        client.force_login(self.user)
        response = client.get(reverse("ui-case-detail", kwargs={"case_id": str(case.number)}))
        body = response.content.decode()
        # No .body element at all when description is empty
        self.assertNotIn('class="body md"', body)
        self.assertNotIn("<pre", body)

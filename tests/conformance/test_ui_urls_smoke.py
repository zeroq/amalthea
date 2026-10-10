"""URL-surface smoke tests for the analyst UI (regression guard for B1).

Two failure classes this file exists to catch, both of which shipped undetected once:

1. **A template names a route that does not exist.** ``{% url 'ui-...' %}`` raises
   ``NoReverseMatch`` at render time, so the page is a 500, not a broken link. The names in
   ``case_tags.html`` and ``case_attachments.html`` were missing from ``ui/urls.py`` exactly this
   way.
2. **A route exists but its view 500s on a live GET.** A name that reverses is not the same claim as
   a page that renders.

The scan is driven by the templates, not by a hand-maintained list, so a new ``{% url 'ui-...' %}``
is covered the moment it is written. The live GET pass follows the same set (plus the two pages B1
was about, which are reached from links or redirects rather than a template tag), so it exercises
the pages an analyst can actually reach without asserting on routes no template touches.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import NoReverseMatch, get_resolver, reverse

from cases.models import Attachment, Case, CaseStatus, Tag
from identity.models import Organisation, User

UI_DIR = Path(__file__).resolve().parents[2] / "ui"

# The literal-name form: `{% url '<name>' ... %}` or `{% url "<name>" ... %}`. Names assembled at
# runtime (a variable) cannot be checked statically and are deliberately out of scope.
URL_TAG = re.compile(r"""\{%\s*url\s+['"]([^'"]+)['"]""")


def _template_url_names() -> set[str]:
    """Every ``ui-`` prefixed name referenced by a literal ``{% url %}`` tag under ``ui/``."""
    names: set[str] = set()
    for template in UI_DIR.rglob("*.html"):
        for match in URL_TAG.finditer(template.read_text(encoding="utf-8")):
            name = match.group(1)
            if name.startswith("ui-"):
                names.add(name)
    return names


@pytest.fixture(autouse=True)
def _media_root(tmp_path: Path, settings: Any) -> None:
    """Uploads must never land in the repository's `media/` tree."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def case(db: None) -> Case:
    status = CaseStatus.objects.get_or_create(value="New", defaults={"stage": "New", "order": 1})[0]
    return Case.objects.create(title="URL smoke case", status=status)


@pytest.fixture
def analyst(db: None) -> User:
    org = Organisation.objects.create(name="URL Smoke Org")
    return User.objects.create_user(
        username="smoke", password="hunter2-correct", email="s@example.net", org=org
    )


@pytest.fixture
def browser(analyst: User) -> Client:
    client = Client()
    assert client.login(username="smoke", password="hunter2-correct")
    return client


# --- 1. every referenced name resolves ------------------------------------


def test_every_ui_url_name_in_a_template_resolves() -> None:
    names = _template_url_names()
    assert names, "the template scan matched no `ui-` URL names; the pattern or path is wrong"

    reverse_dict = get_resolver().reverse_dict
    missing = sorted(name for name in names if name not in reverse_dict)

    assert not missing, (
        "templates reference `ui-` URL names that no route defines, so those pages raise "
        f"NoReverseMatch (HTTP 500) at render time: {missing}"
    )


# --- 2. every GET-routable page renders -----------------------------------


def _reverse_candidates(names: set[str], case: Case) -> list[tuple[str, dict[str, str]]]:
    """Every named ``ui-`` route reverse-able with no variables or with one seeded case.

    Only names this test already knows about are swept — template-referenced names plus the two
    B1 pages. Sweeping the *whole* resolver namespace would be brittle: it would fail on routes no
    template touches (and that are therefore outside this guard's contract). Routes that need
    another resource id (a task, a tag, an attachment) are skipped too; routes that are POST-only
    answer the live GET below with 405 and are skipped there.
    """
    reverse_dict = get_resolver().reverse_dict
    candidates: list[tuple[str, dict[str, str]]] = []
    for name in sorted(names):
        if name not in reverse_dict:
            continue  # test 1 already reports this; do not double-fail here.
        params = reverse_dict[name][0][0][1] if reverse_dict[name][0] else []
        if not params:
            candidates.append((name, {}))
        elif params == ["case_id"]:
            candidates.append((name, {"case_id": str(case.number)}))
    return candidates


def test_every_get_routable_ui_page_renders(browser: Client, case: Case) -> None:
    # The B1 pages are reached from links/redirects, not always from a template tag, so they are
    # named explicitly as well as scanned.
    names = _template_url_names() | {"ui-case-tags", "ui-case-attachment-list"}
    exercised: list[str] = []
    for name, kwargs in _reverse_candidates(names, case):
        try:
            url = reverse(name, kwargs=kwargs)
        except NoReverseMatch:  # pragma: no cover - defensive; the resolver should agree
            continue
        response = browser.get(url)
        if response.status_code == 405:
            continue  # POST-only view; not part of the GET surface.
        exercised.append(name)
        assert response.status_code in (200, 302), (
            f"GET {name} ({url}) -> {response.status_code}, expected 200/302"
        )

    # The pages B1 was about must actually be exercised, or this test passes vacuously.
    for required in ("ui-case-tags", "ui-case-attachment-list", "ui-case-detail"):
        assert required in exercised, f"{required} was not exercised by the smoke test"


# --- 3. the three B1 actions round-trip through the UI --------------------


def test_toggling_a_tag_adds_then_removes_it(browser: Client, case: Case) -> None:
    tag = Tag.objects.create(name="phishing")
    url = reverse("ui-case-tag-toggle", kwargs={"case_id": case.number, "tag_id": tag.id})

    assert browser.post(url).status_code == 302
    assert case.tags.filter(pk=tag.id).exists()

    assert browser.post(url).status_code == 302
    assert not case.tags.filter(pk=tag.id).exists()


def test_uploading_a_file_creates_an_attachment(browser: Client, case: Case) -> None:
    response = browser.post(
        reverse("ui-case-attachment-upload", kwargs={"case_id": case.id}),
        {"attachments": SimpleUploadedFile("x.txt", b"hello", content_type="text/plain")},
    )

    assert response.status_code == 302
    assert Attachment.objects.filter(case=case, name="x.txt").exists()


def test_deleting_an_attachment_removes_the_row_and_blob(browser: Client, case: Case) -> None:
    browser.post(
        reverse("ui-case-attachment-upload", kwargs={"case_id": case.id}),
        {"attachments": SimpleUploadedFile("x.txt", b"hello", content_type="text/plain")},
    )
    attachment = Attachment.objects.get(case=case)
    path = attachment.path
    assert default_storage.exists(path)

    response = browser.post(
        reverse(
            "ui-case-attachment-delete",
            kwargs={"case_id": case.id, "attachment_id": attachment.id},
        )
    )

    assert response.status_code == 302
    assert not Attachment.objects.filter(pk=attachment.id).exists()
    assert not default_storage.exists(path), "the blob outlived its row"

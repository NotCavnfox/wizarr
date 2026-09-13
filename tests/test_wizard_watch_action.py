"""The last Jellyfin step offers a safe exit without changing wizard navigation."""

import frontmatter
import pytest

from app.blueprints.wizard.routes import _serve_wizard
from app.models import MediaServer


@pytest.mark.parametrize("htmx", [False, True])
@pytest.mark.parametrize(
    ("phase", "step_phase", "idx", "visible"),
    [
        ("post", None, 1, True),
        ("post", None, 0, False),
        ("pre", None, 1, False),
        ("preview", "post", 1, True),
        ("preview", "pre", 1, False),
    ],
)
def test_watch_action_only_on_final_post_step(
    app, session, htmx, phase, step_phase, idx, visible
):
    session.add(
        MediaServer(
            name="Library",
            server_type="jellyfin",
            url="http://172.20.0.23:8096",
            external_url="https://watch.example.com/jellyfin/",
        )
    )
    session.commit()
    headers = {"HX-Request": "true"} if htmx else {}
    with app.test_request_context(headers=headers):
        result = _serve_wizard(
            "jellyfin",
            idx,
            [frontmatter.Post("First step"), frontmatter.Post("Last step")],
            phase,
            current_step_phase=step_phase,
        )
        html = result.get_data(as_text=True) if htmx else result
    assert ('href="https://watch.example.com/jellyfin/"' in html) is visible
    assert ("Watch in Jellyfin" in html) is visible
    assert "172.20.0.23" not in html
    if visible:
        assert 'rel="noreferrer"' in html


@pytest.mark.parametrize(
    "external_url",
    [
        None,
        "",
        "javascript:alert(1)",
        "//watch.example.com",
        "http://watch.example.com",
        "https://name:secret@watch.example.com",
        "https://watch.example.com/?api_key=secret",
        "https://watch.example.com/#secret",
        "https://watch.example.com/\n",
        "https://watch.example.com\\@other.example.com",
        "https://[broken",
        "https://watch.example.com:bad/",
        "\x01https://watch.example.com/",
    ],
)
def test_no_action_or_internal_fallback_for_unsafe_client_url(
    app, session, external_url
):
    session.add(
        MediaServer(
            name="Library",
            server_type="jellyfin",
            url="http://172.20.0.23:8096",
            external_url=external_url,
        )
    )
    session.commit()
    with app.test_request_context(headers={"HX-Request": "true"}):
        result = _serve_wizard("jellyfin", 0, [frontmatter.Post("Last step")], "post")
    html = result.get_data(as_text=True)
    assert "Watch in Jellyfin" not in html
    assert "172.20.0.23" not in html

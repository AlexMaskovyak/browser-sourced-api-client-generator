"""Select one reproducible GET path from an authenticated page."""

from website_client.page_link import PageLink, first_page_link


def test_first_eligible_link_wins_in_document_order():
    website = "https://example.test"
    dashboard = website + "/app"
    anchors = [
        "https://elsewhere.test/guide",
        "/logout",
        "/team/123",
        "/team?key=private",
        "/app#settings",
        "/app",
        "/team/guide",
        "/another-page",
    ]

    assert first_page_link(website, dashboard, anchors) == PageLink("/team/guide")


def test_foreign_dashboard_cannot_supply_a_link_back_to_the_login_site():
    website = "https://example.test"
    assert first_page_link(website, "https://elsewhere.test/app", [website + "/guide"]) is None

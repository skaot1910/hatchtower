"""Installable web app (Android): manifest and service worker are public and served from the root."""


def test_manifest_und_service_worker(world):
    c = world.client(csrf=False)
    m = c.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    sizes = {i["sizes"] for i in m.json()["icons"]}
    assert {"192x192", "512x512"} <= sizes and m.json()["display"] == "standalone"
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["content-type"].startswith("text/javascript")
    assert "addEventListener(\"fetch\"" in sw.text and sw.headers["cache-control"] == "no-cache"
    page = world.client(world.ids["olga"]).get("/").text
    assert 'rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"' in page


def test_statische_dateien_lange_im_cache_und_komprimiert(world):
    """CSS/JS are ~200 KB: versioned URLs stay in the browser cache, the files go out compressed."""
    import re
    c = world.client(world.ids["olga"])
    page = c.get("/")
    assert page.headers["cache-control"] == "no-store" and "content-encoding" not in page.headers
    tag = re.search(r'/static/app\.js\?v=(\w+)"', page.text).group(1)
    js = c.get(f"/static/app.js?v={tag}", headers={"Accept-Encoding": "gzip"})
    assert js.status_code == 200 and js.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert js.headers["content-encoding"] == "gzip" and "function" in js.text
    assert "content-security-policy" in js.headers
    assert c.get("/static/app.js").headers["cache-control"] == "no-cache"
    missing = c.get("/static/fehlt.js?v=1")
    assert missing.status_code == 404 and missing.headers["cache-control"] == "no-cache"


def test_cache_parameter_aendert_sich_mit_dem_dateiinhalt(monkeypatch):
    """A deploy without a new version number must still reach browsers that cache /static/ for a year."""
    from hatchtower import main
    tag = main.templates.env.filters["hash_version"]
    before = tag("1.0")
    monkeypatch.setattr(main, "_STATIC_DIGEST", "anderer-inhalt")
    assert tag("1.0") != before

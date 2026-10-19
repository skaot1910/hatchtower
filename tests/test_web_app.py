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

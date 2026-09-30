"""PWA manifest and service worker: installable on the phone home screen."""


def test_manifest_has_required_fields(client):
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    body = response.json()
    assert body["name"] == "Midas's Hoard" and body["short_name"] == "Midas"
    assert body["start_url"] == "/" and body["display"] == "standalone"
    assert body["background_color"] and body["theme_color"]
    sizes = {icon["sizes"] for icon in body["icons"]}
    assert {"192x192", "512x512"} <= sizes


def test_service_worker_is_js_and_installable_at_root(client):
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["service-worker-allowed"] == "/"
    body = response.text
    assert "skipWaiting" in body and "clients.claim" in body and "/api/" in body and "/assets/" in body

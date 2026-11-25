from fastapi import FastAPI
from fastapi.testclient import TestClient

from litkb.api.app import SPAStaticFiles


def test_spa_fallback(tmp_path):
    (tmp_path / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "a.js").write_text("x", encoding="utf-8")
    app = FastAPI()
    app.mount("/", SPAStaticFiles(directory=tmp_path, html=True))
    c = TestClient(app)
    assert c.get("/p/demo/map").text == "<html>app</html>"     # client-side route -> index.html
    assert c.get("/assets/a.js").text == "x"
    assert c.get("/assets/missing.js").status_code == 404      # missing assets and API paths stay 404
    assert c.get("/api/nope").status_code == 404

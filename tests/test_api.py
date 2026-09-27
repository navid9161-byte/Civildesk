from fastapi.testclient import TestClient

from civildesk.main import app


def test_api_crud_flow():
    with TestClient(app) as c:
        meta = c.get("/api/meta").json()
        assert "tasks" in meta["schema"]
        assert c.get("/").status_code == 200

        p = c.post("/api/projects", json={"name": "سوله صنعتی", "contract_amount": "5,000,000"}).json()
        assert p["contract_amount"] == 5_000_000
        t = c.post("/api/tasks", json={"title": "تست بتن", "project_id": p["id"], "due_date": meta["today"]}).json()

        r = c.post("/api/tasks", json={"title": "", "due_date": "bad"})
        assert r.status_code == 400 and "detail" in r.json()

        assert c.patch(f"/api/tasks/{t['id']}", json={"status": "done"}).json()["status"] == "done"
        assert c.get("/api/tasks", params={"include_closed": "false"}).json() == []
        assert len(c.get("/api/tasks", params={"status": "done"}).json()) == 1
        assert c.get("/api/dashboard").status_code == 200
        assert c.get(f"/api/projects/{p['id']}/overview").json()["project"]["name"] == "سوله صنعتی"
        assert c.get("/api/finance/summary").status_code == 200
        assert "tasks" in c.get("/api/export").json()
        assert c.delete(f"/api/tasks/{t['id']}").json() == {"ok": True}
        assert c.get(f"/api/tasks/{t['id']}").status_code == 404
        assert c.get("/api/nope").status_code == 404

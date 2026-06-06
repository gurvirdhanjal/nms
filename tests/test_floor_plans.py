"""Tests for floor-plan geotagging API + agent connection-type detection."""
import io

import pytest

from extensions import db
from models.site import Site
from models.device import Device
from models.floor_plan import FloorPlan


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _png_bytes(w=120, h=80):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (40, 60, 90)).save(buf, format="PNG")
    buf.seek(0)
    return buf


def _alpha_site_id():
    return Site.query.filter_by(site_name="Alpha Site").first().id


def _make_device(name="WS-01", site_id=None, device_type="server", parent_switch_id=None):
    dev = Device(
        device_name=name,
        device_type=device_type,
        device_ip=None,
        site_id=site_id,
        parent_switch_id=parent_switch_id,
    )
    db.session.add(dev)
    db.session.commit()
    return dev


def _upload_plan(admin_client, site_id, name="Ground Floor"):
    return admin_client.post(
        f"/api/sites/{site_id}/floor-plans",
        data={"name": name, "file": (_png_bytes(), "plan.png")},
        content_type="multipart/form-data",
    )


# --------------------------------------------------------------------------- #
# upload / validation
# --------------------------------------------------------------------------- #
def test_upload_png_creates_plan(admin_client):
    site_id = _alpha_site_id()
    res = _upload_plan(admin_client, site_id)
    assert res.status_code == 201
    data = res.get_json()["data"]
    assert data["name"] == "Ground Floor"
    assert data["image_width"] == 120 and data["image_height"] == 80
    assert data["version"] == 1
    assert data["image_url"].startswith(f"/api/floor-plans/{data['id']}/image")


def test_upload_rejects_unsupported_extension(admin_client):
    site_id = _alpha_site_id()
    res = admin_client.post(
        f"/api/sites/{site_id}/floor-plans",
        data={"name": "Bad", "file": (io.BytesIO(b"hello"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    assert "Unsupported" in res.get_json()["message"]


def test_upload_pdf_rasterises_first_page(admin_client):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    doc.new_page(width=400, height=260)
    doc.new_page(width=400, height=260)  # 2nd page must be ignored
    pdf = io.BytesIO(doc.tobytes())
    doc.close()
    pdf.seek(0)
    site_id = _alpha_site_id()
    res = admin_client.post(
        f"/api/sites/{site_id}/floor-plans",
        data={"name": "PDF Plan", "file": (pdf, "plan.pdf")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 201
    data = res.get_json()["data"]
    assert data["mime_type"] == "image/png"
    assert data["image_width"] > 0 and data["image_height"] > 0


def test_viewer_cannot_upload(viewer_client):
    site_id = _alpha_site_id()
    res = viewer_client.post(
        f"/api/sites/{site_id}/floor-plans",
        data={"name": "Nope", "file": (_png_bytes(), "plan.png")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 403


def test_image_served_behind_auth(admin_client, client):
    site_id = _alpha_site_id()
    plan_id = _upload_plan(admin_client, site_id).get_json()["data"]["id"]
    # authenticated admin can fetch
    ok = admin_client.get(f"/api/floor-plans/{plan_id}/image")
    assert ok.status_code == 200
    assert ok.mimetype == "image/png"
    # anonymous client cannot
    anon = client.get(f"/api/floor-plans/{plan_id}/image")
    assert anon.status_code in (401, 302)


# --------------------------------------------------------------------------- #
# placements
# --------------------------------------------------------------------------- #
def test_place_move_and_unplace_device(admin_client):
    site_id = _alpha_site_id()
    plan_id = _upload_plan(admin_client, site_id).get_json()["data"]["id"]
    dev = _make_device(site_id=site_id)

    # place
    res = admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": dev.device_id, "map_x": 25, "map_y": 60}]},
    )
    assert res.status_code == 200 and res.get_json()["updated"] == 1
    db.session.expire_all()
    placed = Device.query.get(dev.device_id)
    assert placed.floor_plan_id == plan_id
    assert placed.map_x == 25 and placed.map_y == 60

    # plan GET reflects placement
    got = admin_client.get(f"/api/floor-plans/{plan_id}").get_json()["data"]
    assert len(got["placed_devices"]) == 1

    # unplace (null coords)
    admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": dev.device_id, "map_x": None, "map_y": None}]},
    )
    db.session.expire_all()
    assert Device.query.get(dev.device_id).floor_plan_id is None


def test_coordinates_are_clamped(admin_client):
    site_id = _alpha_site_id()
    plan_id = _upload_plan(admin_client, site_id).get_json()["data"]["id"]
    dev = _make_device(site_id=site_id)
    admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": dev.device_id, "map_x": 250, "map_y": -30}]},
    )
    db.session.expire_all()
    placed = Device.query.get(dev.device_id)
    assert placed.map_x == 100 and placed.map_y == 0


def test_locked_marker_not_moved_without_force(admin_client):
    site_id = _alpha_site_id()
    plan_id = _upload_plan(admin_client, site_id).get_json()["data"]["id"]
    dev = _make_device(site_id=site_id)

    # place + lock
    admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": dev.device_id, "map_x": 10, "map_y": 10, "map_locked": True}]},
    )
    # attempt to move without force -> skipped
    res = admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": dev.device_id, "map_x": 90, "map_y": 90}]},
    )
    body = res.get_json()
    assert dev.device_id in body["skipped_locked"]
    db.session.expire_all()
    placed = Device.query.get(dev.device_id)
    assert placed.map_x == 10 and placed.map_y == 10  # unchanged

    # with force -> moved
    admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": dev.device_id, "map_x": 90, "map_y": 90}], "force": True},
    )
    db.session.expire_all()
    assert Device.query.get(dev.device_id).map_x == 90


def test_delete_plan_clears_placements(admin_client):
    site_id = _alpha_site_id()
    plan_id = _upload_plan(admin_client, site_id).get_json()["data"]["id"]
    dev = _make_device(site_id=site_id)
    admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": dev.device_id, "map_x": 25, "map_y": 60}]},
    )
    res = admin_client.delete(f"/api/floor-plans/{plan_id}")
    assert res.status_code == 200
    db.session.expire_all()
    placed = Device.query.get(dev.device_id)
    assert placed.floor_plan_id is None and placed.map_x is None
    assert FloorPlan.query.get(plan_id) is None


def test_replace_image_bumps_version_keeps_placements(admin_client):
    site_id = _alpha_site_id()
    plan_id = _upload_plan(admin_client, site_id).get_json()["data"]["id"]
    dev = _make_device(site_id=site_id)
    admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": dev.device_id, "map_x": 25, "map_y": 60}]},
    )
    res = admin_client.put(
        f"/api/floor-plans/{plan_id}",
        data={"file": (_png_bytes(200, 150), "plan2.png")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    body = res.get_json()["data"]
    assert body["version"] == 2 and body["image_width"] == 200
    db.session.expire_all()
    placed = Device.query.get(dev.device_id)
    assert placed.map_x == 25 and placed.map_y == 60  # coords preserved


# --------------------------------------------------------------------------- #
# suggestions (SNMP accelerator)
# --------------------------------------------------------------------------- #
def test_suggestions_surface_downstream_of_placed_switch(admin_client):
    site_id = _alpha_site_id()
    plan_id = _upload_plan(admin_client, site_id).get_json()["data"]["id"]
    switch = _make_device(name="CoreSwitch", site_id=site_id, device_type="switch")
    child = _make_device(name="AP-1", site_id=site_id, parent_switch_id=switch.device_id)

    # place the switch
    admin_client.post(
        f"/api/floor-plans/{plan_id}/placements",
        json={"placements": [{"device_id": switch.device_id, "map_x": 50, "map_y": 50}]},
    )
    res = admin_client.get(f"/api/floor-plans/{plan_id}/suggestions")
    assert res.status_code == 200
    data = res.get_json()["data"]
    ids = [s["device_id"] for s in data]
    assert child.device_id in ids
    sugg = next(s for s in data if s["device_id"] == child.device_id)
    assert sugg["parent_switch_name"] == "CoreSwitch"
    assert sugg["switch_x"] == 50


# --------------------------------------------------------------------------- #
# connection-type detector (agent-side)
# --------------------------------------------------------------------------- #
def test_detect_connection_type_never_raises():
    import server_agent
    assert server_agent.detect_connection_type(None) in ("wifi", "lan", "unknown")
    assert server_agent.detect_connection_type("203.0.113.7") in ("wifi", "lan", "unknown")


def test_classify_interface_name_heuristics(monkeypatch):
    import server_agent
    # Neutralise OS-specific branches so the name heuristic is exercised.
    monkeypatch.setattr(server_agent.platform, "system", lambda: "TestOS")
    assert server_agent._classify_interface("wlan0") == "wifi"
    assert server_agent._classify_interface("Wi-Fi") == "wifi"
    assert server_agent._classify_interface("eth0") == "lan"
    assert server_agent._classify_interface("Ethernet") == "lan"
    assert server_agent._classify_interface("tun0") == "unknown"


def test_device_to_dict_exposes_connection_type():
    site_id = _alpha_site_id()
    dev = _make_device(site_id=site_id)
    dev.connection_type = "wifi"
    db.session.commit()
    d = Device.query.get(dev.device_id).to_dict()
    assert d["connection_type"] == "wifi"
    assert "map_locked" in d and d["map_locked"] is False

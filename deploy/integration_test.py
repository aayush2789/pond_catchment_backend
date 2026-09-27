"""Live integration tests against the load-balanced pond deployment.

Run from the repo root:  python deploy/integration_test.py [BASE_URL]
Default BASE_URL = http://10.1.75.53:3309  (Nginx -> 4 pond API nodes)
"""

import io
import json
import sys
import time
import zipfile

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://10.1.75.53:3309"
LAND = {
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [
                [81.290, 21.245],
                [81.296, 21.245],
                [81.296, 21.250],
                [81.290, 21.250],
                [81.290, 21.245],
            ]
        ],
    }
}

KML = b"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark><name>100.0</name>
      <LineString><coordinates>77.1025,28.7041 77.1040,28.7048</coordinates></LineString></Placemark>
    <Placemark><name>95.0</name>
      <LineString><coordinates>77.1018,28.7034 77.1035,28.7042</coordinates></LineString></Placemark>
  </Document>
</kml>"""

results = []


def request_with_retry(client, method, url, attempts=4, **kwargs):
    """The lab link intermittently drops connections; retry transient failures."""
    last_exc = None
    for i in range(attempts):
        try:
            return client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            time.sleep(2 * (i + 1))
    raise last_exc


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main():
    client = httpx.Client(timeout=httpx.Timeout(30, read=300))

    # 1. Load-balancer distribution over /version
    seen = {}
    for _ in range(8):
        r = request_with_retry(client, "GET", f"{BASE}/api/v1/version")
        node = r.json().get("node")
        seen[node] = seen.get(node, 0) + 1
    check("LB distributes across nodes", len(seen) >= 2, f"distribution={seen}")

    # 2. Readiness + version metadata sanity
    r = request_with_retry(client, "GET", f"{BASE}/api/v1/ready")
    check("ready endpoint", r.status_code == 200 and r.json().get("status") == "ready",
          str(r.json()))
    r = request_with_retry(client, "GET", f"{BASE}/api/v1/version")
    check("version metadata present", bool(r.json().get("git_commit")), str(r.json()))

    # 3. Shared cache: terrainPreview twice (round-robin may hit different nodes)
    r1 = request_with_retry(client, "POST", f"{BASE}/api/v1/terrainPreview", json=LAND)
    t1 = time.perf_counter()
    r2 = request_with_retry(client, "POST", f"{BASE}/api/v1/terrainPreview", json=LAND)
    dt = (time.perf_counter() - t1) * 1000
    check("terrain analysis works via LB", r1.status_code == 200)
    check("repeat served from shared cache", r2.json()["dem"]["cache_hit"] is True,
          f"{dt:.0f} ms")
    check("identical results for same request",
          r1.json()["dem"]["mean_elevation_m"] == r2.json()["dem"]["mean_elevation_m"])

    # 4. Expensive unified endpoint through nginx (GeoJSON path), twice
    f = {"request": (None, json.dumps(LAND))}
    a1 = request_with_retry(client, "POST", f"{BASE}/api/v1/analyzePondSite", files=f)
    check("analyzePondSite (GeoJSON) via LB", a1.status_code == 200,
          f"pond=({a1.json()['pond']['latitude']}, {a1.json()['pond']['longitude']})")
    t2 = time.perf_counter()
    a2 = request_with_retry(client, "POST", f"{BASE}/api/v1/analyzePondSite", files=f)
    dt2 = time.perf_counter() - t2
    same = (
        a2.json()["pond"]["latitude"] == a1.json()["pond"]["latitude"]
        and a2.json()["water"]["expected_collectible_water_m3"]
        == a1.json()["water"]["expected_collectible_water_m3"]
    )
    check("analyzePondSite deterministic + cached", same, f"second call {dt2:.1f} s")

    # 5. KML/KMZ fallback path through nginx
    kmz_buf = io.BytesIO()
    with zipfile.ZipFile(kmz_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("village.kml", KML)
    f_kml = {"file": ("village.kmz", kmz_buf.getvalue(), "application/vnd.google-earth.kmz")}
    r_kml = request_with_retry(client, "POST", f"{BASE}/api/v1/analyzePondSite", files=f_kml)
    check("analyzePondSite (KMZ fallback) via LB",
          r_kml.status_code == 200 and r_kml.json()["selected_land"] is None)

    # 6. Proxy headers / request tracing
    r = request_with_retry(client, "GET", f"{BASE}/api/v1/health",
                           headers={"X-Request-ID": "integration-77"})
    check("X-Request-ID preserved through proxy",
          r.headers.get("x-request-id") == "integration-77")
    check("X-Served-By node header present", bool(r.headers.get("x-served-by")))

    # 7. Existing catchment endpoint unchanged (KML path, backwards compat)
    files = {"file": ("contours.kml", KML, "application/vnd.google-earth.kml+xml")}
    r_c = request_with_retry(client, "POST", f"{BASE}/api/v1/findCatchment", files=files)
    check("legacy /findCatchment still works", r_c.status_code == 200)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)


if __name__ == "__main__":
    main()

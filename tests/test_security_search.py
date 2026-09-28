import sys
import urllib.request
import urllib.error
import json

sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:8000"

def run_tests():
    print("=== 0. Obtain Auth Token ===")
    login_req = urllib.request.Request(
        f"{BASE_URL}/api/auth/login",
        data=json.dumps({"username": "harshsamrat", "password": "222333", "device_id": "test_sec_check"}).encode('utf-8'),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(login_req) as l_resp:
        l_data = json.loads(l_resp.read().decode('utf-8'))
        token = l_data["token"]
        print(f"  Auth Token obtained: {token[:8]}...")

    auth_headers = {"Authorization": f"Bearer {token}"}

    print("=== 1. Test /search page ===")
    req = urllib.request.Request(f"{BASE_URL}/search")
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode('utf-8')
        assert resp.status == 200
        assert "<h1>मतदाता सेवा</h1>" in html, "Heading should be मतदाता सेवा"
        assert "exportExcelBtn" not in html, "Excel export button should be removed"
        assert "desktop-only-admin" in html, "Admin button should have desktop-only-admin class"
        print(f"  /search OK! Length={len(html)} bytes, Heading='मतदाता सेवा', Excel button removed, Desktop-only admin verified")

    print("\n=== 2. Test /api/database/slip/{id} ===")
    search_req = urllib.request.Request(f"{BASE_URL}/api/database/search?limit=1", headers=auth_headers)
    with urllib.request.urlopen(search_req) as s_resp:
        s_data = json.loads(s_resp.read().decode('utf-8'))
        first_id = s_data['records'][0]['id']
        
    req = urllib.request.Request(f"{BASE_URL}/api/database/slip/{first_id}", headers=auth_headers)
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        assert resp.status == 200
        slip = data.get("slip", {})
        print(f"  Voter Slip OK! ID={first_id}, Name={slip.get('name')}, Serial={slip.get('serial_no')}, Booth={slip.get('part_no')}")
        assert "name" in slip

    print("\n=== 3. Test Security Barrier: Authenticated Public IP calling GET /api/database/search ===")
    req = urllib.request.Request(f"{BASE_URL}/api/database/search?limit=5", headers=auth_headers)
    req.add_header("CF-Connecting-IP", "198.51.100.55")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode('utf-8'))
        print(f"  Public search allowed! Records={len(data.get('records', []))}")

    print("\n=== 4. Test Security Barrier: Public IP calling DELETE /api/database/clear ===")
    req = urllib.request.Request(f"{BASE_URL}/api/database/clear", method="DELETE")
    req.add_header("CF-Connecting-IP", "198.51.100.55")
    try:
        urllib.request.urlopen(req)
        assert False, "Should have been rejected with 403!"
    except urllib.error.HTTPError as e:
        print(f"  Public DELETE correctly blocked with HTTP {e.code}: {e.read().decode('utf-8')}")
        assert e.code == 403

    print("\n=== 5. Test Security Barrier: Public IP calling POST /api/upload ===")
    req = urllib.request.Request(f"{BASE_URL}/api/upload", data=b"dummy", method="POST")
    req.add_header("X-Forwarded-For", "203.0.113.195")
    try:
        urllib.request.urlopen(req)
        assert False, "Should have been rejected with 403!"
    except urllib.error.HTTPError as e:
        print(f"  Public POST upload correctly blocked with HTTP {e.code}: {e.read().decode('utf-8')}")
        assert e.code == 403

    print("\n=== 6. Test Security Barrier: Public IP with Valid User Session ===")
    req = urllib.request.Request(f"{BASE_URL}/api/database/stats", headers=auth_headers)
    req.add_header("CF-Connecting-IP", "198.51.100.55")
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        print(f"  Admin token authenticated successfully! Status={resp.status}")

    print("\n=================================================")
    print("ALL SEARCH & SECURITY BARRIER TESTS PASSED 100%!")
    print("=================================================")

if __name__ == "__main__":
    run_tests()

"""OVHcloud VPS (signed API: application key + secret + consumer key). VPSes are bought through the order cart and billed
monthly; the SSH key is injected with a rebuild after delivery, and termination needs a token OVH emails to the account."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Callable, Optional
from urllib.parse import urlencode

import httpx

from .base import Account, HttpProvider, Plan, ProviderError, Region, VpsHandle, VpsSpec

ENDPOINTS = {"us": ("https://api.us.ovhcloud.com/1.0", "US"), "ca": ("https://ca.api.ovh.com/1.0", "CA"),
             "eu": ("https://eu.api.ovh.com/1.0", "FR")}
CREATE_TOKEN_URL = {"us": "https://api.us.ovhcloud.com/createToken", "ca": "https://ca.api.ovh.com/createToken",
                    "eu": "https://eu.api.ovh.com/createToken"}
# rights the consumer key needs (createToken form); nothing outside ordering and VPS management
RIGHTS = [("GET", "/me"), ("GET", "/me/order/*"), ("GET", "/order/*"), ("POST", "/order/cart"), ("POST", "/order/cart/*"),
          ("GET", "/vps"), ("GET", "/vps/*"), ("POST", "/vps/*/rebuild"), ("POST", "/vps/*/terminate"),
          ("POST", "/vps/*/confirmTermination")]
# datacenter code -> readable place (city first, so select.py's name hints find the country)
PLACES = {"US-EAST-VA": "Vint Hill, Virginia", "US-WEST-OR": "Hillsboro, Oregon", "US-EAST-LZ-ATL": "Atlanta (Local Zone)",
          "US-EAST-LZ-DAL": "Dallas (Local Zone)", "US-EAST-LZ-MIA": "Miami (Local Zone)", "US-EAST-LZ-NYC": "New York (Local Zone)",
          "US-WEST-LZ-DEN": "Denver (Local Zone)", "US-WEST-LZ-LAX": "Los Angeles (Local Zone)",
          "US-WEST-LZ-PAO": "Palo Alto (Local Zone)", "US-WEST-LZ-SEA": "Seattle (Local Zone)", "CA-EAST-BHS": "Beauharnois",
          "CA-EAST-TOR": "Toronto", "EU-WEST-GRA": "Gravelines", "EU-WEST-SBG": "Strasbourg", "EU-WEST-RBX": "Roubaix",
          "EU-CENTRAL-WAW": "Warsaw", "EU-WEST-LON": "London", "EU-WEST-ERI": "London (Erith)", "EU-CENTRAL-FRA": "Frankfurt",
          "AP-SOUTHEAST-SGP": "Singapore", "AP-SOUTHEAST-SYD": "Sydney", "AP-SOUTH-MUM": "Mumbai"}
_SPEC = re.compile(r"(\d+)\s*vCPU\s+(\d+)\s*GB RAM\s+(\d+)\s*GB", re.I)
_FAMILY = re.compile(r"^vps-20\d\d(-lz)?$")
_SKIP = re.compile(r"degressivity|percent|-eu$|-ca$|resell", re.I)
TERMINATE_NOTE = ("OVH emails a confirmation link to the account owner; the server stops at the end of the paid month "
                  "once it is confirmed (or confirm with the token: heardos-installer ovh-confirm <service> <token>).")


def parse_token(token: str) -> dict:
    """The vault stores one JSON string: {"endpoint", "app_key", "app_secret", "consumer_key"}."""
    try:
        d = json.loads(token) if token else {}
    except ValueError:
        raise ProviderError("ovh: the saved credentials are not valid JSON")
    for k in ("app_key", "app_secret", "consumer_key"):
        if not d.get(k):
            raise ProviderError(f"ovh: missing {k}")
    if d.get("endpoint", "us") not in ENDPOINTS:
        raise ProviderError(f"ovh: endpoint must be one of {sorted(ENDPOINTS)}")
    return d


def signature(app_secret: str, consumer_key: str, method: str, url: str, body: str, ts: int) -> str:
    raw = "+".join([app_secret, consumer_key, method.upper(), url, body, str(ts)])
    return "$1$" + hashlib.sha1(raw.encode()).hexdigest()


class Ovh(HttpProvider):
    name = "ovh"
    retries = 4

    def __init__(self, token: str, client: Optional[httpx.Client] = None, key_path=None):
        super().__init__(token, client)
        c = parse_token(token)
        self.endpoint = c.get("endpoint", "us")
        self.base_url, self.subsidiary = ENDPOINTS[self.endpoint]
        self.app_key, self.app_secret, self.consumer_key = c["app_key"], c["app_secret"], c["consumer_key"]
        self.key_path = key_path
        self._delta: Optional[int] = None
        self._catalog: Optional[dict] = None
        self._stock: dict[str, dict[str, bool]] = {}
        self.destroy_note = ""

    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "User-Agent": "heardos-installer/0.1"}

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(headers=self.headers(), timeout=self.timeout)
        return self._client

    def _now(self) -> int:
        if self._delta is None:
            r = self.client.get(f"{self.base_url}/auth/time")
            self._delta = int(r.text.strip()) - int(time.time()) if r.status_code == 200 else 0
        return int(time.time()) + self._delta

    def _json(self, method: str, path: str, params: Optional[dict] = None, json_body=None, auth: bool = True, **_):
        url = f"{self.base_url}{path}" + (f"?{urlencode(params)}" if params else "")
        body = json.dumps(json_body, separators=(",", ":")) if json_body is not None else ""
        r = None
        for attempt in range(self.retries):
            h = {"Content-Type": "application/json"} if body else {}
            if auth:
                ts = self._now()
                h.update({"X-Ovh-Application": self.app_key, "X-Ovh-Consumer": self.consumer_key, "X-Ovh-Timestamp": str(ts),
                          "X-Ovh-Signature": signature(self.app_secret, self.consumer_key, method, url, body, ts)})
            try:
                r = self.client.request(method, url, content=body or None, headers=h)
            except httpx.HTTPError as e:
                if attempt == self.retries - 1:
                    raise ProviderError(f"ovh: network error on {method} {path}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            # a checkout or rebuild that 5xx'd may still have gone through: never repeat those
            if r.status_code >= 500 and attempt < self.retries - 1 and not (method == "POST" and path.endswith(("/checkout", "/rebuild"))):
                time.sleep(2 ** attempt)
                continue
            break
        if r.status_code in (401, 403):
            raise ProviderError(f"ovh: the API keys were rejected or lack a right ({r.status_code}): {r.text[:200]}")
        if r.status_code >= 400:
            raise ProviderError(f"ovh: {method} {path} -> {r.status_code}: {r.text[:300]}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            raise ProviderError(f"ovh: non-JSON response from {path}: {r.text[:200]}")

    # --- catalog -----------------------------------------------------------------
    def catalog(self) -> dict:
        if self._catalog is None:
            self._catalog = self._json("GET", "/order/catalog/public/vps", params={"ovhSubsidiary": self.subsidiary}, auth=False)
        return self._catalog

    @staticmethod
    def _renew(pricings: list, mode: str = "default") -> float:
        for x in pricings or []:
            if x.get("mode") == mode and "renew" in (x.get("capacities") or []) and x.get("interval") == 1:
                return x.get("price", 0) / 1e8
        return 0.0

    @staticmethod
    def _conf(plan: dict, name: str) -> list[str]:
        return next((c.get("values") or [] for c in plan.get("configurations") or [] if c.get("name") == name), [])

    def _mandatory_addons(self, plan: dict) -> list[tuple[str, float]]:
        """Cheapest addon of every mandatory family (OS, storage, the Local Zone's automated backup)."""
        prices = {a["planCode"]: self._renew(a.get("pricings")) for a in self.catalog().get("addons") or []}
        out = []
        for fam in plan.get("addonFamilies") or []:
            if fam.get("mandatory") and fam.get("addons"):
                code = min(fam["addons"], key=lambda c: prices.get(c, 0.0))
                out.append((code, prices.get(code, 0.0)))
        return out

    def _plans(self) -> list[dict]:
        products = {p["name"]: p.get("description", "") for p in self.catalog().get("products") or []}
        out = []
        for p in self.catalog().get("plans") or []:
            m = _SPEC.search(products.get(p.get("product"), ""))
            # only the current range (family vps-2025 / vps-2025-lz); older codes map onto other hardware or are upgrade paths
            if not m or _SKIP.search(p["planCode"]) or not _FAMILY.match(p.get("family") or "") or not self._conf(p, "vps_datacenter") or not self._renew(p.get("pricings")):
                continue
            vcpu, ram, disk = (int(x) for x in m.groups())
            out.append({"raw": p, "vcpu": vcpu, "ram_mb": ram * 1024, "disk_gb": disk,
                        "monthly": round(self._renew(p.get("pricings")) + sum(c for _, c in self._mandatory_addons(p)), 2)})
        return out

    def stock(self, plan_code: str) -> dict[str, bool]:
        if plan_code not in self._stock:
            d = self._json("GET", "/vps/order/rule/datacenter", params={"ovhSubsidiary": self.subsidiary, "planCode": plan_code}, auth=False)
            self._stock[plan_code] = {x["datacenter"]: x.get("linuxStatus", x.get("status")) == "available" for x in d.get("datacenters") or []}
        return self._stock[plan_code]

    # --- the seam ----------------------------------------------------------------
    def validate_token(self) -> Account:
        me = self._json("GET", "/me")
        return Account(label=f"OVHcloud {self.endpoint.upper()}: {me.get('email') or me.get('nichandle')}",
                       extra={"nichandle": me.get("nichandle"), "currency": (me.get("currency") or {}).get("code")})

    def regions(self) -> list[Region]:
        seen: dict[str, bool] = {}
        for p in self._plans():
            st = self.stock(p["raw"]["planCode"])
            for dc in self._conf(p["raw"], "vps_datacenter"):
                seen[dc] = seen.get(dc, False) or st.get(dc, False)
        return [Region(id=dc, name=PLACES.get(dc, dc), country=dc.split("-")[0] if dc[:3] in ("US-", "CA-") else "", available=ok)
                for dc, ok in sorted(seen.items())]

    def plans(self, region_id: str) -> list[Plan]:
        out = []
        for p in self._plans():
            code = p["raw"]["planCode"]
            if region_id not in self._conf(p["raw"], "vps_datacenter") or not self.stock(code).get(region_id):
                continue
            out.append(Plan(id=code, name=f"{p['raw'].get('invoiceName', code)} ({p['vcpu']} vCPU / {p['ram_mb'] // 1024} GB)",
                            vcpu=p["vcpu"], ram_mb=p["ram_mb"], disk_gb=p["disk_gb"], monthly_usd=p["monthly"], region_ids=[region_id]))
        return out

    def ubuntu_image(self) -> str:
        """An OS label for the order (vps_os); the rebuild later picks the matching image id."""
        return "Ubuntu 24.04"

    def ensure_ssh_key(self, name: str, pubkey: str) -> str:
        return pubkey.strip()    # OVH takes the key itself at rebuild time

    def create(self, spec: VpsSpec) -> VpsHandle:
        plan = next((p["raw"] for p in self._plans() if p["raw"]["planCode"] == spec.plan_id), None)
        if not plan:
            raise ProviderError(f"ovh: plan {spec.plan_id} is not in the {self.subsidiary} catalog")
        os_label = spec.image_id if spec.image_id in self._conf(plan, "vps_os") else "Debian 12"
        before = set(self._json("GET", "/vps") or [])
        cart = self._json("POST", "/order/cart", json_body={"ovhSubsidiary": self.subsidiary, "description": spec.label})["cartId"]
        item = self._json("POST", f"/order/cart/{cart}/vps", json_body={"planCode": spec.plan_id, "duration": "P1M",
                                                                        "pricingMode": "default", "quantity": 1})["itemId"]
        for label, value in (("vps_datacenter", spec.region_id), ("vps_os", os_label)):
            self._json("POST", f"/order/cart/{cart}/item/{item}/configuration", json_body={"label": label, "value": value})
        for code, _ in self._mandatory_addons(plan):
            self._json("POST", f"/order/cart/{cart}/vps/options", json_body={"itemId": item, "planCode": code, "duration": "P1M",
                                                                             "pricingMode": "default", "quantity": 1})
        self._json("POST", f"/order/cart/{cart}/assign")
        order = self._json("POST", f"/order/cart/{cart}/checkout", json_body={"autoPayWithPreferredPaymentMethod": True,
                                                                              "waiveRetractationPeriod": False})
        oid = order.get("orderId")
        total = ((order.get("prices") or {}).get("withTax") or {}).get("text", "")
        return VpsHandle(self.name, f"order-{oid}", extra={"orderId": oid, "before": sorted(before), "os": os_label,
                                                           "pubkey": spec.ssh_key_id, "total": total, "pay_url": order.get("url")})

    def _service(self, handle: VpsHandle) -> Optional[str]:
        if not handle.id.startswith("order-"):
            return handle.id
        new = sorted(set(self._json("GET", "/vps") or []) - set(handle.extra.get("before") or []))
        if new:
            handle.id = new[0]
            return handle.id
        st = self._json("GET", f"/me/order/{handle.extra['orderId']}/status")
        if st in ("cancelled", "cancelling"):
            raise ProviderError(f"ovh: order {handle.extra['orderId']} was cancelled")
        if st == "notPaid" and handle.extra.get("pay_url"):
            raise ProviderError(f"ovh: order {handle.extra['orderId']} is not paid (no default payment method?). "
                                f"Pay it at {handle.extra['pay_url']} and retry this step.")
        return None

    def _image_id(self, sn: str, os_label: str) -> str:
        want = os_label.lower()
        for iid in self._json("GET", f"/vps/{sn}/images/available") or []:
            if want in (self._json("GET", f"/vps/{sn}/images/available/{iid}").get("name") or "").lower():
                return iid
        raise ProviderError(f"ovh: no {os_label} image offered for {sn}")

    def wait_ip(self, handle: VpsHandle, timeout: int = 600, on_line: Optional[Callable[[str], None]] = None) -> str:
        deadline = time.time() + max(timeout, 1800)   # OVH delivery can take a while; the step's own timeout still bounds it
        sn = self._poll(lambda: self._service(handle), int(deadline - time.time()), 20, on_line, what="order to be delivered")
        self._poll(lambda: "ok" if self._json("GET", f"/vps/{sn}").get("state") == "running" else None,
                   int(deadline - time.time()), 10, on_line, what="to start")
        if not handle.extra.get("rebuilt"):
            if on_line:
                on_line(f"{sn}: reinstalling {handle.extra.get('os')} with the installer's SSH key")
            task = self._json("POST", f"/vps/{sn}/rebuild", json_body={"imageId": self._image_id(sn, handle.extra.get("os", "Ubuntu 24.04")),
                                                                       "publicSshKey": handle.extra["pubkey"], "doNotSendPassword": True})
            handle.extra["rebuilt"] = True
            tid = task.get("id")
            if tid:
                def done():
                    t = self._json("GET", f"/vps/{sn}/tasks/{tid}")
                    if t.get("state") in ("error", "cancelled"):
                        raise ProviderError(f"ovh: rebuild of {sn} ended in state {t['state']}")
                    return "ok" if t.get("state") == "done" else None
                self._poll(done, int(deadline - time.time()), 15, on_line, what="rebuild")
        ips = [ip for ip in self._json("GET", f"/vps/{sn}/ips") or [] if ":" not in ip]
        if not ips:
            raise ProviderError(f"ovh: {sn} has no IPv4 address")
        handle.ip = ips[0]
        self._root_login(handle.ip, handle.extra.get("os", ""), on_line)
        return handle.ip

    def _root_login(self, ip: str, os_label: str, on_line=None) -> None:
        """OVH images put the key on the distro user (ubuntu / debian); the kit logs in as root, so copy it across."""
        from ..keys import DEFAULT_KEY
        from ..remote.ssh import Session, wait_ssh
        key = self.key_path or DEFAULT_KEY
        try:
            Session(ip, "root", key_path=key, connect_timeout=10).connect().run("true", timeout=20, check=True)
            return
        except Exception:  # noqa: BLE001
            pass
        user = "debian" if os_label.lower().startswith("debian") else "ubuntu"
        s = wait_ssh(ip, user, key_path=key, timeout=600, on_line=on_line)
        s.run("install -d -m700 /root/.ssh && cat ~" + user + "/.ssh/authorized_keys >> /root/.ssh/authorized_keys && "
              "sort -u -o /root/.ssh/authorized_keys /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && "
              "printf 'PermitRootLogin prohibit-password\\n' > /etc/ssh/sshd_config.d/00-heardos-root.conf && "
              "(systemctl reload ssh || systemctl reload sshd)", sudo=True, timeout=60, check=True)

    def destroy(self, handle: VpsHandle) -> None:
        sn = self._service(handle)
        if not sn:
            raise ProviderError("ovh: the order has not been delivered yet; cancel it in the OVHcloud control panel")
        self._json("POST", f"/vps/{sn}/terminate")
        self.destroy_note = TERMINATE_NOTE

    def confirm_destroy(self, service: str, token: str) -> None:
        self._json("POST", f"/vps/{service}/confirmTermination", json_body={"token": token, "reason": "OTHER",
                                                                             "commentary": "replaced by heardos-installer"})

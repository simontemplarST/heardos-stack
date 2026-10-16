#!/usr/bin/env python3
"""Re-apply the Nextcloud app-store screenshot fix after an upgrade (until the point release ships nextcloud/server#64771).

Symptom: App Store tiles show the proxy's "Preview not available / Image not recognized" placeholder because
apps/appstore/lib/Controller/ApiController.php base64-wraps screenshot URLs that the store feed already serves
as https://usercontent.apps.nextcloud.com/<b64> links (double wrap -> empty 200 from the proxy).

Run from the laptop:
  ssh root@192.168.10.2 "pct exec 108 -- docker exec -i nextcloud_migrated-nextcloud-1 python3 -" < nextcloud-appstore-screenshots-patch.py
(docker exec needs -i or the script never reaches python.)  Idempotent; exits 0 with "already patched" or
"upstream fixed" when nothing is to do.  Opcache revalidates within 60 s; occ integrity:check-app appstore will
report INVALID_HASH for this one file until the next upgrade overwrites it.
"""
import shutil, sys
p = "/var/www/html/apps/appstore/lib/Controller/ApiController.php"
s = open(p).read()
if "HeardOS local patch" in s:
    print("already patched"); sys.exit(0)
old2 = """	private function createProxyPreviewUrl(string $url): string {
		if ($url === '') {
			return '';
		}

		return 'https://usercontent.apps.nextcloud.com/' . base64_encode($url);
	}"""
new2 = """	private function createProxyPreviewUrl(string $url): string {
		// HeardOS local patch 2026-10-12 = upstream PR nextcloud/server#64771: the app store feed already serves proxied
		// screenshot URLs; wrapping them twice made the proxy answer "Preview not available".
		if ($url === '' || str_starts_with($url, 'https://usercontent.apps.nextcloud.com/')) {
			return $url;
		}

		// The proxy stores its files under the URL-safe alphabet
		return 'https://usercontent.apps.nextcloud.com/' . strtr(base64_encode($url), '+/', '-_');
	}"""
old3 = """				'screenshot' => isset($app['screenshots'][0]['url']) ? 'https://usercontent.apps.nextcloud.com/' . base64_encode($app['screenshots'][0]['url']) : '',"""
new3 = """				'screenshot' => $this->createProxyPreviewUrl($app['screenshots'][0]['url'] ?? ''),"""
if old2 not in s and "str_starts_with($url, self::USERCONTENT_PROXY_URL)" in s:
    print("upstream fixed, nothing to do"); sys.exit(0)
for o, n in ((old2, new2), (old3, new3)):
    if s.count(o) != 1:
        sys.exit(f"anchor not found exactly once, file changed upstream - inspect by hand: {o[:60]!r}")
    s = s.replace(o, n)
shutil.copy(p, "/tmp/ApiController.php.before-heardos-patch")
open(p, "w").write(s)
print("patched; backup /tmp/ApiController.php.before-heardos-patch (inside the container)")

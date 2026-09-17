"""릴리스 파일을 소개 페이지(MobiFolio_promo) 에 반영한다 — release.cmd 가 부른다.

  python publish_promo.py --version 0.1.2 --base https://fo.mobimml.com --promo ..\\MobiFolio_promo

하는 일:
  1. site/MobiFolioLite-<ver>.exe 복사 (+ promo 루트에도 사본)
  2. site/latest.json, promo/latest.json  — 경량판 자동 업데이트가 읽는 파일 {version, url, sha256, notes}
  3. site/SHA256SUMS.txt                   — release/ 에 있는 exe·zip 전부
  4. site/_redirects 의 /download 줄, site/main.js 의 SITE.downloadUrl/version/fileSize 갱신
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--base", required=True, help="공개 주소 (예: https://fo.mobimml.com)")
    ap.add_argument("--promo", default=os.path.join(HERE, "..", "MobiFolio_promo"))
    ap.add_argument("--release", default=os.path.join(HERE, "release"))
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    ver, base = a.version, a.base.rstrip("/")
    promo = os.path.abspath(a.promo); site = os.path.join(promo, "site")
    if not os.path.isdir(site):
        print(f"[promo] site 폴더가 없습니다: {site}"); return 1
    lite_name = f"MobiFolioLite-{ver}.exe"
    src = os.path.join(a.release, lite_name)
    if not os.path.isfile(src):
        print(f"[promo] 경량판이 없습니다: {src}"); return 1
    # 1) exe
    for dst_dir in (site, promo):
        shutil.copy2(src, os.path.join(dst_dir, lite_name))
    digest = sha256(src)
    size_mb = f"{os.path.getsize(src) / (1024 * 1024):.1f} MB"
    # 2) latest.json
    manifest = {"version": ver, "url": f"{base}/{lite_name}", "sha256": digest, "notes": a.notes}
    for dst_dir in (site, promo):
        with open(os.path.join(dst_dir, "latest.json"), "w", encoding="ascii") as f:
            json.dump(manifest, f)
    # 3) SHA256SUMS.txt (release 폴더의 exe/zip 전부)
    lines = []
    for name in sorted(os.listdir(a.release)):
        if name.lower().endswith((".exe", ".zip")):
            lines.append(f"{sha256(os.path.join(a.release, name)).upper()}  {name}")
    with open(os.path.join(site, "SHA256SUMS.txt"), "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    # 4) _redirects, main.js
    rp = os.path.join(site, "_redirects")
    if os.path.isfile(rp):
        t = open(rp, encoding="utf-8").read()
        t2 = re.sub(r"^(/download\s+)/MobiFolioLite-[0-9.]+\.exe(\s+\d+)", rf"\g<1>/{lite_name}\g<2>", t, flags=re.M)
        open(rp, "w", encoding="utf-8", newline="\n").write(t2)
    mp = os.path.join(site, "main.js")
    if os.path.isfile(mp):
        t = open(mp, encoding="utf-8").read()
        t = re.sub(r'downloadUrl:\s*"[^"]*"', f'downloadUrl: "./{lite_name}"', t, count=1)
        t = re.sub(r'version:\s*"v?[0-9.]+"', f'version: "v{ver}"', t, count=1)
        t = re.sub(r'fileSize:\s*"[^"]*"', f'fileSize: "{size_mb}"', t, count=1)
        open(mp, "w", encoding="utf-8", newline="\n").write(t)
    print(f"[promo] {lite_name} ({size_mb}) → {site}  latest.json url={manifest['url']}")
    print(f"[promo] 남은 일: {site} 를 배포(Cloudflare Pages)하면 {base}/latest.json 이 새 버전을 가리킨다")
    return 0


if __name__ == "__main__":
    sys.exit(main())

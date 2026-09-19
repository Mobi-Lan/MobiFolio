"""릴리스 파일을 소개 페이지에 반영하고 배포까지 한다 — 배포는 --publish 를 줄 때만.

  python scripts\\publish_promo.py --version 0.2.5 --base https://…             # 무엇이 올라갈지 보여만 줌
  python scripts\\publish_promo.py --version 0.2.5 --base https://… --publish   # 실제 배포

**빌드는 배포가 아니다.** release.cmd 는 --publish 없이 부르므로 개발 중 빌드는 사이트에 나가지 않는다.
사이트의 latest.json 이 바뀌는 순간 설치된 앱들이 그 판으로 자동 업데이트되므로,
어느 판을 내보낼지는 사람이 고른다 (scripts\\publish.cmd).

소개 페이지 폴더(`<promo>\\site`)는 배포 저장소(Mobi-Lan/MobiFolio_WEB)의 작업 복사본이다.
여기에 쓰고 커밋·푸시하면 Cloudflare Pages 가 몇 분 안에 https://fo.mobimml.com 에 올린다.
그래서 이 스크립트가 끝나면 사람이 따로 할 일이 없다.

하는 일:
  1. site/MobiFolioLite-<ver>.exe 복사 — 직전 한 판은 남기고 그보다 옛 판만 지운다
  2. site/latest.json                 — 경량판 자동 업데이트가 읽는 파일 {version, url, sha256, notes}
  3. site/SHA256SUMS.txt              — release/ 에 있는 exe·zip 전부
  4. site/_redirects 의 /download 줄, site/main.js 의 downloadUrl·version·fileSize
  5. 검증 — 아래 「왜 검증하나」
  6. git commit + push (--no-push 로 끌 수 있다)

왜 검증하나 (전부 실제로 겪은 사고):
  - url 이 자리표시(example.invalid)나 http 로 나가면 업데이트 확인이 조용히 죽는다.
  - latest.json 의 sha256 이 올리는 exe 와 다르면 앱이 내려받고도 검증에서 멈춘다.
  - 같은 버전 번호로 내용이 다른 파일을 올리면, 이미 받은 사람은 「같은 버전」이라
    자동 업데이트를 영원히 못 탄다. 내용이 바뀌면 반드시 번호를 올려야 한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KEEP_OLD = 1          # 직전 몇 판을 남길지 — latest.json 을 읽은 직후 배포가 갈린 사용자가 404 를 받지 않게
PLACEHOLDER = ("example.", ".invalid", "localhost", "127.0.0.1", "<", ">")


def sha256(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ver_key(name: str) -> tuple:
    m = re.search(r"MobiFolioLite-([0-9.]+)\.exe$", name)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def git(site: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=site,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit(f"[promo] git {' '.join(args)} 실패:\n{p.stdout}{p.stderr}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--base", required=True, help="공개 주소 (예: https://fo.mobimml.com)")
    ap.add_argument("--promo", default=os.path.join(HERE, "..", "..", "MobiFolio_promo"))
    ap.add_argument("--release", default=os.path.join(HERE, "..", "release"))
    ap.add_argument("--notes", default="")
    ap.add_argument("--publish", action="store_true",
                    help="실제로 배포한다. 이 옵션이 없으면 무엇이 올라갈지 보여만 주고 아무것도 건드리지 않는다.")
    ap.add_argument("--no-push", action="store_true", help="--publish 와 함께: 커밋만 하고 푸시하지 않는다")
    a = ap.parse_args()
    ver, base = a.version, a.base.rstrip("/")
    promo = os.path.abspath(a.promo)
    site = os.path.join(promo, "site")
    rel = os.path.abspath(a.release)

    if not os.path.isdir(site):
        print(f"[promo] site 폴더가 없습니다: {site}"); return 1
    lite_name = f"MobiFolioLite-{ver}.exe"
    src = os.path.join(rel, lite_name)
    if not os.path.isfile(src):
        print(f"[promo] 경량판이 없습니다: {src}"); return 1

    # 주소 검사 — 자리표시나 http 로 나가면 업데이트 확인이 조용히 죽는다
    if not base.startswith("https://") or any(x in base for x in PLACEHOLDER):
        print(f"[promo] 공개 주소가 이상합니다: {base}\n"
              f"        set MF_UPDATE_BASE=https://<실제 주소> 를 주고 다시 실행하세요."); return 1

    is_repo = git(site, "rev-parse", "--is-inside-work-tree", check=False).returncode == 0
    if not is_repo:
        print(f"[promo] {site} 가 git 저장소가 아닙니다 — 배포 저장소 작업 복사본이어야 합니다."); return 1

    digest = sha256(src)

    # 같은 번호로 내용이 다른 파일을 올리려는 경우를 막는다
    dst = os.path.join(site, lite_name)
    if os.path.isfile(dst) and sha256(dst) != digest:
        print(f"[promo] {lite_name} 이 이미 있는데 내용이 다릅니다.\n"
              f"        같은 번호로 다시 구우면 이미 받은 사람은 자동 업데이트를 못 탑니다.\n"
              f"        app\\package.json 의 버전을 올리고 다시 빌드하세요."); return 1

    # 배포는 명시적으로 허락할 때만 한다 — 빌드할 때마다 공개되면 개발 중인 판이 사용자에게 내려간다.
    # (latest.json 이 바뀌는 순간 설치된 앱들이 그 판으로 자동 업데이트된다)
    if not a.publish:
        live = "?"
        cur = os.path.join(site, "latest.json")
        if os.path.isfile(cur):
            try:
                live = json.load(open(cur, encoding="utf-8")).get("version", "?")
            except (OSError, ValueError):
                pass
        print(f"[promo] 빌드 {ver} — 배포하지 않았습니다 (사이트는 {live} 그대로).")
        print(f"[promo] 이 판을 사용자에게 내보내려면:  scripts\\publish.cmd {ver}")
        return 0

    # 1) exe — 새 판을 넣고, 직전 KEEP_OLD 판만 남기고 그보다 옛 판은 지운다
    shutil.copy2(src, dst)
    olds = sorted((f for f in os.listdir(site)
                   if re.fullmatch(r"MobiFolioLite-[0-9.]+\.exe", f) and f != lite_name),
                  key=ver_key, reverse=True)
    for stale in olds[KEEP_OLD:]:
        os.remove(os.path.join(site, stale))
        print(f"[promo] 옛 판 삭제: {stale}")
    kept = olds[:KEEP_OLD]

    size_mb = f"{os.path.getsize(src) / (1024 * 1024):.1f} MB"   # 탐색기가 보여주는 값(MiB)과 맞춘다

    # 2) latest.json
    manifest = {"version": ver, "url": f"{base}/{lite_name}", "sha256": digest, "notes": a.notes}
    with open(os.path.join(site, "latest.json"), "w", encoding="ascii", newline="\n") as f:
        json.dump(manifest, f)

    # 3) SHA256SUMS.txt (release 폴더의 exe/zip 전부)
    lines = [f"{sha256(os.path.join(rel, n)).upper()}  {n}"
             for n in sorted(os.listdir(rel)) if n.lower().endswith((".exe", ".zip"))]
    with open(os.path.join(site, "SHA256SUMS.txt"), "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    # 4) _redirects, main.js
    rp = os.path.join(site, "_redirects")
    if os.path.isfile(rp):
        t = open(rp, encoding="utf-8").read()
        t = re.sub(r"^(/download\s+)/MobiFolioLite-[0-9.]+\.exe", rf"\g<1>/{lite_name}", t, flags=re.M)
        open(rp, "w", encoding="utf-8", newline="\n").write(t)
    mp = os.path.join(site, "main.js")
    if os.path.isfile(mp):
        t = open(mp, encoding="utf-8").read()
        t = re.sub(r'downloadUrl:\s*"[^"]*"', f'downloadUrl: "./{lite_name}"', t, count=1)
        t = re.sub(r'version:\s*"v?[0-9.]+"', f'version: "v{ver}"', t, count=1)
        t = re.sub(r'fileSize:\s*"[^"]*"', f'fileSize: "{size_mb}"', t, count=1)
        open(mp, "w", encoding="utf-8", newline="\n").write(t)

    # 5) 검증 — 쓰고 나서 다시 읽어 대조한다
    back = json.load(open(os.path.join(site, "latest.json"), encoding="utf-8"))
    checks = [
        (back["sha256"] == sha256(dst), "latest.json 의 sha256 이 올리는 exe 와 다르다"),
        (back["url"] == f"{base}/{lite_name}", f"latest.json 의 url 이 이상하다: {back['url']}"),
        (back["version"] == ver, "latest.json 의 version 이 다르다"),
        (digest.upper() in open(os.path.join(site, "SHA256SUMS.txt"), encoding="ascii").read(),
         "SHA256SUMS.txt 에 경량판 해시가 없다"),
        (f"/{lite_name}" in open(rp, encoding="utf-8").read() if os.path.isfile(rp) else True,
         "_redirects 의 /download 가 갱신되지 않았다"),
    ]
    bad = [msg for ok, msg in checks if not ok]
    if bad:
        print("[promo] 검증 실패 — 배포하지 않습니다:")
        for m in bad:
            print(f"        - {m}")
        return 1

    print(f"[promo] {lite_name} ({size_mb})  sha256 {digest[:16]}…")
    print(f"[promo] latest.json url={manifest['url']}")
    print(f"[promo] 함께 두는 직전 판: {', '.join(kept) if kept else '없음'}")

    # 6) 커밋 + 푸시
    git(site, "add", "-A")
    if not git(site, "diff", "--cached", "--quiet", check=False).returncode:
        print("[promo] 바뀐 것이 없습니다 — 커밋하지 않습니다."); return 0
    msg = (f"release: 경량판 {ver}\n\n"
           f"exe·latest.json·SHA256SUMS 의 sha256 을 대조해 일치 확인: {digest[:8]}… "
           f"({os.path.getsize(src):,} B).\n"
           f"직전 판({', '.join(kept) if kept else '없음'})은 남겨 둔다 — latest.json 을 읽은 직후\n"
           f"배포가 갈린 사용자가 404 를 받지 않게.\n")
    git(site, "commit", "-q", "-m", msg)
    print(f"[promo] 커밋: {git(site, 'log', '--oneline', '-1').stdout.strip()}")
    if a.no_push:
        print("[promo] --no-push — 푸시하지 않았습니다. 준비되면 site 폴더에서 git push 하세요."); return 0
    git(site, "push", "-q", "origin", "main")
    print(f"[promo] 푸시 완료 — 몇 분 안에 {base} 에 반영됩니다.")
    print(f"[promo] 확인: curl -s {base}/latest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

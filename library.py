"""악보 라이브러리: 제목 정규화 → 아티스트 분류 → 색인·검색.

사용자가 직접 만드는 악보라 제목이 제각각이다. 파이프라인:
  1) clean_title: [ ] ( ) 【 】 「 」 장식, 잡음어(악보·ver·cover·완성본…), 기호·이모지 제거
  2) split: ' - ', '–', '/', '|', '_', 'by', ':' 등으로 아티스트/곡 분리 (숫자만인 쪽은 곡)
  3) classify: 수동 지정 > 별칭·아티스트 사전(이름이 제목 안에 통째로 있으면) > 구분자 분리 > 기타
     구분자로 뽑힌 아티스트는 사전에 합류해, 구분자 없는 '아이유 밤편지' 같은 제목도 2차로 잡는다.
재생은 원본 DisplayTitle 로 보내야 하므로 `title` 은 원본 그대로 둔다.
"""
from __future__ import annotations

import re
import unicodedata

TITLE_KEYS = ("DisplayTitle", "Title", "DisplayName", "Name")
NAME_KEYS = ("Name", "DisplayName", "Title")
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_CHO_MERGE = {"ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ"}
INDEX_ORDER = list("ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ") + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["#"]
_SEP = re.compile(r"[\s\-_·•.,()\[\]{}~!?/\\|:;\"'`+*&%$#@^=<>【】「」『』〈〉《》]+")
# 아티스트/곡 구분자 (우선순위 순). 공백 하이픈 계열 먼저, 그다음 붙은 것들.
_SPLITTERS = [" - ", " – ", " — ", " / ", " | ", " : ", "：", " by ", " BY ", "_", " -", "- ", "-", "–", "—", "/", "|"]
# 기본 잡음어: 제목에서 떼어내도 곡 식별에 영향 없는 것들. data/artists.json 의 noise 로 덧붙일 수 있다.
DEFAULT_NOISE = [
    "악보", "ver", "ver.", "version", "버전", "cover", "커버", "remix", "리믹스", "편곡", "피아노", "piano", "ost",
    "full", "풀", "완성", "완성본", "미완", "미완성", "test", "테스트", "연습", "수정", "수정본", "final", "최종",
    "mr", "inst", "inst.", "원곡", "orig", "original", "short", "숏", "long", "롱", "easy", "쉬움", "hard",
    "v1", "v2", "v3", "v4", "v5", "1절", "2절", "무료", "공유", "배포", "자작", "자동", "auto", "복사본", "copy",
]
_NUMERIC = re.compile(r"^[\d\s.]+$")
_WORD_SPLIT = re.compile(r"[\s\-_·•.,()\[\]{}~!?/\\|:;\"'`+*&%$#@^=<>【】「」『』〈〉《》]+")
# 실측(193곡)에서 본 패턴: 전부 '악보: ' 접두, 개인 분류 태그(A_ C_ J_ K_ P_ #_ !_ @ 1. 5.), 파트 번호(-1 -2 -test)
_PREFIXES = [r"^악보\s*[:：]\s*", r"^[!@#$%*]+\s*", r"^[A-Za-z]_(?=\S)", r"^[!@#]_", r"^\d{1,2}\.\s*(?=\D)"]
# 카테고리어: 아티스트가 아니라 분류 태그. 제목에서 떼어 tags 로 보관한다.
CATEGORY_WORDS = ["동요", "영화", "뮤지컬", "애니", "애니메이션", "게임", "클래식", "jpop", "kpop", "j-pop", "k-pop", "ost",
                  "재즈버전", "재즈", "jazz", "피아노", "piano", "임시", "연습", "테스트", "test"]
_VARIANT_TAIL = re.compile(r"[\s\-_]*(?:-|_|\s)(\d{1,2}|\d+합|test|TEST|테스트|재즈|jazz|rock\.?ver|Rock\.ver)\s*$")
_ATTACHED_TAG = re.compile(r"(?<=[가-힣A-Za-z])(OST|ost)\s*$")   # '라퓨타OST' 처럼 붙어 있는 꼬리


def pick(obj: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if isinstance(v, str) and v.strip():
            return v
    return ""


def norm(s: str) -> str:
    """검색·비교용: NFKC, 소문자, 구분자·기호 제거."""
    s = unicodedata.normalize("NFKC", s or "").lower()
    return _SEP.sub("", s)


def tokens(s: str) -> list[str]:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return [t for t in _WORD_SPLIT.split(s) if t]


def dup_key(s: str) -> str:
    """'백예린 - 0310' 과 '0310-백예린' 을 같게 보는 키."""
    return "|".join(sorted(tokens(s)))


def initial(s: str) -> str:
    for ch in (s or "").strip():
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:
            c = _CHO[(o - 0xAC00) // 588]
            return _CHO_MERGE.get(c, c)
        if ch.isascii() and ch.isalpha():
            return ch.upper()
        if ch.isspace():
            continue
        return "#"
    return "#"


# ── 1) 정리 ──
def _strip_symbols(s: str) -> str:
    out = []
    for ch in s:
        cat = unicodedata.category(ch)
        if cat.startswith(("So", "Sk", "Cf")) or ch in "★☆♪♫♬♥❤✨":   # 이모지·장식 기호
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def clean_title(title: str, noise: list[str] | None = None) -> tuple[str, list[str], list[str], str]:
    """장식·잡음어를 떼고 (정리된 제목, 떼어낸 조각, 카테고리 태그, 변형 꼬리) 를 돌려준다. 전부 떼어져 비면 원본."""
    removed: list[str] = []
    tags: list[str] = []
    variant = ""
    t = unicodedata.normalize("NFKC", title or "").strip()
    t = _strip_symbols(t)
    # 접두 태그 ('악보: ', 'A_', '!_', '1.', '@' …)
    for pat in _PREFIXES:
        t2, n = re.subn(pat, "", t)
        if n and t2.strip():
            removed.append(t[: len(t) - len(t2)]); t = t2.strip()
    # 변형 꼬리 ('-1' '-2' '-test' '-재즈' '-6합' …) → variant. 같은 곡의 파트/버전이 한 곡으로 묶이게
    m = _VARIANT_TAIL.search(t)
    if m and len(t) - len(m.group(0)) >= 2:
        variant = m.group(1); t = t[: m.start()].strip()
    # 붙은 꼬리 태그 ('천공의성 라퓨타OST', '인터스텔라OST')
    m = _ATTACHED_TAG.search(t)
    if m and len(t) - len(m.group(0)) >= 2:
        tags.append("OST"); t = t[: m.start()].strip(" -_·•.,:/")
    # 카테고리어 → tags (단독 토큰일 때만; '동요_루돌프' 처럼 붙은 것도 포함)
    for w in CATEGORY_WORDS:
        pat = re.compile(r"(?i)(?:^|(?<=[\s_\-:/(\[]))" + re.escape(w) + r"(?=$|[\s_\-:/)\]])")
        if pat.search(t):
            t2 = pat.sub(" ", t).strip(" -_·•.,:/")
            if t2:
                tags.append(w.upper() if w.isascii() else w); t = t2
    # 괄호류 안의 내용은 대개 장식(버전·비고). 제목 전체가 괄호면 유지.
    def _br(m):
        inner = m.group(0)
        removed.append(inner)
        return " "
    for pat in (r"\[[^\]]*\]", r"\([^)]*\)", r"【[^】]*】", r"「[^」]*」", r"『[^』]*』", r"〈[^〉]*〉", r"《[^》]*》", r"\{[^}]*\}"):
        t2 = re.sub(pat, _br, t)
        if t2.strip():
            t = t2
        else:
            removed.pop() if removed else None
    words = set(w.lower() for w in (noise if noise is not None else DEFAULT_NOISE))
    kept = []
    for w in re.split(r"(\s+)", t):
        if w.strip() == "":
            kept.append(w); continue
        core = w.strip(" .-_")
        if core.lower() in words or re.fullmatch(r"(?i)v\d+|ver\.?\d*|\d+절", core):
            removed.append(w)
        else:
            kept.append(w)
    t = re.sub(r"\s{2,}", " ", "".join(kept)).strip(" -_·•.,:;/|")
    if not t:
        return (title or "").strip(), removed, tags, variant
    return t, removed, tags, variant


# ── 2) 분리 ──
def raw_left(cleaned: str) -> str:
    """구분자 왼쪽 조각 (빈도 세기용). 없으면 ''."""
    t = cleaned.strip()
    for sep in _SPLITTERS:
        if sep in t:
            a, b = t.split(sep, 1)
            if a.strip(" -_·•.,") and b.strip(" -_·•.,"):
                return a.strip(" -_·•.,")
    return ""


def split_artist(cleaned: str, numeric_artists: frozenset[str] = frozenset()) -> tuple[str, str, str]:
    """(아티스트, 곡, 규칙). 못 나누면 ('', cleaned, '').
    numeric_artists: '0018' 처럼 숫자로만 된 이름이 왼쪽에 2곡 이상 반복되면 밴드 이름으로 인정한다."""
    t = cleaned.strip()
    for sep in _SPLITTERS:
        if sep in t:
            a, b = t.split(sep, 1)
            a, b = a.strip(" -_·•.,"), b.strip(" -_·•.,")
            if not a or not b:
                continue
            if _NUMERIC.match(a) and norm(a) in numeric_artists:
                return a, b, "split"
            # 숫자만인 쪽은 곡 제목(0310 같은 것) → 다른 쪽이 아티스트
            if _NUMERIC.match(a) and not _NUMERIC.match(b):
                return b, a, "split"
            if _NUMERIC.match(b) and not _NUMERIC.match(a):
                return a, b, "split"
            if _NUMERIC.match(a) and _NUMERIC.match(b):
                return "", t, ""
            # 너무 긴 쪽은 아티스트가 아니다 (문장형 제목)
            if len(a) > 24 and len(b) <= 24:
                return b, a, "split"
            # '내나이50에다시배우는악기' 같은 개인 태그: 글자+숫자 섞인 긴 덩어리는 아티스트로 안 본다
            if len(a) >= 8 and re.search(r"\d", a) and re.search(r"[가-힣A-Za-z]", a) and not _NUMERIC.match(a):
                return "", b, ""
            return a, b, "split"
    return "", t, ""


# ── 3) 분류 ──
class ArtistIndex:
    """수동 아티스트·별칭·지정 + 자동 추출 아티스트를 합친 사전."""

    def __init__(self, state: dict):
        self.artists: dict[str, dict] = {a["id"]: a for a in state.get("artists", [])}
        self.assign: dict[str, str] = dict(state.get("assign", {}))          # 원본 제목 → artistId
        self.noise: list[str] = list(DEFAULT_NOISE) + list(state.get("noise", []))
        self.by_norm: dict[str, str] = {}                                     # norm(이름/별칭) → artistId
        for a in self.artists.values():
            for nm in [a["name"], *a.get("aliases", [])]:
                if nm and nm.strip():
                    self.by_norm[norm(nm)] = a["id"]
        self.auto: dict[str, str] = {}                                        # norm(자동 추출 이름) → 표시 이름

    def lookup(self, name: str) -> str | None:
        return self.by_norm.get(norm(name))

    def display(self, artist_id: str) -> str:
        return self.artists.get(artist_id, {}).get("name", artist_id)

    def find_in_title(self, cleaned: str) -> tuple[str | None, str]:
        """제목 토큰 조합 안에 사전의 이름이 통째로 들어 있으면 그 아티스트. (긴 이름 우선)"""
        n = norm(cleaned)
        if not n:
            return None, ""
        best: tuple[int, str, str] | None = None
        for key, aid in self.by_norm.items():
            if key and key in n and (best is None or len(key) > best[0]):
                best = (len(key), aid, key)
        if best:
            return best[1], "dict"
        for key, disp in self.auto.items():
            if key and key in n and len(key) >= 2 and (best is None or len(key) > best[0]):
                best = (len(key), disp, key)
        if best:
            return best[1], "auto"
        return None, ""


def build(scores: list, artist_state: dict | None = None) -> list[dict]:
    """원본 배열 → UI 아이템. bucket = 'artist' | 'other'."""
    idx = ArtistIndex(artist_state or {})
    prelim: list[dict] = []
    exact: dict[str, int] = {}
    similar: dict[str, int] = {}
    # 사전 통과: 구분자 왼쪽에 숫자 이름이 2곡 이상 반복되면 밴드(0018)로 인정
    left_counts: dict[str, int] = {}
    for raw in scores:
        t0 = pick(raw, TITLE_KEYS) if isinstance(raw, dict) else str(raw)
        a = raw_left(clean_title(t0, idx.noise)[0])
        if a and _NUMERIC.match(a):
            left_counts[norm(a)] = left_counts.get(norm(a), 0) + 1
    numeric_artists = frozenset(k for k, c in left_counts.items() if c >= 2)
    for i, raw in enumerate(scores):
        if not isinstance(raw, dict):
            raw = {"DisplayTitle": str(raw)}
        title = pick(raw, TITLE_KEYS)
        exact[title] = exact.get(title, 0) + 1
        similar[dup_key(title)] = similar.get(dup_key(title), 0) + 1
        cleaned, removed, tags, variant = clean_title(title, idx.noise)
        artist, song, rule = split_artist(cleaned, numeric_artists)
        prelim.append({"i": i, "raw": raw, "title": title, "cleaned": cleaned, "removed": removed, "tags": tags, "variant": variant,
                       "artist_guess": artist, "song": song, "rule": rule})
    # 구분자로 뽑힌 아티스트를 자동 사전에 합류 (2회 이상이면 확신, 1회도 후보로)
    counts: dict[str, int] = {}
    disp: dict[str, str] = {}
    for p in prelim:
        if p["artist_guess"] and not idx.lookup(p["artist_guess"]):
            k = norm(p["artist_guess"])
            counts[k] = counts.get(k, 0) + 1
            disp.setdefault(k, p["artist_guess"])
    for k, c in counts.items():
        if len(k) >= 2:
            idx.auto[k] = disp[k]
    items: list[dict] = []
    for p in prelim:
        title = p["title"]
        artist_id: str | None = None
        artist_name = ""
        rule = ""
        if title in idx.assign and idx.assign[title] in idx.artists:
            artist_id = idx.assign[title]; artist_name = idx.display(artist_id); rule = "manual"
        elif p["artist_guess"] and idx.lookup(p["artist_guess"]):
            artist_id = idx.lookup(p["artist_guess"]); artist_name = idx.display(artist_id); rule = "dict"
        elif p["artist_guess"]:
            artist_name = idx.auto.get(norm(p["artist_guess"]), p["artist_guess"]); rule = "split"
        else:
            hit, r = idx.find_in_title(p["cleaned"])
            if hit and r == "dict":
                artist_id = hit; artist_name = idx.display(hit); rule = "dict"
            elif hit and r == "auto":
                artist_name = hit; rule = "auto"
        song = p["song"] if rule else p["cleaned"]
        if rule in ("dict", "auto", "manual") and artist_name and not p["artist_guess"]:
            # 구분자 없이 이름이 들어 있던 경우: 곡 제목에서 아티스트 이름을 뺀다
            song = re.sub(re.escape(artist_name), "", p["cleaned"], flags=re.I).strip(" -_·•.,:") or p["cleaned"]
        akey = artist_id or (("auto:" + norm(artist_name)) if artist_name else "")
        items.append({
            "i": p["i"], "title": title, "cleaned": p["cleaned"], "removed": p["removed"], "tags": p["tags"], "variant": p["variant"],
            "artist": artist_name, "artistKey": akey, "manual": rule == "manual",
            "song": song, "rule": rule or "none", "bucket": "artist" if artist_name else "other",
            "location": p["raw"].get("Location", ""), "locked": bool(p["raw"].get("IsLocked", False)),
            "initial": initial(song or title), "norm": norm(title) + " " + norm(p["cleaned"]),
            "dupExact": exact.get(title, 1), "dupSimilar": similar.get(dup_key(title), 1),
            "raw": p["raw"],
        })
    return items


def build_instruments(instruments: list) -> list[dict]:
    out = []
    for i, raw in enumerate(instruments):
        if not isinstance(raw, dict):
            raw = {"Name": str(raw)}
        name = pick(raw, NAME_KEYS)
        out.append({"i": i, "name": name, "norm": norm(name), "equipped": bool(raw.get("IsEquipped", False)),
                    "durability": raw.get("Durability"), "raw": raw})
    return out


def search(items: list[dict], q: str) -> list[dict]:
    qt = [norm(t) for t in tokens(q)]
    qt = [t for t in qt if t]
    if not qt:
        return items
    return [it for it in items if all(t in it["norm"] or t in norm(it.get("artist", "")) for t in qt)]


def artists_summary(items: list[dict]) -> list[dict]:
    """아티스트 색인: key, 이름, 곡 수, 수동/자동. 곡 수 내림차순."""
    agg: dict[str, dict] = {}
    for it in items:
        if it["bucket"] != "artist":
            continue
        k = it["artistKey"]
        a = agg.setdefault(k, {"key": k, "name": it["artist"], "count": 0, "manual": not k.startswith("auto:"), "initial": initial(it["artist"])})
        a["count"] += 1
    return sorted(agg.values(), key=lambda a: (-a["count"], a["name"]))


def summary(items: list[dict]) -> dict:
    by_initial: dict[str, int] = {}
    rules: dict[str, int] = {}
    for it in items:
        by_initial[it["initial"]] = by_initial.get(it["initial"], 0) + 1
        rules[it["rule"]] = rules.get(it["rule"], 0) + 1
    dups = sorted({it["title"] for it in items if it["dupExact"] > 1})
    other = sum(1 for it in items if it["bucket"] == "other")
    return {"count": len(items), "byInitial": by_initial, "rules": rules, "other": other,
            "artists": artists_summary(items), "duplicateTitles": dups}

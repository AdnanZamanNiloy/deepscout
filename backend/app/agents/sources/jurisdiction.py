from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

from app.agents.sources.urls import (
    extract_domain,
)

COUNTRY_OFFICIAL_SUFFIX: Dict[str, Tuple[str, ...]] = {
    "ae": ("gov.ae",), "af": ("gov.af", "af"), "al": ("gov.al",),
    "am": ("gov.am",), "ao": ("gov.ao",), "ar": ("gob.ar", "gov.ar"),
    "at": ("gv.at", "at"), "au": ("gov.au",), "az": ("gov.az",),
    "ba": ("gov.ba",), "bd": ("gov.bd", "org.bd"), "be": ("gouv.be",),
    "bf": ("gov.bf",), "bg": ("government.bg", "bg"), "bh": ("gov.bh", "com.bh"),
    "bi": ("gov.bi",), "bj": ("gouv.bj",), "bo": ("gob.bo", "gov.bo"),
    "br": ("gov.br", "org.br"), "bw": ("gov.bw",), "by": ("gov.by", "by"),
    "ca": ("gc.ca", "gov.ca"), "cd": ("gouv.cd",), "cf": ("gov.cf",),
    "cg": ("gov.cg",), "ch": ("admin.ch", "ch"), "ci": ("gouv.ci",),
    "cl": ("gob.cl", "gov.cl"), "cm": ("gov.cm",), "cn": ("gov.cn",),
    "co": ("gov.co",), "cr": ("gob.cr", "go.cr"), "cu": ("gov.cu",),
    "cv": ("gov.cv",), "cy": ("gov.cy",), "cz": ("gov.cz",),
    "de": ("de",), "dj": ("gouv.dj",), "dk": ("gov.dk",),
    "do": ("gob.do", "gov.do"), "dz": ("gov.dz",), "ec": ("gob.ec", "gov.ec"),
    "ee": ("riik.ee", "ee"), "eg": ("gov.eg",), "er": ("gov.er",),
    "es": ("gob.es", "gov.es"), "et": ("gov.et",), "eu": ("europa.eu",),
    "fi": ("gov.fi",), "fj": ("gov.fj",), "fr": ("gouv.fr",),
    "ga": ("gouv.ga",), "gb": ("gov.uk", "ac.uk"), "ge": ("gov.ge",),
    "gh": ("gov.gh",), "gm": ("gov.gm",), "gn": ("gov.gn",),
    "gq": ("gob.gq",), "gr": ("gov.gr", "ge"), "gt": ("gob.gt", "gov.gt"),
    "gw": ("gov.gw",), "hk": ("gov.hk",), "hn": ("gob.hn",),
    "hr": ("gov.hr",), "ht": ("gouv.ht",), "hu": ("gov.hu",),
    "id": ("go.id",), "ie": ("gov.ie",), "il": ("gov.il",),
    "in": ("gov.in", "nic.in"), "iq": ("gov.iq",), "ir": ("gov.ir",),
    "is": ("is",), "it": ("gov.it",), "jm": ("gov.jm",),
    "jo": ("gov.jo", "jo"), "jp": ("go.jp",), "ke": ("go.ke",),
    "kg": ("gov.kg",), "kh": ("gov.kh",), "km": ("gouv.km",),
    "lk": ("gov.lk",),
    "kp": ("gov.kp",), "kr": ("go.kr",), "kw": ("gov.kw",),
    "kz": ("gov.kz",), "la": ("gov.la",), "lb": ("gov.lb",),
    "lr": ("gov.lr",), "ls": ("gov.ls",), "lt": ("gov.lt",),
    "lu": ("gouv.lu",), "lv": ("gov.lv",), "ly": ("gov.ly",),
    "ma": ("gov.ma",), "md": ("gov.md",), "me": ("gov.me",),
    "mg": ("gov.mg",), "mk": ("gov.mk",), "ml": ("gouv.ml",),
    "mm": ("gov.mm",), "mn": ("gov.mn",), "mo": ("gov.mo",),
    "mt": ("gov.mt",), "mu": ("gov.mu",), "mv": ("gov.mv",),
    "mw": ("gov.mw",), "mx": ("gob.mx", "gob.mx"),
    "my": ("gov.my",), "mz": ("gov.mz",), "na": ("gov.na",),
    "ne": ("gouv.ne",), "ng": ("gov.ng",), "ni": ("gob.ni",),
    "nl": ("gov.nl", "rijksoverheid.nl"), "no": ("regjeringen.no", "no"),
    "np": ("gov.np", "com.np"), "nz": ("govt.nz", "govt.nz"),
    "om": ("gov.om",), "pa": ("gob.pa",), "pe": ("gob.pe", "gov.pe"),
    "pg": ("gov.pg",), "ph": ("gov.ph",), "pk": ("gov.pk",),
    "pl": ("gov.pl",), "ps": ("gov.ps",), "pt": ("gov.pt",),
    "py": ("gov.py",), "qa": ("gov.qa",), "ro": ("gov.ro",),
    "rs": ("gov.rs",), "ru": ("gov.ru",), "rw": ("gov.rw",),
    "sa": ("gov.sa",), "sd": ("gov.sd",), "se": ("gov.se",),
    "sg": ("gov.sg",), "si": ("gov.si",), "sk": ("gov.sk",),
    "sl": ("gov.sl",), "sm": ("gov.sm",), "sn": ("gouv.sn",),
    "so": ("gov.so",), "sr": ("gov.sr",), "ss": ("gov.ss",),
    "sv": ("gob.sv", "gov.sv"), "sy": ("gov.sy",), "sz": ("gov.sz",),
    "td": ("gouv.td",), "tg": ("gouv.tg",), "th": ("go.th",), "tj": ("gov.tj",),
    "tl": ("gov.tl",), "tm": ("gov.tm",), "tn": ("gov.tn",),
    "to": ("gov.to",), "tr": ("gov.tr",), "tt": ("gov.tt",),
    "tw": ("gov.tw",), "tz": ("go.tz",), "ua": ("gov.ua", "kiev.ua"),
    "ug": ("gov.ug",), "us": ("gov",), "uy": ("gub.uy", "gov.uy"),
    "uz": ("gov.uz",), "ve": ("gov.ve",), "vn": ("gov.vn",),
    "ye": ("gov.ye",), "za": ("gov.za",), "zm": ("gov.zm",),
    "zw": ("gov.zw",),
}


_COUNTRY_ALIASES: Dict[str, str] = {}


_COUNTRY_BY_ISO2_ALIASES: Dict[str, Tuple[str, ...]] = {}


_KNOWN_COUNTRY_CODES: Set[str] = set()


_CCTLD_TO_ISO2: Dict[str, str] = {"uk": "gb"}


def _register_country(iso2: str, *aliases: str) -> None:
    _KNOWN_COUNTRY_CODES.add(iso2)
    for alias in aliases:
        key = alias.lower()
        if len(key) < 3:
            # Too short to match safely in prose; usable for hosts only.
            continue
        if key in _COUNTRY_ALIASES:
            continue
        _COUNTRY_ALIASES[key] = iso2
        _COUNTRY_BY_ISO2_ALIASES[iso2] = _COUNTRY_BY_ISO2_ALIASES.get(iso2, ()) + (key,)


for _iso, *_aliases in (
    ("af", "afghanistan"), ("al", "albania", "albanian"),
    ("dz", "algeria", "algerian"),
    ("ec", "ecuador"),
    ("ar", "argentina", "argentine"), ("am", "armenia", "armenian"),
    ("ao", "angola"), ("at", "austria", "austrian"),
    ("au", "australia", "australian"), ("az", "azerbaijan", "azerbaijani"),
    ("bh", "bahrain"), ("bd", "bangladesh", "bangladeshi"),
    ("by", "belarus", "belarusian"),
    ("be", "belgium", "belgian"),
    ("bj", "benin"), ("bo", "bolivia", "bolivian"),
    ("ba", "bosnia"), ("bw", "botswana"), ("br", "brazil", "brazilian"),
    ("bg", "bulgaria", "bulgarian"), ("bf", "burkina faso"),
    ("bi", "burundi"), ("kh", "cambodia"), ("cm", "cameroon"),
    ("ca", "canada", "canadian"), ("cv", "cape verde"),
    ("cf", "central african republic"),
    ("td", "chad"), ("cl", "chile", "chilean"), ("cn", "china", "chinese"),
    ("co", "colombia", "colombian"), ("km", "comoros"),
    ("cg", "congo"), ("cd", "democratic republic of the congo", "drc"),
    ("cr", "costa rica"), ("ci", "cote divoire"),
    ("hr", "croatia", "croatian"), ("cu", "cuba"), ("cy", "cyprus"),
    ("cz", "czechia", "czech"), ("dk", "denmark", "danish"),
    ("dj", "djibouti"), ("do", "dominican republic"), ("eg", "egypt", "egyptian"),
    ("sv", "el salvador"), ("gq", "equatorial guinea"), ("er", "eritrea"),
    ("ee", "estonia", "estonian"), ("sz", "eswatini"), ("et", "ethiopia", "ethiopian"),
    ("fj", "fiji"), ("fi", "finland", "finnish"), ("fr", "france", "french"),
    ("ga", "gabon"), ("gm", "gambia"), ("ge", "georgia", "georgian"),
    ("de", "germany", "german"), ("gh", "ghana", "ghanese"), ("gr", "greece", "greek"),
    ("gt", "guatemala"), ("gn", "guinea"), ("gw", "guinea-bissau"),
    ("ht", "haiti"), ("hn", "honduras"), ("hk", "hong kong"),
    ("hu", "hungary", "hungarian"), ("is", "iceland", "icelandic"),
    ("in", "india", "indian"), ("id", "indonesia", "indonesian"),
    ("ir", "iran", "iranian"), ("iq", "iraq", "iraqi"), ("ie", "ireland", "irish"),
    ("il", "israel", "israeli"), ("it", "italy", "italian"),
    ("jm", "jamaica"), ("jp", "japan", "japanese"), ("jo", "jordan"),
    ("kz", "kazakhstan", "kazakh"), ("ke", "kenya", "kenyan"),
    ("kw", "kuwait"), ("kg", "kyrgyzstan"),
    ("la", "laos"), ("lv", "latvia", "latvian"), ("lb", "lebanon"),
    ("ls", "lesotho"), ("lr", "liberia"), ("ly", "libya"), ("lt", "lithuania", "lithuanian"),
    ("lu", "luxembourg"), ("mo", "macau"), ("mg", "madagascar"),
    ("mw", "malawi", "malawian"), ("my", "malaysia", "malaysian"),
    ("mv", "maldives"), ("ml", "mali"), ("mt", "malta"), ("mu", "mauritius"),
    ("mx", "mexico", "mexican"), ("md", "moldova"), ("mn", "mongolia", "mongolian"),
    ("me", "montenegro"), ("ma", "morocco", "moroccan"), ("mz", "mozambique"),
    ("mm", "myanmar", "burma"), ("na", "namibia"), ("np", "nepal", "nepalese"),
    ("nl", "netherlands", "dutch"), ("nz", "new zealand"),
    ("ni", "nicaragua"), ("ne", "niger"), ("ng", "nigeria", "nigerian"),
    ("kp", "north korea"), ("kr", "south korea", "korea", "korean"),
    ("mk", "north macedonia", "macedonia"),
    ("no", "norway", "norwegian"), ("om", "oman"), ("pk", "pakistan", "pakistani"),
    ("ps", "palestine", "palestinian"), ("pa", "panama"), ("pg", "papua new guinea"),
    ("py", "paraguay"), ("pe", "peru", "peruvian"), ("ph", "philippines", "filipino"),
    ("pl", "poland", "polish"), ("pt", "portugal", "portuguese"),
    ("qa", "qatar"), ("ro", "romania", "romanian"), ("ru", "russia", "russian"),
    ("rw", "rwanda", "rwandan"), ("sa", "saudi arabia", "saudi"),
    ("sn", "senegal"), ("rs", "serbia", "serbian"), ("sl", "sierra leone"), ("sm", "san marino"),
    ("sg", "singapore", "singaporean"), ("sk", "slovakia", "slovak"),
    ("si", "slovenia", "slovenian"), ("so", "somalia", "somali"),
    ("za", "south africa", "south african"), ("ss", "south sudan"),
    ("es", "spain", "spanish"), ("lk", "sri lanka"), ("sd", "sudan"),
    ("sr", "suriname"), ("se", "sweden", "swedish"), ("ch", "switzerland", "swiss"),
    ("sy", "syria"), ("tw", "taiwan"), ("tj", "tajikistan"),
    ("tz", "tanzania", "tanzanian"), ("th", "thailand", "thai"),
    ("tl", "timor-leste", "east timor"), ("tg", "togo"), ("to", "tonga"),
    ("tt", "trinidad and tobago"), ("tn", "tunisia", "tunisian"),
    ("tr", "turkey", "turkish", "turkiye"), ("tm", "turkmenistan"),
    ("ug", "uganda", "ugandan"), ("ua", "ukraine", "ukrainian"),
    ("ae", "united arab emirates", "uae", "dubai"),
    ("gb", "united kingdom", "britain", "british", "england", "english", "scotland", "wales"),
    ("us", "united states", "u.s.", "usa", "america", "american"),
    ("uy", "uruguay"), ("uz", "uzbekistan"), ("ve", "venezuela"),
    ("vn", "vietnam", "vietnamese"), ("ye", "yemen"), ("zm", "zambia", "zambian"),
    ("zw", "zimbabwe", "zimbabwean"),
    ("eu", "european union"),
):
    _register_country(_iso, *_aliases)


SUPRANATIONAL_JURISDICTIONS: frozenset = frozenset({"eu"})


_PSEUDO_TLDS: frozenset = frozenset({
    "com", "net", "org", "info", "biz", "io", "co", "ai", "tv", "cc", "ws",
    "fm", "am", "sh", "st", "gg", "im", "name", "dev", "app", "online",
    "site", "tech", "store", "blog", "xyz", "top", "live", "world", "today",
    "cloud", "digital", "design", "email", "link", "media", "news", "group",
    "center", "zone", "network", "systems", "social", "expert", "solutions",
})


_JURISDICTION_SUFFIX_RULES: Tuple[Tuple[str, str], ...] = (
    (".europa.eu", "eu"), (".gov.uk", "gb"), (".gov.au", "au"),
    (".govt.nz", "nz"), (".ac.uk", "gb"),
    (".gov.in", "in"), (".nic.in", "in"), (".co.in", "in"),
    (".gov.bd", "bd"), (".gov.sg", "sg"), (".gouv.fr", "fr"),
    (".govt.gr", "gr"), (".gc.ca", "ca"), (".gov.ca", "ca"),
    (".go.jp", "jp"), (".go.kr", "kr"), (".or.kr", "kr"), (".ne.jp", "jp"),
    (".go.id", "id"), (".go.th", "th"), (".go.ke", "ke"),
    (".gob.ar", "ar"), (".gob.br", "br"), (".gob.cl", "cl"), (".gob.mx", "mx"),
    (".gob.pe", "pe"), (".gov.gr", "gr"), (".bund.de", "de"),
    (".admin.ch", "ch"), (".gouv.be", "be"), (".gouv.lu", "lu"),
    (".gov.pl", "pl"), (".gov.pt", "pt"), (".gov.se", "se"),
    (".gov.za", "za"), (".gov.ng", "ng"), (".gov.gh", "gh"),
    (".gov.es", "es"), (".gob.es", "es"), (".gov.it", "it"), (".gov.il", "il"),
    (".gov.my", "my"), (".gov.ph", "ph"), (".gov.pk", "pk"), (".gov.vn", "vn"),
    (".gov.ir", "ir"), (".gov.sa", "sa"), (".gov.eg", "eg"), (".gov.tr", "tr"),
    (".gov.tw", "tw"), (".gov.hk", "hk"), (".gov.ie", "ie"), (".gov.nl", "nl"),
    (".gov.dk", "dk"), (".gov.no", "no"), (".gov.hu", "hu"), (".gov.cz", "cz"),
    (".gov.ro", "ro"), (".gov.gr", "gr"), (".gov.lv", "lv"), (".gov.lt", "lt"),
    (".gov.hr", "hr"), (".gov.rs", "rs"), (".gov.fi", "fi"), (".gov.ee", "ee"),
    (".gov.at", "at"), (".gov.si", "si"), (".gov.sk", "sk"), (".gov.lk", "lk"),
    (".gov.kz", "kz"), (".gov.uz", "uz"), (".gov.ge", "ge"), (".gov.am", "am"),
    (".gov.by", "by"), (".gov.md", "md"), (".gov.ma", "ma"), (".gov.dz", "dz"),
    (".gov.co", "co"), (".gov.pe", "pe"), (".gov.uy", "uy"), (".gov.py", "py"),
    (".gov.bo", "bo"), (".gov.ve", "ve"), (".gov.cu", "cu"), (".gov.do", "do"),
    (".gov", "us"), (".mil", "us"),
)


_TWO_PART_SUFFIXES: frozenset = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "gov.au", "edu.au", "co.nz", "govt.nz", "co.jp", "or.jp", "ne.jp",
    "go.jp", "co.kr", "or.kr", "go.kr", "re.kr", "com.br", "gov.br",
    "org.br", "com.cn", "gov.cn", "org.cn", "co.in", "gov.in", "nic.in",
    "org.in", "co.za", "gov.za", "org.za", "com.bd", "gov.bd", "org.bd",
    "com.tr", "gov.tr", "com.sg", "gov.sg", "com.my", "gov.my", "com.pk",
    "gov.pk", "com.ph", "gov.ph", "co.id", "go.id", "co.th", "go.th",
    "com.mx", "gob.mx", "com.ar", "gob.ar", "com.co", "gov.co", "com.pe",
    "gob.pe", "co.ke", "go.ke", "co.il", "gov.il", "com.ng", "gov.ng",
    "co.ke", "com.es", "gob.es", "com.pl", "gov.pl", "co.at", "gv.at",
})


def _registrable(host: str) -> str:
    """Registrable domain of a bare host: the last two labels, or three for the
    common two-part public suffixes (.co.uk, .org.bd, .gov.uk).

    A full public-suffix list would be a dependency this module deliberately
    does not have (see the module docstring); the explicit set above covers the
    suffixes that decide jurisdiction in practice.
    """
    host = (host or "").lower().strip().strip(".")
    if not host or "." not in host:
        return host
    parts = host.split(".")
    if len(parts) >= 3 and f"{parts[-2]}.{parts[-1]}" in _TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def host_jurisdiction(host_or_url: str) -> Optional[str]:
    """ISO2 code of the single country a publisher is bound to, else None.

    None means "not jurisdiction-bound": a multilateral body (worldbank.org,
    imf.org), a global publisher (arxiv.org, doi.org), a reference work, or a
    bare commercial host. Callers must NOT read None as "somewhere else" — it
    means "carries no constraint", which is what keeps those hosts targetable
    for every question.
    """
    text = (host_or_url or "").strip()
    if not text:
        return None
    domain = extract_domain(text) if "//" in text else _registrable(text)
    if not domain:
        return None
    for suffix, iso2 in _JURISDICTION_SUFFIX_RULES:
        if domain.endswith(suffix):
            return iso2
    tld = domain.rsplit(".", 1)[-1]
    if len(tld) != 2 or tld in _PSEUDO_TLDS:
        return None
    if tld in _CCTLD_TO_ISO2:
        return _CCTLD_TO_ISO2[tld]
    return tld if tld in _KNOWN_COUNTRY_CODES else None


_DOMAIN_IN_TEXT_RE = re.compile(
    r"\b([a-z0-9][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+)\b", re.I
)


_UPPER_CODE_RE = re.compile(r"(?<![A-Za-z])(\b[A-Z]{2}\b)(?![A-Za-z])")


def question_jurisdiction(text: str) -> Tuple[str, ...]:
    """ISO2 codes for every country a question or claim names, in MENTION order.

    Mention order is what makes the first target right: "Germany vs France" must
    aim at German publishers first, and a registry-ordered scan returned France
    for it. Ordering by match position also means the country the question leads
    with is the country whose agencies lead the query.

    Returns () when the question names no country, which callers must treat as
    "do not narrow by jurisdiction" — not as "no jurisdiction exists". Most
    questions are global, and narrowing those would be the same bug in reverse.
    """
    raw = text or ""
    blob = raw.lower()
    if not blob.strip():
        return ()
    hits: List[Tuple[int, str]] = []
    for iso2, aliases in _COUNTRY_BY_ISO2_ALIASES.items():
        for alias in aliases:
            m = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", blob)
            if m:
                hits.append((m.start(), iso2))
                break
    for code in _UPPER_CODE_RE.findall(raw):
        iso2 = code.lower()
        if iso2 in _KNOWN_COUNTRY_CODES:
            hits.append((raw.find(code), iso2))
    for candidate in _DOMAIN_IN_TEXT_RE.findall(blob):
        found_iso2 = host_jurisdiction(candidate)
        if found_iso2:
            hits.append((blob.find(candidate), found_iso2))
    seen: Set[str] = set()
    out: List[str] = []
    for _pos, iso2 in sorted(hits):
        if iso2 not in seen:
            seen.add(iso2)
            out.append(iso2)
    return tuple(out)


def jurisdiction_is_admissible(host: str, jurisdictions: Sequence[str]) -> bool:
    """May `host` be a `site:` target for a question about `jurisdictions`?

    True when the host is not bound to one country (multilateral, global, or a
    bare commercial domain), when the question names no country, when the host's
    country is one the question asked about, or when the host is supranational.
    """
    if not jurisdictions:
        return True
    bound = host_jurisdiction(host)
    if bound is None or bound in SUPRANATIONAL_JURISDICTIONS:
        return True
    return bound in jurisdictions


def jurisdiction_site_terms(
    jurisdictions: Sequence[str], max_sites: int = 2
) -> Tuple[str, ...]:
    """Official suffix families for the countries a question names.

    These are the strongest available primary targets for a country-specific
    question: the country's own statistics office, regulator and ministries,
    which no foreign publisher substitutes for.
    """
    out: List[str] = []
    for iso2 in jurisdictions or ():
        for suffix in COUNTRY_OFFICIAL_SUFFIX.get(iso2, ()):  # type: ignore[arg-type]
            term = suffix.lstrip(".")
            # A bare ccTLD would match every host under it; keep the full family.
            if term not in out:
                out.append(term)
            if len(out) >= max(1, max_sites):
                return tuple(out)
    return tuple(out)

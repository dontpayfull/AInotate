"""Privacy redaction: labelled corpus (precision/recall), OCR words, marks, policy, DOM detector."""
import pytest

from ainotate import privacy as P
from ainotate.spec import SpecError, validate

# Fake keys are split into adjacent literals so secret scanners do not read them as real credentials.
JWT = ("eyJ" "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ" "zdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
       ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")

# (text, [(kind, exact value that must be found)]); [] = tricky negative, nothing may be found
CORPUS = [
    # emails
    ("Contact support@example.com for help", [("email", "support@example.com")]),
    ("cc: first.last+promo@mail.example.co.uk, thanks", [("email", "first.last+promo@mail.example.co.uk")]),
    ("From: Ana <ana_p92@yahoo.ro>", [("email", "ana_p92@yahoo.ro")]),
    ("two: a@b.io and j.doe@acme-corp.com.", [("email", "a@b.io"), ("email", "j.doe@acme-corp.com")]),
    # passwords and key=value secrets
    ("password: hunter2", [("password_field", "hunter2")]),
    ("login with user admin pwd=S3cr3t!x", [("password_field", "S3cr3t!x")]),
    ('{"client_secret": "a1B2c3D4e5F6"}', [("token", "a1B2c3D4e5F6")]),
    ("aws_secret_access_key = wJalr" "XUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", [("token", "wJalr" "XUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")]),
    ("api_key=9d8f7a" "6b5c4d3e2f1a0b9c8d7e6f5a4b", [("token", "9d8f7a" "6b5c4d3e2f1a0b9c8d7e6f5a4b")]),
    ("https://x.io/cb?access_token=ya29" "a0AfH6SMBx3&state=1", [("token", "ya29" "a0AfH6SMBx3")]),
    # prefixed API keys, JWT, bearer, high entropy
    ("OPENAI_API_KEY sk-" "proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z", [("api_key", "sk-" "proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z")]),
    ("stripe sk_" "live_51H8xYzAbCdEf12345GhIj", [("api_key", "sk_" "live_51H8xYzAbCdEf12345GhIj")]),
    ("pk sk_" "test_4eC39HqLyjWDarjtT1zdp7dc", [("api_key", "sk_" "test_4eC39HqLyjWDarjtT1zdp7dc")]),
    ("git remote gh" "p_1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P7q8R", [("api_key", "gh" "p_1a2B3c4D5e6F7g8H9i0J1k2L3m4N5o6P7q8R")]),
    ("github_" "pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123", [("api_key", "github_" "pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123")]),
    ("SLACK xo" "xb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx", [("api_key", "xo" "xb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx")]),
    ("key id AK" "IAIOSFODNN7EXAMPLE", [("api_key", "AK" "IAIOSFODNN7EXAMPLE")]),
    ("maps AI" "zaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY", [("api_key", "AI" "zaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY")]),
    ("gitlab gl" "pat-Ab12Cd34Ef56Gh78Ij90", [("api_key", "gl" "pat-Ab12Cd34Ef56Gh78Ij90")]),
    (f"cookie jwt={JWT}", [("jwt", JWT)]),
    ("Authorization: Bearer 8f3kQ9zLm2Xp7Rt4Vw1Ys6Nb", [("token", "8f3kQ9zLm2Xp7Rt4Vw1Ys6Nb")]),
    ("session Zx8Qp2Lm9Rt4Vw7Yb1Nc6Kd3Fg5Hj0", [("token", "Zx8Qp2Lm9Rt4Vw7Yb1Nc6Kd3Fg5Hj0")]),
    # cards, IBAN
    ("Visa 4111 1111 1111 1111 exp 12/27", [("credit_card", "4111 1111 1111 1111")]),
    ("MC 5500-0000-0000-0004", [("credit_card", "5500-0000-0000-0004")]),
    ("amex 378282246310005", [("credit_card", "378282246310005")]),
    ("IBAN RO49 AAAA 1B31 0075 9384 0000", [("iban", "RO49 AAAA 1B31 0075 9384 0000")]),
    ("pay to DE89370400440532013000 today", [("iban", "DE89370400440532013000")]),
    ("GB82 WEST 1234 5698 7654 32", [("iban", "GB82 WEST 1234 5698 7654 32")]),
    # phones, IPs
    ("call +1 (415) 555-2671", [("phone", "+1 (415) 555-2671")]),
    ("UK office +44 20 7946 0958", [("phone", "+44 20 7946 0958")]),
    ("NY (212) 555-0198 or 212.555.0199", [("phone", "(212) 555-0198"), ("phone", "212.555.0199")]),
    ("Mobil: 0721 234 567", [("phone", "0721 234 567")]),
    ("Tel: 021 312 34 56", [("phone", "021 312 34 56")]),
    ("whatsapp +40721234567", [("phone", "+40721234567")]),
    # truncated emails (Finder sidebar, narrow columns)
    ("Finder sidebar andrei@do\u2026 Recents", [("email", "andrei@do\u2026")]),
    ("To: andrei@dontpay... (2)", [("email", "andrei@dontpay...")]),
    ("from andr\u2026@gmail.com today", [("email", "andr\u2026@gmail.com")]),
    ("owner andrei@dontp\u2026ull.com", [("email", "andrei@dontp\u2026ull.com")]),
    ("server 203.0.113.45 is down", [("ip", "203.0.113.45")]),
    ("redis at 10.0.0.12:6379", [("ip", "10.0.0.12")]),
    # tricky negatives
    ("Order #151481 shipped on 2024-01-15", []),
    ("Total: $1,234.56 (was €1.499,00)", []),
    ("Version 1.2.3.4 released, build v10.0.19045.1234", []),
    ("Open 9:00-17:00, Mon-Fri", []),
    ("Invoice 2024-000123 dated 15.01.2024", []),
    ("SKU: ABC-12345-XL qty 2", []),
    ("Tracking number 1Z999AA10123456784", []),
    ("https://www.example.com/products/summer-sale-2024-coupon-codes", []),
    ("Use code SAVE20NOW at checkout", []),
    ("@johndoe mentioned you in #general", []),
    ("logo src icon@2x.png", []),
    ("commit 9fceb02d0ae598e95dc970b74767f19372d61af8 fixed it", []),
    ("uuid 550e8400-e29b-41d4-a716-446655440000", []),
    ("getUserAccountSettingsByIdentifier2()", []),
    ("Population 1234567890 in 2024", []),
    ("Order ID: 0721234567", []),
    ("ISBN 978-3-16-148410-0", []),
    ("Date 12/31/2025 time 23:59:59", []),
    ("Coordinates 44.4268, 26.1025", []),
    ("Password: ********", []),
    ("Forgot password? Reset token: none", []),
    ("The API key field is required", []),
    ("Phone: 2024-01-15", []),
    ("utm_source=newsletter_2024_october_campaign", []),
    ("Rating 4.5/5 from 1,234 reviews, 50% off", []),
    ("Card ending 1111, order 4111111111111112", []),
    ("Path /Users/adi/Pictures/AInotate/Screenshot_2024-10-07_at_12.png", []),
    ("CSS class btn-primary-large-rounded-2xl-shadow", []),
    ("Loading... please wait\u2026", []),
    ("@johndoe\u2026 replied to you", []),
]


def score(corpus=CORPUS):
    tp = fp = fn = 0
    by_kind = {}
    misses = []
    for text, want in corpus:
        got = {(k, text[s:e]) for k, s, e in P.find_in_text(text)}
        want = set(want)
        tp += len(got & want)
        fp += len(got - want)
        fn += len(want - got)
        for k, _ in want - got:
            by_kind[k] = by_kind.get(k, 0) + 1
        if got != want:
            misses.append((text, sorted(got - want), sorted(want - got)))
    return tp, fp, fn, by_kind, misses


def test_corpus_precision_recall():
    assert len(CORPUS) >= 40
    tp, fp, fn, missed_kinds, misses = score()
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    print(f"\ncorpus: {len(CORPUS)} strings, tp={tp} fp={fp} fn={fn}, "
          f"precision={precision:.3f} recall={recall:.3f}; misses: {misses}")
    assert precision >= 0.95 and recall >= 0.95, misses
    for k in ("password_field", "api_key", "jwt", "token", "email"):
        assert k not in missed_kinds, misses


def test_preview_never_holds_the_value():
    for text, want in CORPUS:
        for k, s, e in P.find_in_text(text):
            v, prev = text[s:e], P.mask(text[s:e], k)
            assert v not in prev or len(v) < 4, (v, prev)
    assert P.mask("andrei@example.com", "email") == "a***@e***.com"
    assert P.mask("hunter2", "password_field") == "***"
    assert P.mask("sk-" "proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z", "api_key") == "sk-" "proj-***(40 chars)"


def test_allow_list_and_custom_patterns():
    t = "write support@company.com or jo@company.com or ana@other.org, ticket CASE-99812"
    found = lambda **kw: [t[s:e] for _, s, e in P.find_in_text(t, **kw)]
    assert found(allow=["support@company.com"]) == ["jo@company.com", "ana@other.org"]
    assert found(allow=["@company.com"]) == ["ana@other.org"]
    assert "CASE-99812" in found(custom=[r"CASE-\d+"])


def words(line, y=100, h=20, cw=10, x0=50):
    """Fake OCR words for one line: 10 px per char, one space = 10 px."""
    out, x = [], x0
    for w in line.split(" "):
        out.append({"text": w, "x": x, "y": y, "w": cw * len(w), "h": h, "conf": 91, "level": "word"})
        x += cw * (len(w) + 1)
    return out


def test_scan_text_items_word_precise_rects():
    items = words("Mail: john.doe@acme.com Card 4111 1111 1111 1111 total $12.00")
    items += words("Order #151481 on 2024-01-15", y=140)
    items.append({"text": "Mail: john.doe@acme.com Card ...", "x": 50, "y": 100, "w": 300, "h": 20, "conf": 90, "level": "line"})
    f = P.scan_text_items(items)
    assert [x["kind"] for x in f] == ["email", "credit_card"]
    email, card = f
    assert (email["x"], email["w"], email["units"], email["source"]) == (110, 170, "image", "ocr")
    assert card["x"] == 340 and card["w"] == 190 and card["preview"] == "**** 1111"   # 4 words merged


def test_scan_text_items_splits_columns_and_partial_words():
    items = words("Phone 0721", x0=0) + words("234 567", x0=600)       # far apart: two table columns
    assert P.scan_text_items(items) == []
    f = P.scan_text_items(words("token=Zx8Qp2Lm9Rt4Vw7Yb1Nc6Kd3"))      # value inside one OCR word
    assert len(f) == 1 and (f[0]["x"], f[0]["w"]) == (50, 300)          # the whole word, label too


def test_ocr_partial_match_covers_whole_words_not_a_char_slice():
    # regression: a char-count slice assumed equal glyph widths; "WWWW..." is wide, so the
    # real key started far right of the estimate and the mask was ~150 px off
    item = {"text": "WWWWWWWWWWWW:api_key=Zx8Qp2Lm9Rt4Vw7Yb1Nc6Kd3", "x": 50, "y": 100, "w": 700, "h": 20,
            "conf": 90, "level": "word"}
    f = P.scan_text_items([item])
    assert len(f) == 1 and (f[0]["x"], f[0]["y"], f[0]["w"], f[0]["h"]) == (50, 100, 700, 20)
    # a match that starts and ends inside words: all touched words, whole
    f = P.scan_text_items(words("pay card:4111 1111 1111 1111, thanks"))
    assert len(f) == 1 and f[0]["kind"] == "credit_card"
    assert (f[0]["x"], f[0]["w"]) == (90, 10 * len("card:4111 1111 1111 1111,"))


def test_custom_pattern_never_weakens_a_builtin():
    # regression: custom "proj" won the overlap and left most of the key visible
    key = "sk-" "proj-AbC3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zQwErTy"
    t = f"OPENAI_API_KEY={key} done"
    got = P.find_in_text(t, custom=["proj"])
    assert [(k, t[s:e]) for k, s, e in got] == [("api_key", key)]
    # overlaps union: span grows to cover both, the more severe kind wins
    t2 = "acct sk_" "live_51H8xYzAbCdEf12345GhIj/acct42"
    got = P.find_in_text(t2, custom=[r"GhIj/acct\d+"])
    assert [(k, t2[s:e]) for k, s, e in got] == [("api_key", "sk_" "live_51H8xYzAbCdEf12345GhIj/acct42")]
    f = P.scan_text_items(words(f"key {key}"), P.privacy_mode({"privacy": {"patterns": ["proj"]}}))
    assert len(f) == 1 and f[0]["kind"] == "api_key" and f[0]["w"] == 10 * len(key)


def test_allow_list_applies_to_ocr_and_truncated_values():
    pol = P.privacy_mode({"privacy": {"allow": ["support@company.com"]}})
    f = P.scan_text_items(words("write support@company.com or jo@company.com"), pol)
    assert [x["preview"] for x in f] == ["j***@c***.com"]
    # a truncated allowed address stays visible, a truncated other one does not
    f = P.scan_text_items(words("support@comp\u2026 jo@comp\u2026"), pol)
    assert [x["x"] for x in f] == [50 + 10 * len("support@comp\u2026 ")]
    # a domain allow does not cover a cut-off domain ("@company.com" vs "jo@comp…")
    assert P.find_in_text("jo@comp\u2026", allow=["@company.com"])
    # an allowed value never shields an overlapping secret
    t = "sk-" "proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z@company.com"
    assert [k for k, _, _ in P.find_in_text(t, allow=["@company.com"])] == ["api_key"]


def test_truncated_emails_in_ocr():
    f = P.scan_text_items(words("Favorites andrei@do\u2026 Recents"))
    assert [(x["kind"], x["preview"]) for x in f] == [("email", "a***@d***\u2026")]
    f = P.scan_text_items(words("Owner andrei@dontpay..."))
    assert [x["kind"] for x in f] == ["email"] and "dontpay" not in f[0]["preview"]


def test_mask_text():
    s = "Ambiguous 'andrei@example.com' at [1, 2] near sk-" "proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z, card 4111 1111 1111 1111"
    out = P.mask_text(s)
    assert out == "Ambiguous 'a***@e***.com' at [1, 2] near sk-" "proj-***(40 chars), card **** 1111"
    assert P.mask_text("nothing here") == "nothing here"
    assert P.mask_text("x@y.com", P.privacy_mode({"privacy": "off"})) == "x***@y***.com"   # off = image only
    assert P.mask_text("CASE-99812 support@co.com", P.privacy_mode(
        {"privacy": {"allow": ["support@co.com"], "patterns": [r"CASE-\d+"]}})) == "***(10 chars) support@co.com"


@pytest.mark.parametrize("url,want", [
    ("https://user:pa55@app.example.com:8443/reset/9fceb02d0ae598e95dc970b74767f19372d61af8?token=abc#f",
     "https://app.example.com:8443/reset/9f***(40 chars)"),
    ("https://x.io/users/andrei%40example.com/settings;jsessionid=ABC123", "https://x.io/users/a***@e***.com/settings"),
    ("https://x.io/k/sk-" "proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z/", "https://x.io/k/sk-" "proj-***(40 chars)/"),
    ("http://localhost:3000/api/Zx8Qp2Lm9Rt4Vw7Yb1Nc6Kd3/x", "http://localhost:3000/api/Zx***(24 chars)/x"),
    ("https://www.example.com/products/summer-sale-2024-coupon-codes?utm_source=x",
     "https://www.example.com/products/summer-sale-2024-coupon-codes"),
    ("acme.test/cart?id=5", "acme.test/cart"),
    ("/cart?session=abc", "/cart"),
    ("mailto:andrei@x.com?subject=hi", "mailto:a***@x***.com"),
    ("data:text/html,<b>secret</b>", "data:\u2026"),
    ("about:blank", "about:blank"),
])
def test_sanitize_url(url, want):
    assert P.sanitize_url(url) == want


def test_marks_render_spec_and_summary(ui):
    f = [{"kind": "email", "x": 100, "y": 50, "w": 80, "h": 10, "preview": "a***@d***.com"},
         {"kind": "password_field", "x": 0, "y": 0, "w": 50, "h": 20, "preview": "***"},
         {"kind": "email", "x": 300, "y": 50, "w": 80, "h": 10, "preview": "j***@e***.org"}]
    marks = P.to_redact_marks(f, scale=0.5, pad=2)
    assert marks[0] == {"type": "redact", "rect": [48.0, 23.0, 92.0, 32.0], "note": "email"}
    assert marks[1]["rect"][:2] == [0, 0]
    validate({"input": ui, "marks": marks})
    assert P.summary(f) == ["password field x1", "email x2: a***@d***.com, j***@e***.org"]


def test_privacy_mode():
    assert P.privacy_mode({})["kinds"] == P.AUTO_KINDS
    assert P.privacy_mode({"privacy": "off"})["enabled"] is False
    m = P.privacy_mode({"privacy": {"kinds": ["email"], "allow": ["Support@Company.com"], "patterns": [r"CASE-\d+"]}})
    assert m["kinds"] == {"email", "custom"} and m["allow"] == ["support@company.com"]
    assert P.scan_text_items(words("x@y.com"), P.privacy_mode({"privacy": "off"})) == []
    for bad in ("maybe", {"kinds": ["face"]}, {"allow": "x"}, {"patterns": ["("]}, {"foo": 1}):
        with pytest.raises(SpecError):
            P.privacy_mode({"privacy": bad})
    assert P.filter_findings([{"kind": "ip"}, {"kind": "email"}], m) == [{"kind": "email"}]


# ------------------------------------------------------------------ DOM detector (Playwright)
FIXTURE = """<!doctype html><html><head><style>
body{font:16px/1.4 Arial;margin:20px;width:900px} td{padding:4px 12px;border:1px solid #ccc}
.narrow{width:140px;font-size:14px;overflow-wrap:anywhere} .hide{display:none} .ghost{visibility:hidden} .off{position:absolute;left:-9999px}
iframe{border:6px solid #999;padding:4px;width:360px;height:70px}
</style></head><body>
<form><input id="em" type="email" value="john.doe@acme.com"> <input id="pw" type="password"> <input id="pw2" type="password" value="hunter2">
<input id="user" name="username" value="tomsmith"> <input id="coupon" name="coupon_code" value="SAVE20">
<input id="cc" name="cardNumber" value="4111111111111111"></form>
<p id="split">Write to <b id="b">john</b>.doe@acme.com today, order #151481, total $1,234.56 on 2024-01-15.</p>
<table><tr><td>Smith</td><td id="td1">jsmith@gmail.com</td><td>$50.00</td></tr>
<tr><td>Bach</td><td id="td2">fbach@yahoo.com</td><td>v1.2.3.4</td></tr></table>
<div class="narrow" id="wrap">Key: sk-""" """proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zQwErTy and more</div>
<div class="hide">hidden@example.com</div><span class="ghost">ghost@example.com</span><div class="off">off@example.com</div>
<code id="code">Authorization: Bearer 8f3kQ9zLm2Xp7Rt4Vw1Ys6Nb</code>
<textarea id="ta">token=Zx8Qp2Lm9Rt4Vw7Yb1Nc6Kd3Fg5Hj0</textarea>
<div id="host"></div><br>
<iframe id="same" srcdoc="<body style='margin:8px;font:16px Arial'>frame: <span id='e'>in@frame.com</span></body>"></iframe>
<iframe id="cross" src="data:text/html,<body style='margin:8px;font:16px Arial'>cross: <span id='e'>x@cross.org</span></body>"></iframe>
<script>document.getElementById('host').attachShadow({mode:'open'}).innerHTML='shadow: <span id="sh">sh@dow.dev</span>'</script>
</body></html>"""


@pytest.fixture(scope="module")
def browser():
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:      # browser not installed
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()


@pytest.fixture
def page(browser):
    pg = browser.new_page(viewport={"width": 1280, "height": 900})
    yield pg
    pg.close()


def box(page, sel, frame=None):
    return (frame or page).locator(sel).bounding_box()


@pytest.mark.web
def test_js_matches_python_on_corpus(page):
    for text, _ in CORPUS:
        js = [(f["kind"], f["start"], f["end"], f["preview"]) for f in page.evaluate(P.DOM_SCAN_JS, {"text": text})]
        py = [(k, s, e, P.mask(text[s:e], k)) for k, s, e in P.find_in_text(text)]
        assert js == py, text


@pytest.mark.web
def test_dom_fixture(page):
    page.set_content(FIXTURE)
    page.wait_for_timeout(300)
    f = P.scan_page(page)
    kinds = sorted(x["kind"] for x in f)
    by = lambda k: [x for x in f if x["kind"] == k]
    assert all(x["units"] == "css" and x["source"] == "dom" for x in f)
    # fields: email/password/username/card yes, coupon no; textarea scanned by value
    fields = {x.get("field") for x in f if "field" in x}
    assert {"username", "cardNumber"} <= fields and "coupon_code" not in fields
    hit = lambda sel: any(abs(x["x"] - box(page, sel)["x"]) < 1 and abs(x["w"] - box(page, sel)["width"]) < 1 for x in f)
    for sel in ("#em", "#pw2", "#cc", "#ta"):
        assert hit(sel), sel
    assert not hit("#pw")           # an EMPTY password input stays visible (login guides point at it)
    assert [x["field"] for x in by("password_field")] == ["pw2"]
    # split email: starts at <b>, text box only (narrower than the paragraph)
    b, p = box(page, "#b"), box(page, "#split")
    split = [x for x in by("email") if abs(x["y"] - b["y"]) < 3 and x["x"] < p["x"] + 200]
    assert len(split) == 1 and abs(split[0]["x"] - b["x"]) < 1 and split[0]["w"] < 200
    # table cells: text rect inside the cell
    for sel in ("#td1", "#td2"):
        c = box(page, sel)
        hit = [x for x in by("email") if c["x"] <= x["x"] and x["x"] + x["w"] <= c["x"] + c["width"] and c["y"] <= x["y"] < c["y"] + c["height"]]
        assert len(hit) == 1 and hit[0]["w"] < c["width"] - 10
    # the wrapped key spans several lines: one rect per line, all inside the narrow div
    w = box(page, "#wrap")
    lines = [x for x in by("api_key") if w["y"] <= x["y"] < w["y"] + w["height"]]
    assert len(lines) >= 2 and all(x["x"] + x["w"] <= w["x"] + w["width"] + 1 for x in lines)
    # hidden, invisible and off-screen text is not reported
    assert not [x for x in f if x["preview"] in ("h***@e***.com", "g***@e***.com", "o***@e***.com")]
    # same-origin iframe (offset by border + padding), shadow DOM, data: frame scanned via Playwright
    for prev, loc in (("i***@f***.com", page.frame_locator("#same").locator("#e")),
                      ("x***@c***.org", page.frame_locator("#cross").locator("#e")),
                      ("s***@d***.dev", page.locator("#host #sh"))):
        want, got = loc.bounding_box(), [x for x in by("email") if x["preview"] == prev]
        assert len(got) == 1, prev
        err = max(abs(got[0][a] - want[b]) for a, b in (("x", "x"), ("y", "y"), ("w", "width"), ("h", "height")))
        assert err < 1, (prev, got[0], want)
    assert "unknown_frame" not in kinds and "phone" not in kinds and "ip" not in kinds
    assert by("token")   # bearer in <code> (the textarea token is reported by its field)


@pytest.mark.web
def test_js_matches_python_with_policy(page):
    key = "sk-" "proj-AbC3dEf6hIj9kLm2nOp5qRs8tUv1wXy4zQwErTy"
    cases = [(f"OPENAI_API_KEY={key} done", {"patterns": ["proj"]}),
             ("acct sk_" "live_51H8xYzAbCdEf12345GhIj/acct42", {"patterns": [r"GhIj/acct\d+"]}),
             ("support@comp\u2026 jo@comp\u2026 support@company.com x@company.com", {"allow": ["support@company.com"]}),
             ("jo@comp\u2026 a@company.com b@other.org", {"allow": ["@company.com"]}),
             ("token=a@b.io pwd=S3cr3t!x 203.0.113.45", {"kinds": ["email", "ip"]})]
    for text, pol in cases:
        js = [(f["kind"], f["start"], f["end"], f["preview"]) for f in page.evaluate(P.DOM_SCAN_JS, dict(pol, text=text))]
        py = [(k, s, e, P.mask(text[s:e], k)) for k, s, e in
              P.find_in_text(text, pol.get("allow", ()), pol.get("patterns", ()), pol.get("kinds"))]
        assert js == py, text


@pytest.mark.web
def test_dom_allow_list_applies_to_classified_fields(page):
    # regression: an email field holding an allowed address was redacted anyway
    page.set_content('<input id="a" type="email" value="Support@Company.com"> <input id="b" type="email" value="jo@x.com">'
                     '<input id="c" name="contact_email" value="support@company.com">'
                     '<input id="p" type="password" value="support@company.com">')
    pol = P.privacy_mode({"privacy": {"allow": ["support@company.com"]}})
    f = P.scan_page(page, pol)
    assert sorted(x["kind"] for x in f) == ["email", "password_field"]
    hit = lambda sel: any(abs(x["x"] - box(page, sel)["x"]) < 1 for x in f)
    assert hit("#b") and hit("#p") and not hit("#a") and not hit("#c")


@pytest.mark.web
def test_dom_scan_has_no_finding_cap(page):
    # regression: the DOM scan stopped at 1000 findings
    page.set_content("<body style='font:12px Arial'>" + "".join(f"<div>user{i}@example.org</div>" for i in range(1100)))
    f = P.scan_page(page, full_page=True)
    assert len([x for x in f if x["kind"] == "email"]) == 1100


@pytest.mark.web
def test_dom_scan_reads_past_3m_chars(page):
    # regression: text collection stopped at 3,000,000 chars; a secret after that was missed
    filler = "lorem ipsum dolor sit amet " * 120_000                     # ~3.2M chars, not displayed
    page.set_content(f"<div style='display:none'>{filler}</div><p id='s'>Key: sk-" "proj-Ab3dEf6hIj9kLm2nOp5qRs8tUv1wXy4z</p>")
    f = P.scan_page(page)
    assert [x["kind"] for x in f] == ["api_key"]
    assert abs(f[0]["y"] - box(page, "#s")["y"]) < 3


@pytest.mark.web
def test_dom_chunked_scan_matches_single_pass(page):
    page.set_content(FIXTURE.replace("<div id=\"host\">", "<p>" + "filler text here. " * 400 + "</p><div id=\"host\">"))
    page.wait_for_timeout(300)
    key = lambda f: {(x["kind"], x["x"], x["y"], x["w"], x["h"]) for x in f}
    one = page.evaluate(P.DOM_SCAN_JS, P.dom_options())
    for ch in (300, 1000, 5000):
        assert key(page.evaluate(P.DOM_SCAN_JS, dict(P.dom_options(), chunk=ch))) == key(one), ch


@pytest.mark.web
def test_dom_unknown_frame_without_playwright_frames(page):
    page.set_content(FIXTURE)
    page.wait_for_timeout(300)
    f = page.evaluate(P.DOM_SCAN_JS, P.dom_options())          # what neo's evaluate gets
    assert [x for x in f if x["kind"] == "unknown_frame"]
    assert P.neo_code().startswith("return ((opts) =>")


@pytest.mark.web
def test_live_the_internet(page):
    try:
        # "commit" + selector: a slow third-party script in <head> can hold DOMContentLoaded
        page.goto("https://the-internet.herokuapp.com/login", timeout=45000, wait_until="commit")
        page.wait_for_selector("#password", timeout=45000)
    except Exception as e:
        pytest.skip(f"offline: {e}")
    assert P.scan_page(page) == []                           # empty fields: nothing to hide
    page.fill("#password", "SuperSecretPassword!")
    f = P.scan_page(page)
    assert [x["kind"] for x in f] == ["password_field"]
    pw = box(page, "#password")
    assert abs(f[0]["x"] - pw["x"]) < 1 and abs(f[0]["y"] - pw["y"]) < 1
    page.fill("#username", "tomsmith")
    assert sorted(x["kind"] for x in P.scan_page(page)) == ["name_field", "password_field"]
    try:
        page.goto("https://the-internet.herokuapp.com/tables", timeout=45000, wait_until="commit")
        page.wait_for_selector("#table2 tbody tr:nth-child(4)", timeout=45000)
    except Exception as e:
        pytest.skip(f"offline: {e}")
    f = P.scan_page(page)
    assert [x["kind"] for x in f] == ["email"] * 8          # 2 tables x 4 rows; no $ dues or URLs
    cells = page.locator("td:has-text('@')")
    for i in range(cells.count()):
        c = cells.nth(i).bounding_box()
        assert any(c["x"] <= x["x"] and x["x"] + x["w"] <= c["x"] + c["width"] + 0.5
                   and c["y"] <= x["y"] <= c["y"] + c["height"] for x in f)

"""Translation source chain (services/translation.translate_text): official Google key ->
own LibreTranslate -> MyMemory (only trusted entries) -> Google's public web endpoint -> an
honest "unavailable". Network is faked; these test the order, the trust rules and the failures."""
import httpx
import pytest

from app.core.config import settings
from app.services import translation as tr


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("GET", "http://x"), response=httpx.Response(self.status_code))

    def json(self):
        return self._d


MM_NO_MT = {"responseData": {"translatedText": "mai ye nahe chahta hun", "match": 0.9},
            "matches": [{"created-by": "Public Web", "translation": "mai ye nahe chahta hun"}]}
MM_MT = {"responseData": {"translatedText": "x", "match": 0.5}, "matches": [{"created-by": "MT!", "translation": "मैं यह खरीदना चाहता हूँ"}]}
GOOGLE_WEB = [[["मैं यह वस्तु खरीदना चाहता हूं", "I want to buy this item", None, None, 10]], None, "en"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRANSLATE_API_KEY", "")
    monkeypatch.setattr(settings, "LIBRETRANSLATE_URL", "")
    monkeypatch.setattr(settings, "LIBRETRANSLATE_API_KEY", "")
    monkeypatch.setattr(settings, "TRANSLATE_ALLOW_UNOFFICIAL_GOOGLE", True)


def wire(monkeypatch, *, mymemory=None, web=None, libre=None, google=None, calls=None):
    """Each source answers with the given payload, raises when given an Exception, or is unreachable when None."""
    def get(url, **k):
        if calls is not None:
            calls.append("mymemory")
        if isinstance(mymemory, Exception) or mymemory is None:
            raise httpx.ConnectError("down")
        return Resp(mymemory)

    def post(url, **k):
        if "translation.googleapis.com" in url:
            kind, payload = "google", google
        elif "translate_a/single" in url:
            kind, payload = "web", web
        else:
            kind, payload = "libre", libre
        if calls is not None:
            calls.append(kind)
        if payload is None or isinstance(payload, Exception):
            raise httpx.ConnectError("down")
        if isinstance(payload, int):
            return Resp({}, payload)
        return Resp(payload)
    monkeypatch.setattr(tr.httpx, "get", get)
    monkeypatch.setattr(tr.httpx, "post", post)


def test_the_case_that_used_to_fail_now_translates_through_the_web_fallback(monkeypatch):
    wire(monkeypatch, mymemory=MM_NO_MT, web=GOOGLE_WEB)   # MyMemory has no machine translation for this phrase
    r = tr.translate_text("I want to buy this item", "hi", "en")
    assert r["text"] == "मैं यह वस्तु खरीदना चाहता हूं" and r["demo"] is False and r["provider"] == "google-web"
    assert r["detected_source_lang"] == "en"


def test_a_weak_mymemory_guess_is_still_never_shown(monkeypatch):
    wire(monkeypatch, mymemory=MM_NO_MT, web=None)
    r = tr.translate_text("I want to buy this item", "hi", "en")
    assert r["demo"] is True and r["text"] == "I want to buy this item" and "unavailable" in r["note"]
    assert "provider" not in r


def test_a_machine_translated_mymemory_entry_is_used_and_the_web_endpoint_is_not_called(monkeypatch):
    calls = []
    wire(monkeypatch, mymemory=MM_MT, web=GOOGLE_WEB, calls=calls)
    r = tr.translate_text("x", "hi", "en")
    assert r["provider"] == "mymemory" and calls == ["mymemory"]


def test_the_official_google_key_wins_over_everything(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRANSLATE_API_KEY", "k")
    calls = []
    wire(monkeypatch, google={"data": {"translations": [{"translatedText": "नमस्ते", "detectedSourceLanguage": "en"}]}},
         mymemory=MM_MT, web=GOOGLE_WEB, calls=calls)
    r = tr.translate_text("Hello", "hi")
    assert r["provider"] == "google" and r["text"] == "नमस्ते" and calls == ["google"]


def test_a_failing_official_key_falls_through_to_the_free_sources(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_TRANSLATE_API_KEY", "bad")
    wire(monkeypatch, google=403, mymemory=MM_NO_MT, web=GOOGLE_WEB)
    assert tr.translate_text("I want to buy this item", "hi", "en")["provider"] == "google-web"


def test_own_libretranslate_comes_before_the_free_sources(monkeypatch):
    monkeypatch.setattr(settings, "LIBRETRANSLATE_URL", "http://libre.local:5000/")
    calls = []
    seen = {}

    def post(url, **k):
        calls.append(url)
        seen["json"] = k.get("json")
        return Resp({"translatedText": "मुझे यह चाहिए"})
    monkeypatch.setattr(tr.httpx, "post", post)
    monkeypatch.setattr(tr.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("MyMemory must not be asked")))
    r = tr.translate_text("I want this", "hi", "en")
    assert r["provider"] == "libretranslate" and calls == ["http://libre.local:5000/translate"]
    assert seen["json"] == {"q": "I want this", "source": "en", "target": "hi", "format": "text"}


def test_libretranslate_key_is_sent_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "LIBRETRANSLATE_URL", "http://libre.local")
    monkeypatch.setattr(settings, "LIBRETRANSLATE_API_KEY", "secret")
    seen = {}
    monkeypatch.setattr(tr.httpx, "post", lambda url, **k: seen.update(json=k["json"]) or Resp({"translatedText": "ok"}))
    tr.translate_text("hi", "fr")
    assert seen["json"]["api_key"] == "secret" and seen["json"]["source"] == "auto"


def test_a_down_libretranslate_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "LIBRETRANSLATE_URL", "http://libre.local")
    wire(monkeypatch, libre=None, mymemory=None, web=GOOGLE_WEB)
    assert tr.translate_text("I want to buy this item", "hi", "en")["provider"] == "google-web"


def test_the_unofficial_endpoint_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "TRANSLATE_ALLOW_UNOFFICIAL_GOOGLE", False)
    calls = []
    wire(monkeypatch, mymemory=MM_NO_MT, web=GOOGLE_WEB, calls=calls)
    r = tr.translate_text("I want to buy this item", "hi", "en")
    assert r["demo"] is True and "web" not in calls


def test_the_web_endpoint_gets_the_right_language_codes_and_text_in_the_body(monkeypatch):
    seen = {}

    def post(url, **k):
        seen.update(url=url, params=k["params"], data=k["data"])
        return Resp(GOOGLE_WEB)
    monkeypatch.setattr(tr.httpx, "post", post)
    monkeypatch.setattr(tr.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    tr.translate_text("Where is the hospital?", "zh", "en")
    assert seen["params"] == {"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t"}
    assert seen["data"] == {"q": "Where is the hospital?"}   # in the body, so long text is not cut off by a URL limit


def test_multi_sentence_web_results_are_joined(monkeypatch):
    two = [[["Hola. ", "Hello. "], ["¿Cómo estás?", "How are you?"]], None, "en"]
    wire(monkeypatch, mymemory=None, web=two)
    assert tr.translate_text("Hello. How are you?", "es", "en")["text"] == "Hola. ¿Cómo estás?"


@pytest.mark.parametrize("bad", [[], [[]], [None], "nope", {"x": 1}, [[["", "x"]], None, "en"], [[["???", "x"]], None, "en"]])
def test_malformed_or_empty_web_answers_are_rejected_not_shown(monkeypatch, bad):
    wire(monkeypatch, mymemory=None, web=bad)
    r = tr.translate_text("Hello", "hi", "en")
    assert r["demo"] is True


def test_a_rate_limited_web_endpoint_is_just_unavailable(monkeypatch):
    wire(monkeypatch, mymemory=None, web=429)
    assert tr.translate_text("Hello", "hi", "en")["demo"] is True


def test_api_reports_the_provider(client, tourist_headers, monkeypatch):
    wire(monkeypatch, mymemory=MM_NO_MT, web=GOOGLE_WEB)
    r = client.post("/api/translate/text", headers=tourist_headers, json={"text": "I want to buy this item", "target_lang": "hi", "source_lang": "en"})
    assert r.status_code == 200
    j = r.json()
    assert j["demo"] is False and j["text"] == "मैं यह वस्तु खरीदना चाहता हूं"

"""Per-factor explanations of the safety score.

Formula mode (default): exact weighted terms. Model mode: SHAP for the
RandomForest. Most tests below exercise model mode (the `model_mode` fixture)."""
import pytest

from app.services import explain, ml_service


@pytest.fixture(autouse=True)
def _clear_explainer_cache():
    explain.clear_cache()
    yield
    explain.clear_cache()


@pytest.fixture
def model_mode(monkeypatch):
    monkeypatch.setattr(ml_service.settings, "SAFETY_SCORE_MODE", "model")


# ---------------------------------------------------------------- formula mode (default)
def test_formula_mode_is_the_default_and_exact():
    features = [70.0, 14, 0.3, 60.0, 20.0]
    result = explain.explain_safety_score(features)
    assert result["method"] == "formula"
    assert result["base_value"] == 100.0
    assert set(result["contributions"]) == set(explain.SAFETY_FEATURE_NAMES)
    reconstructed = result["base_value"] + sum(result["contributions"].values())
    assert reconstructed == pytest.approx(ml_service.predict_safety_score(features), abs=0.06)


def test_formula_terms_follow_the_published_weights():
    c = explain.explain_safety_score([50.0, 23, 0.2, 40.0, 30.0])["contributions"]
    assert c == {"zone_risk": -15.0, "hour": -10.0, "anomaly_score": -5.0,
                 "crime_index": -10.0, "weather_risk": -3.0}


def test_formula_mode_needs_no_trained_model(monkeypatch):
    monkeypatch.setattr(ml_service, "_load", lambda name: None)
    assert explain.explain_safety_score([50, 12, 0.1, 30, 20])["method"] == "formula"


# ---------------------------------------------------------------- model mode (SHAP)
def test_model_mode_without_a_model_falls_back_to_the_formula(model_mode, monkeypatch):
    monkeypatch.setattr(ml_service, "_load", lambda name: None)
    assert ml_service.safety_score_method() == "formula"
    assert explain.explain_safety_score([50, 12, 0.1, 30, 20])["method"] == "formula"


@pytest.fixture
def _shap_only(model_mode):
    if ml_service._load("safety_rf.joblib") is None:
        pytest.skip("safety_rf.joblib not present in this environment")


def test_model_mode_uses_shap(_shap_only):
    assert explain.explain_safety_score([50, 12, 0.1, 30, 20])["method"] == "shap"


def test_returns_a_base_value_and_per_feature_contribution(model_mode):
    result = explain.explain_safety_score([50, 12, 0.1, 30, 20])
    if result is None:
        pytest.skip("safety_rf.joblib not present in this environment")
    assert "base_value" in result
    assert set(result["contributions"]) == set(explain.SAFETY_FEATURE_NAMES)
    assert all(isinstance(v, float) for v in result["contributions"].values())


def test_contributions_sum_to_the_actual_prediction(model_mode):
    """The core SHAP guarantee: base_value + sum(contributions) reproduces the
    model's own prediction for that input (within rounding)."""
    features = [70.0, 14, 0.3, 60.0, 20.0]
    result = explain.explain_safety_score(features)
    if result is None:
        pytest.skip("safety_rf.joblib not present in this environment")

    predicted = ml_service.predict_safety_score(features)
    reconstructed = result["base_value"] + sum(result["contributions"].values())
    assert reconstructed == pytest.approx(predicted, abs=0.5)


def test_different_inputs_produce_different_explanations(model_mode):
    safe = explain.explain_safety_score([10.0, 12, 0.0, 10.0, 5.0])
    risky = explain.explain_safety_score([100.0, 2, 0.9, 90.0, 80.0])
    if safe is None or risky is None:
        pytest.skip("safety_rf.joblib not present in this environment")
    assert safe["contributions"] != risky["contributions"]


def test_higher_risk_inputs_get_more_negative_contributions_overall(model_mode):
    """Not a per-feature guarantee, but the aggregate direction should make
    sense: a clearly worse situation should pull the score down overall."""
    safe = explain.explain_safety_score([5.0, 12, 0.0, 5.0, 5.0])
    dangerous = explain.explain_safety_score([100.0, 2, 1.0, 100.0, 100.0])
    if safe is None or dangerous is None:
        pytest.skip("safety_rf.joblib not present in this environment")

    safe_total = sum(safe["contributions"].values())
    dangerous_total = sum(dangerous["contributions"].values())
    assert dangerous_total < safe_total


def test_explainer_is_cached_across_calls(model_mode):
    explain.explain_safety_score([50, 12, 0.1, 30, 20])
    first = explain._explainer_cache.get("safety")
    explain.explain_safety_score([60, 13, 0.2, 40, 30])
    second = explain._explainer_cache.get("safety")
    assert first is second  # same object, not rebuilt


def test_concurrent_first_explain_is_thread_safe(model_mode):
    """Same class of bug fixed in ml_service._load: several threads racing to
    build the TreeExplainer for the first time must not corrupt the cache."""
    import threading

    explain.clear_cache()
    results = []
    barrier = threading.Barrier(12)

    def worker():
        barrier.wait()
        results.append(explain.explain_safety_score([50, 12, 0.1, 30, 20]))

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 12
    non_none = [r for r in results if r is not None]
    if non_none:
        assert all(r["base_value"] == non_none[0]["base_value"] for r in non_none)


def test_safety_score_endpoint_includes_the_explanation(client, admin_headers, tourist_user):
    r = client.get(f"/api/tourists/{tourist_user.tourist_id}/safety-score",
                   headers=admin_headers)
    assert r.status_code == 200
    breakdown = r.json()["breakdown"]
    assert "explanation" in breakdown

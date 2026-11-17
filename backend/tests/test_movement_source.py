"""Real (GeoLife) vs synthetic normal movement for the anomaly model."""
import gzip

import pandas as pd
import pytest

from app.ml import generate_data


def _write_fake_geolife(path, n=500):
    df = pd.DataFrame({
        "speed_kmh": [4.0 + (i % 30) for i in range(n)],
        "dist_from_prev_m": [30.0 + (i % 50) for i in range(n)],
        "inactivity_min": [0.25 + (i % 8) * 0.25 for i in range(n)],
        "dist_from_route_m": [100.0 + (i % 40) * 20 for i in range(n)],
        "person": [i % 7 for i in range(n)],
    })
    with gzip.open(path, "wt") as f:
        df.to_csv(f, index=False)
    return df


@pytest.fixture
def fake_geolife(tmp_path, monkeypatch):
    p = tmp_path / "geolife_movement.csv.gz"
    df = _write_fake_geolife(p)
    monkeypatch.setattr(generate_data, "GEOLIFE_MOVEMENT", str(p))
    return df


@pytest.fixture
def no_geolife(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_data, "GEOLIFE_MOVEMENT", str(tmp_path / "missing.csv.gz"))


def test_auto_uses_real_movement_when_the_file_exists(fake_geolife):
    assert generate_data.resolve_movement_source("auto") == "geolife"
    assert generate_data.resolve_movement_source("synthetic") == "synthetic"


def test_auto_falls_back_to_synthetic_without_the_file(no_geolife):
    assert generate_data.resolve_movement_source("auto") == "synthetic"


def test_asking_for_geolife_without_the_file_is_an_error(no_geolife):
    with pytest.raises(FileNotFoundError):
        generate_data.resolve_movement_source("geolife")


def test_geolife_rows_are_the_normal_class_plus_simulated_anomalies(fake_geolife):
    df = generate_data.generate_movement_data(n_normal=300, n_anomaly=30, source="geolife")
    assert list(df.columns) == [*generate_data.MOVEMENT_FEATURES, "label"]
    assert (df["label"] == 0).sum() == 300
    assert (df["label"] == 1).sum() == 30
    normal = df[df["label"] == 0]
    # Normal rows are exactly the real ones (no synthetic draws mixed in).
    real_speeds = set(fake_geolife["speed_kmh"])
    assert set(normal["speed_kmh"]).issubset(real_speeds)


def test_requesting_more_normal_rows_than_exist_uses_them_all(fake_geolife):
    df = generate_data.generate_movement_data(n_normal=5_000, n_anomaly=500, source="geolife")
    assert (df["label"] == 0).sum() == len(fake_geolife)
    # ...and keeps the requested anomaly share (10%) instead of 500 anomalies.
    assert (df["label"] == 1).sum() == 50


def test_training_records_where_the_data_came_from(fake_geolife, tmp_path):
    from app.ml import train_anomaly

    metrics = train_anomaly.train(str(tmp_path / "models"), movement_source="auto")
    assert metrics["data_source"] == "geolife"
    assert "GeoLife" in metrics["data_provenance"]["normal"]
    assert "simulated" in metrics["data_provenance"]["anomalies"]
    assert 0.0 <= metrics["roc_auc"] <= 1.0


def test_training_can_still_use_synthetic_on_request(fake_geolife, tmp_path):
    from app.ml import train_anomaly

    metrics = train_anomaly.train(str(tmp_path / "models"), movement_source="synthetic")
    assert metrics["data_source"] == "synthetic"

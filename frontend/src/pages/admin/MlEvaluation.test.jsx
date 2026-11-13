import { describe, it, beforeEach, expect } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../../api'
import MlEvaluation from './MlEvaluation'

const mock = new MockAdapter(api)

const group = (g, n) => ({ group: g, n, n_suspicious: 20, n_safe: 20, tpr: 0.9, fpr: 0.1, fnr: 0.1, selection_rate: 0.5, accuracy: 0.9, low_confidence: false })
const fair = { groups: [group('domestic', 60), group('international', 40)], equalized_odds_difference: 0.2,
  demographic_parity_difference: 0.05, tpr_gap: 0.2, fpr_gap: 0.05, verdict: 'review', low_confidence: false }
const det = (key, name, type) => ({ key, name, type, tp: 1, fp: 1, fn: 1, tn: 1, recall: 0.9, fpr: 0.1, precision: 0.8, f1: 0.85, accuracy: 0.9, auc: 0.95 })
const cal = { points: [{ pred: 0.2, obs: 0.1, n: 10 }, { pred: 0.8, obs: 0.9, n: 10 }], brier: 0.1, ece: 0.1 }
const stat = { n: 3, mean_s: 30, median_s: 30, p90_s: 40 }
const report = {
  run_id: 'abc123', created_at: '2026-11-13T10:00:00+00:00', duration_s: 4.2,
  config: { seed: 7, n_scenarios: 100 },
  models: { inference_mode: 'model', active_versions: { anomaly: 6, safety: 7 } },
  totals: { n_scenarios: 100, n_suspicious: 40, n_safe: 60, n_pings: 1900 },
  headline: { detection_rate: 0.925, false_positive_rate: 0.15, precision: 0.804, recall: 0.925, f1: 0.86, accuracy: 0.9,
    roc_auc: 0.978, avg_detection_delay_min: 24.5, median_detection_delay_min: 4, p90_detection_delay_min: 80,
    inference_ms_per_ping: 0.03, expected_precision_at_5pct_prevalence: 0.24, ping_false_alarm_rate: 0.02 },
  confusion: { tn: 51, fp: 9, fn: 3, tp: 37 },
  detectors: [det('anomaly_model', 'Isolation Forest (anomaly)', 'ML'), det('system', 'Combined system', 'System')],
  by_scenario: [{ kind: 'abduction', name: 'Abduction jump', label: 'suspicious', n: 10, flagged: 10, rate: 1, avg_delay_min: 0.3,
    by_detector: { anomaly_model: 1, route_deviation: 1, geofence: 0.5 } }],
  curves: { roc: { system: [[0, 0], [0.1, 0.9], [1, 1]], anomaly_model: [[0, 0], [1, 1]] }, pr: { system: [[0, 1], [1, 0.5]], anomaly_model: [[0, 1]] },
    score_hist: [{ bin: '0.0-0.1', safe: 3, suspicious: 0 }], calibration: { anomaly_model: cal, safety_rf: cal, system: cal } },
  fairness: { traveller_type: { system: fair, anomaly_model: fair }, age_band: { system: fair, anomaly_model: fair },
    tolerance: 0.1, min_group_n: 15, note: 'Groups are synthetic attributes.' },
  explainability: { mean_abs_contribution: { zone_risk: 9.8, hour: 1.8 }, n_samples: 100 },
  response: { all: { incidents: 3, acknowledge: stat, dispatch: stat, resolve: stat },
    simulated: { incidents: 1, acknowledge: stat, dispatch: stat, resolve: stat } },
}

beforeEach(() => {
  mock.reset()
  mock.onGet('/ml/registry').reply(200, { anomaly: { active_version: 6, versions: [
    { version: 6, trained_at: '2026-11-13T13:06:00', dataset_hash: 'ff86ecc', metrics_summary: { model: 'IsolationForest', precision: 0.96, recall: 0.96, f1: 0.96, roc_auc: 1 } }] } })
  mock.onGet('/ml/drift').reply(200, { available: false, reason: 'Only 3 live pings recorded' })
})

describe('AI/ML Evaluation tab', () => {
  it('shows an empty state with a run button before any evaluation', async () => {
    mock.onGet('/ml/evaluation/latest').reply(200, { latest: null, history: [] })
    const { findByText, getByRole } = render(<MlEvaluation />)
    await findByText(/No evaluation has been run yet/)
    expect(getByRole('button', { name: /Run evaluation/ })).toBeTruthy()
  })

  it('renders headline metrics, fairness verdict, registry and the honesty banner', async () => {
    mock.onGet('/ml/evaluation/latest').reply(200, { latest: report, history: [{ run_id: 'abc123', created_at: report.created_at, n_scenarios: 100, seed: 7 }] })
    const { findByText, getAllByText } = render(<MlEvaluation />)
    await findByText('92.5%')                                  // detection rate
    expect(getAllByText('15.0%').length).toBeGreaterThan(0)    // false alarm rate
    expect(await findByText(/Review: gap above 10%/)).toBeTruthy()
    expect(await findByText(/Read this honestly/)).toBeTruthy()
    expect(await findByText('ff86ecc')).toBeTruthy()           // registry
    expect(await findByText('Abduction jump')).toBeTruthy()
  })

  it('Run posts the chosen scenario count and seed, then shows the new report', async () => {
    mock.onGet('/ml/evaluation/latest').replyOnce(200, { latest: null, history: [] })
    mock.onPost('/ml/evaluation/run').reply(200, report)
    mock.onGet('/ml/evaluation/latest').reply(200, { latest: report, history: [] })
    const { findByText, getByRole, getByLabelText } = render(<MlEvaluation />)
    await findByText(/No evaluation has been run yet/)
    fireEvent.change(getByLabelText('Scenarios'), { target: { value: '200' } })
    fireEvent.change(getByLabelText('Seed'), { target: { value: '42' } })
    fireEvent.click(getByRole('button', { name: /Run evaluation/ }))
    await findByText('92.5%')
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ n_scenarios: 200, seed: 42 })
  })
})

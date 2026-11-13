# Test report — 2026-11-13

All tests use SYNTHETIC fixtures or constructed Dhan payloads (no network, no real credentials). They verify deterministic logic only and are **not** real-market validation.

## Backend (pytest)
```
tests/test_api_backtest.py::test_health_public_everything_else_requires_login PASSED
tests/test_api_backtest.py::test_first_run_and_dashboard_shows_disconnected_feed PASSED
tests/test_api_backtest.py::test_credentials_never_returned PASSED
tests/test_api_backtest.py::test_frozen_values_need_explicit_text_and_are_audited PASSED
tests/test_api_backtest.py::test_live_enable_blocked_without_feature_flag PASSED
tests/test_api_backtest.py::test_kill_switch_requires_confirmation PASSED
tests/test_api_backtest.py::test_websocket_rejects_without_token PASSED
tests/test_api_backtest.py::test_backtest_no_lookahead_prefix_invariance PASSED
tests/test_api_backtest.py::test_backtest_costs_with_premium_overlay PASSED
tests/test_api_backtest.py::test_metrics_basic_and_small_sample_flag PASSED
tests/test_derivatives.py::test_pcr_and_max_pain_hand_computed PASSED
tests/test_derivatives.py::test_missing_oi_gives_none_not_fabricated PASSED
tests/test_derivatives.py::test_oi_change_alone_never_concludes_writing PASSED
tests/test_derivatives.py::test_oi_rising_with_rising_premium_is_not_put_writing PASSED
tests/test_derivatives.py::test_bullish_and_bearish_inference PASSED
tests/test_derivatives.py::test_intraday_basis_and_wall_shift PASSED
tests/test_derivatives.py::test_put_call_parity_and_iv_roundtrip PASSED
tests/test_derivatives.py::test_ensure_greeks_never_overwrites_feed_and_labels_calculated PASSED
tests/test_derivatives.py::test_year_fraction_expiry_at_1530 PASSED
tests/test_derivatives.py::test_selection_prefers_band_and_rejects_bad_liquidity PASSED
tests/test_derivatives.py::test_selection_rejects_iv_spike_and_expired_contract PASSED
tests/test_dhan.py::test_parse_option_chain_fields_and_missing_values PASSED
tests/test_dhan.py::test_parse_option_chain_rejects_malformed[bad0] PASSED
tests/test_dhan.py::test_parse_option_chain_rejects_malformed[bad1] PASSED
tests/test_dhan.py::test_parse_option_chain_rejects_malformed[bad2] PASSED
tests/test_dhan.py::test_parse_option_chain_rejects_malformed[bad3] PASSED
tests/test_dhan.py::test_parse_option_chain_rejects_malformed[bad4] PASSED
tests/test_dhan.py::test_parse_candles_and_completeness PASSED
tests/test_dhan.py::test_expiry_list PASSED
tests/test_dhan.py::test_ws_packets_per_sdk_layout PASSED
tests/test_dhan.py::test_instrument_master_compact PASSED
tests/test_dhan.py::test_client_headers_and_no_token_in_repr PASSED
tests/test_dhan.py::test_client_auth_error_stops_requests PASSED
tests/test_dhan.py::test_client_rate_limit_backs_off PASSED
tests/test_dhan.py::test_client_invalid_json PASSED
tests/test_dhan.py::test_live_broker_cannot_be_constructed_in_tests PASSED
tests/test_dhan.py::test_correlation_id_deterministic_and_short PASSED
tests/test_dhan.py::test_token_expiry_decode_and_mask PASSED
tests/test_engine_scoring_signals.py::test_score_maxes_total_exactly_100 PASSED
tests/test_engine_scoring_signals.py::test_missing_data_scores_zero_per_component PASSED
tests/test_engine_scoring_signals.py::test_setup_a_bullish_signal_generated_with_full_breakdown PASSED
tests/test_engine_scoring_signals.py::test_setup_b_bearish_mirror PASSED
tests/test_engine_scoring_signals.py::test_mandatory_overrides_score_stale_data PASSED
tests/test_engine_scoring_signals.py::test_mandatory_overrides_score_risk_gate PASSED
tests/test_engine_scoring_signals.py::test_min_score_enforced PASSED
tests/test_engine_scoring_signals.py::test_entry_cutoff_blocks PASSED
tests/test_engine_scoring_signals.py::test_deterministic_and_reproducible PASSED
tests/test_engine_scoring_signals.py::test_reward_risk_blocked_by_nearby_resistance PASSED
tests/test_engine_scoring_signals.py::test_premium_plan_nonlinear PASSED
tests/test_engine_scoring_signals.py::test_signal_expiry_after_three_completed_candles_and_dedupe PASSED
tests/test_engine_scoring_signals.py::test_signal_trigger_and_invalidation PASSED
tests/test_engine_scoring_signals.py::test_still_valid_cancels_on_close_below_stop PASSED
tests/test_engine_scoring_signals.py::test_setup_c_breakout_retest_detection PASSED
tests/test_indicators.py::test_sma_and_volume_sma PASSED
tests/test_indicators.py::test_ema_seed_and_recursion PASSED
tests/test_indicators.py::test_ema_no_lookahead PASSED
tests/test_indicators.py::test_atr_wilder PASSED
tests/test_indicators.py::test_rsi_extremes PASSED
tests/test_indicators.py::test_adx_trending_vs_flat PASSED
tests/test_indicators.py::test_session_vwap_resets_and_external_volume PASSED
tests/test_indicators.py::test_crosses_and_slope PASSED
tests/test_paper_service.py::test_paper_is_default_and_live_disabled PASSED
tests/test_paper_service.py::test_paper_broker_partial_fills_latency_and_reject PASSED
tests/test_paper_service.py::test_end_to_end_signal_entry_partial_exit_and_close PASSED
tests/test_paper_service.py::test_restart_recovery_restores_open_trade_counters_and_floor PASSED
tests/test_paper_service.py::test_duplicate_evaluation_does_not_duplicate_signal PASSED
tests/test_paper_service.py::test_stale_tick_never_triggers PASSED
tests/test_paper_service.py::test_entry_rejected_when_no_fresh_quote_cancels_signal_without_counting PASSED
tests/test_paper_service.py::test_kill_switch_blocks_and_exits PASSED
tests/test_paper_service.py::test_live_mode_requires_confirmation_and_is_not_reachable_by_default PASSED
tests/test_paper_service.py::test_paper_reset_requires_confirmation_and_audits PASSED
tests/test_risk.py::test_charges_effective_dated_stt PASSED
tests/test_risk.py::test_charges_components_buy_leg PASSED
tests/test_risk.py::test_schedule_roundtrip_serialization_and_missing_date PASSED
tests/test_risk.py::test_sizing_respects_risk_and_funds PASSED
tests/test_risk.py::test_sizing_needs_verified_lot_size PASSED
tests/test_risk.py::test_daily_limits_per_index_independent PASSED
tests/test_risk.py::test_gate_blocks_kill_switch_uncertain_cutoff PASSED
tests/test_risk.py::test_secure_exit_stepped_monotonic_floor PASSED
tests/test_risk.py::test_secure_exit_profit_target_default_exit PASSED
tests/test_risk.py::test_secure_exit_small_capital_continuous_trail PASSED
tests/test_risk.py::test_secure_exit_max_loss_and_daily PASSED
tests/test_risk.py::test_secure_state_survives_serialization PASSED
tests/test_risk.py::test_exit_stop_and_premium_stop PASSED
tests/test_risk.py::test_t1_books_half_moves_stop_and_trails_monotonic PASSED
tests/test_risk.py::test_single_lot_t1_exit_all_and_eod_and_structure PASSED
tests/test_structure_levels_patterns.py::test_pivots_confirmed_only_after_n_bars_no_repaint PASSED
tests/test_structure_levels_patterns.py::test_structure_uptrend_and_breakdown PASSED
tests/test_structure_levels_patterns.py::test_fibonacci_levels_up_leg PASSED
tests/test_structure_levels_patterns.py::test_previous_day_and_opening_range PASSED
tests/test_structure_levels_patterns.py::test_confluence_counts_independent_families PASSED
tests/test_structure_levels_patterns.py::test_oi_levels_from_chain PASSED
tests/test_structure_levels_patterns.py::test_single_candle_patterns PASSED
tests/test_structure_levels_patterns.py::test_two_three_candle_patterns PASSED
tests/test_structure_levels_patterns.py::test_inside_bar_breakout_detection PASSED
tests/test_structure_levels_patterns.py::test_flag_detection PASSED
tests/test_structure_levels_patterns.py::test_double_bottom_triangle_hs_on_zigzag PASSED
tests/test_structure_levels_patterns.py::test_patterns_need_completed_candle PASSED
tests/test_structure_levels_patterns.py::test_resample_session_anchor_and_completeness PASSED
tests/test_structure_levels_patterns.py::test_tick_builder_ignores_late_ticks PASSED
tests/test_structure_levels_patterns.py::test_trend_filter_bullish_and_sideways PASSED
======================== 101 passed, 1 warning in 4.63s ========================
```

## Frontend (vitest)
```

 Test Files  1 passed (1)
      Tests  3 passed (3)
   Start at  18:13:46
   Duration  1.04s (transform 87ms, setup 0ms, collect 209ms, tests 60ms, environment 406ms, prepare 196ms)

```

## Failures found and fixed during development
* Synthetic fixtures initially produced a hammer larger than 1.5 ATR: the engine correctly rejected it as "chasing". The fixture was fixed, not the rule.
* The backtest tests were vacuous at first (0 trades): the synthetic data now injects pullbacks, and the tests assert that trades exist.
* Test-side arithmetic mistakes (SEBI fee ₹0.10 per lakh; crossing count) were corrected.
* The chart drew VWAP continuously across sessions: the line now breaks at each new session.
* Verified in a browser (Playwright, desktop and Pixel 7): login, all 11 screens, no console errors.

## Not verifiable in the build environment
* Live Dhan API responses (hosts blocked); see docs/DHAN_INTEGRATION.md.
* Pine Script compilation (TradingView only).
* Windows .bat execution (the build ran on Linux; the scripts use only standard cmd/PowerShell commands).

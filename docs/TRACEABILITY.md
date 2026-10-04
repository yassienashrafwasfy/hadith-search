# Requirements traceability matrix

This file links each requirement to the code that implements it and the tests that cover it. Every
requirement comes from a statement already in the repo (`README.md`, `CLAUDE.md`,
`docs/ARCHITECTURE.md`, `docs/HANDOFF.md`, `docs/CORPUS_DECISIONS.md`). Nothing was added from
outside. Where a statement is unclear, the item is under "Open questions" and not guessed.

How it was built: read the five docs, read the code they point at, and grep every test name below
in `tests/` (a script checked that each `path::name` exists). No test was run, and no coverage or
mutation tool was used, so "covered" means "a test with that name exists and exercises the
behaviour in its name", not "line coverage". Tests under `tests/` need PostgreSQL with pgvector
(`TEST_DATABASE_URL`); without it they skip.

Source shorthand: `README` = README.md section "Business requirements" unless another section is
named; `HO n` = docs/HANDOFF.md item n; `ARCH` = docs/ARCHITECTURE.md; `CD n` =
docs/CORPUS_DECISIONS.md decision n; `CLAUDE` = CLAUDE.md.

## Summary

| Id | Requirement | Status |
| --- | --- | --- |
| R-01 | Eight search methods exist and return results | Covered |
| R-02 | SQL ranking matches the old in-memory scores | Covered |
| R-03 | Dense methods are Arabic only; English gets 422 | Covered |
| R-04 | Same query gives the same order (ties by id) | Covered |
| R-05 | Search takes filters and returns hadiths in rank order | Covered |
| R-06 | REST resources live under `/api/v1` with the right verbs and codes | Covered |
| R-07 | Errors are `application/problem+json` | Covered |
| R-08 | Cacheable reads carry `Cache-Control`, `ETag`, 304, `_links` | Covered |
| R-09 | `GET /api/v1/search-methods` lists enabled methods and languages | Covered |
| R-10 | JWT auth: signed, expiring, HS256 pinned | Covered |
| R-11 | Password rules and hashing | Covered |
| R-12 | Sign-in does not reveal which usernames exist | Covered |
| R-13 | Annotator sign-up and query assignment | Covered |
| R-14 | Labels and progress: scoped, idempotent, validated | Covered |
| R-15 | Inter-annotator agreement | Covered |
| R-16 | Benchmark endpoints | Covered |
| R-17 | KV pairs: login required, list, filter, statistics, verify | Covered |
| R-18 | KV pair shows the hadith text through `hadith_id` | Covered |
| R-19 | Settings class reads the environment once | Covered |
| R-20 | `APP_ENV` dev, test, prod behaviour | Covered |
| R-21 | Feature flags and `APP_MODE` presets decide what runs | Covered |
| R-22 | DB lifespan: create schema, explain failure, always dispose | Covered |
| R-23 | Users never see status codes or technical error text | Partly covered (status-to-message map only) |
| R-24 | Search queue in the app (`SearchLimiter`) | Covered |
| R-25 | nginx smoothing for search and the sign-in limit | Partly covered (script, not pytest) |
| R-26 | Foreign keys enforced; corpus rebuild is one transaction | Covered |
| R-27 | Schema is in third normal form | Covered |
| R-28 | Schema changes are additive (blue and green share a DB) | Covered |
| R-29 | The DB layer uses the ORM only, no raw SQL | Covered |
| R-30 | Each test runs in its own database schema | Covered |
| R-31 | Blue/green and canary releases | Covered |
| R-32 | Image, Dockerfile and secret scanning | Not covered by tests |
| R-33 | Security hardening (headers, path traversal, CORS, input limits) | Covered |
| R-34 | Corpus build: reconstruction, bilingual drop, grade normalisation | Covered |
| R-35 | Preprocessing and the second-stage drop | Covered |
| R-36 | Index and embeddings use matn only | Covered |
| R-37 | Arabic ONNX encoder, loaded once, one thread by default | Covered |
| R-38 | Build pipeline orchestrator | Covered |
| R-39 | One-time copy from the old SQLite install | Covered |
| R-40 | Evaluation metrics and statistics | Covered |
| R-41 | Recall proxy for the semantic methods | Covered |
| R-42 | Package-level lazy exports | Covered |
| R-43 | Frontend picker lists only methods the server offers | Not covered |
| R-44 | Load-test performance figures | Not covered |
| R-45 | The app serves the built frontend with a fallback route | Covered |
| R-46 | Blue/green rollback: automatic after a failed promote, manual, dry run | Partly covered (script, not pytest) |
| R-47 | Security suite: tokens, IDOR, sign-up and sign-in input, injection | Covered (`tests/security`; open findings are strict xfail) |
| R-48 | Security suite: input validation, error hygiene, prod docs hidden | Covered (`tests/security`; findings are strict xfail) |
| R-49 | Security suite: headers, CORS, nginx config, settings, abuse, secrets | Covered (`tests/security`; gaps are strict xfail) |
| R-50 | Health route: `GET /api/v1/health` checks the process and the database | Covered |
| R-51 | Image pruning keeps the newest 3 release images and the ones in use | Covered (fake docker; real run by hand) |
| R-52 | `tools/dead-code.sh` finds dead code with the repo's vulture config and never writes into the repo | Covered (`tools/test-dead-code.sh`, by hand) |
| R-53 | Model promotion picks the best MLflow version by nDCG@10 on logged pairs, gated by a margin over the live model and an ONNX parity check | Covered |
| R-54 | Embeddings can live in a per-release table; a colour with a missing or incomplete release refuses to start; the default table is untouched | Covered |
| R-55 | Promotion is staged, resumable and dry-runnable; only `--promote` releases it; `prune` keeps the newest 3 releases | Covered (`tools/promote_model.sh` itself and a real deploy by hand) |
| R-56 | CI uses the self-hosted runner only for trusted events when it is online and idle, otherwise GitHub-hosted | Partly covered (selector tested; workflow never ran on GitHub) |

Counts: 56 requirements, 49 fully covered, 4 partly covered (R-23, R-25, R-46, R-56), 3 with no automated test
(R-32, R-43, R-44). See "Requirements with no test".

## Search

### R-01 Eight search methods exist and return results
- Statement: term overlap, TF-IDF, BM25, BM25 + TF-IDF hybrid, BM25 + PRF, cosine, semantic rerank
  and semantic RRF each answer a search.
- Source: README "Retrieval Architecture" and "Business requirements > Search"; CLAUDE "Architecture"
  (`scripts/`); HO 22.
- Code: `backend/services/retrieval.py` (`SYSTEMS` registry, `_term_overlap`, `_tfidf`, `_bm25`,
  `_hybrid`, `_bm25_prf`, `_cosine`, `_semantic_rerank`, `_semantic_rrf`, `run_search`);
  `backend/services/ranking.py` (`term_overlap`, `tf_idf`, `bm25`, `bm25_tfidf_hybrid`, `bm25_prf`,
  `cosine_search`, `semantic_rerank`, `bm25_dense_rrf`); `backend/scripts/search.py` (`rrf_fusion`);
  `backend/routers/search.py` (`make_search_router`).
- Tests: `tests/test_search_api.py::test_every_search_method_returns_results`,
  `tests/test_ranking.py::test_bm25`, `::test_tf_idf`, `::test_term_overlap`, `::test_hybrid`,
  `::test_prf`, `::test_hybrid_prf`, `::test_cosine_matches_numpy`,
  `::test_rerank_only_scores_candidates`, `::test_rrf_matches_legacy`,
  `tests/test_search.py::test_rrf_fusion_combines_lists`,
  `tests/test_mutation_killers.py::test_registry_slugs_and_requirements`,
  `tests/test_eval_pipeline.py::test_every_system_is_registered`.

### R-02 SQL ranking gives the same scores as the old in-memory code
- Statement: BM25, TF-IDF, overlap and PRF run in SQL and agree with the legacy code to 9 decimals.
- Source: HO 20 ("BM25 and TF-IDF run in SQL"); CLAUDE "Architecture" (`tests/_legacy_search.py` is the
  oracle).
- Code: `backend/services/ranking.py` (`bm25`, `tf_idf`, `_expansion_weights`, `_corpus_stats`);
  oracle `tests/_legacy_search.py`.
- Tests: `tests/test_ranking.py::test_bm25`, `::test_tf_idf`, `::test_term_overlap`, `::test_hybrid`,
  `::test_prf`, `::test_hybrid_prf`, `::test_index_rows_cover_every_hadith`,
  `tests/test_mutation_killers_ranking.py::test_hybrid_scores_are_a_weighted_sum_of_the_two_normalised_scores`,
  `::test_expansion_weights_start_from_the_query_terms`.

### R-03 Dense methods are Arabic only
- Statement: cosine, semantic rerank and semantic RRF reject English queries with 422.
- Source: README "Search"; HO 23; ARCH "Retrieval Data Flow".
- Code: `backend/services/retrieval.py` (`_DENSE`, `_system(..., languages=)`);
  `backend/services/ranking.py` (`dense_search`, `encode_query`); `backend/routers/search.py`.
- Tests: `tests/test_search_api.py::test_dense_methods_reject_english`,
  `tests/test_ranking.py::test_english_is_rejected`,
  `tests/test_mutation_killers_ranking.py::test_the_arabic_only_message_is_exact`.

### R-04 The same query always returns the same order
- Statement: ties (scores equal to 9 decimals) go to the lower hadith id; the hybrid result is sorted
  by score.
- Source: HO 20 ("Ties are now broken by hadith id", "hybrid now returns its results sorted").
- Code: `backend/services/ranking.py` (`_ranked`, `bm25_tfidf_hybrid`, `dense_search`).
- Tests: `tests/test_search_api.py::test_hybrid_results_come_back_ranked`,
  `tests/test_mutation_killers_ranking.py::test_dense_ties_go_to_the_lower_id_and_top_k_is_honoured`,
  `tests/test_ranking.py::test_limit_and_restrict`.

### R-05 Search takes filters and returns hadiths in rank order
- Statement: a query of 1 to 500 characters plus method, language, book and grade filters gives a
  ranked list; unknown ids are skipped; the cut to `top_k` happens after filtering.
- Source: README "Search"; CLAUDE `services/results.py`; HO 18 (search text capped at 500), HO 24.
- Code: `backend/services/results.py` (`build_results`, `_to_hadith`); `backend/models/schemas.py`
  (`SearchRequest`); `backend/routers/search.py` (`GET /searches`).
- Tests: `tests/test_search_api.py::test_bm25_result_shape_and_order`,
  `::test_book_and_grade_filters`, `::test_bad_search_requests_are_422`,
  `tests/test_services.py::test_build_results_filters_and_orders`,
  `::test_build_results_keeps_the_ranking_order`, `::test_build_results_skips_unknown_ids`,
  `::test_build_results_top_k`,
  `tests/test_mutation_killers.py::test_build_results_filters_before_the_cut`.

### R-09 `GET /api/v1/search-methods` lists enabled methods and languages
- Statement: the client can ask which methods the server offers and for which languages.
- Source: README "Search"; HO 22 follow-up, HO 23.
- Code: `backend/routers/search.py` (`/search-methods`); `backend/services/retrieval.py`
  (`enabled_systems`).
- Tests: `tests/test_search_api.py::test_search_methods_lists_links`,
  `::test_search_methods_list_languages`.

## REST API

### R-06 Resources under `/api/v1`, correct verbs and status codes
- Statement: every route is under `/api/v1`, named after a resource; 201 with `Location` on create, 401
  without a token, 403 for another person's data, 404, 409 on duplicate username, 422 on bad input;
  old URLs are gone.
- Source: HO 16; CLAUDE "REST conventions".
- Code: `backend/rest.py` (`API_PREFIX`, `href`, `link`); `backend/routers/` (`auth.py`,
  `annotation.py`, `kv_pairs.py`, `benchmark.py`, `search.py`, `hadiths.py`, `root.py`).
- Tests: `tests/test_routers.py::test_signup_creates_annotator_and_returns_token`,
  `::test_signup_validation_is_422`, `::test_signup_duplicate_username_is_409`,
  `::test_annotator_by_id_is_self_only`, `::test_missing_or_malformed_credentials_are_401`,
  `::test_hadith_resource`, `tests/test_search_api.py::test_post_to_searches_is_405`,
  `tests/test_rest.py::test_api_root_lists_only_enabled_resources`,
  `tests/test_app_factory.py::test_hadith_route`,
  `tests/test_mutation_killers_edges.py::test_href_strips_only_slashes_from_each_part`.

### R-07 Errors are `application/problem+json`
- Statement: `HTTPException` and validation errors are formatted as RFC 9457 bodies, keeping headers
  such as `WWW-Authenticate`.
- Source: HO 16; CLAUDE "REST conventions"; README "Deployment".
- Code: `backend/rest.py` (`problem_response`, `_http_exception_handler`, `_validation_handler`,
  `install_error_handlers`).
- Tests: `tests/test_rest.py::test_problem_response_shape`,
  `::test_http_errors_are_problem_json_and_keep_headers`, `::test_validation_errors_list_fields`,
  `tests/test_mutation_killers_edges.py::test_401_adds_a_www_authenticate_bearer_header_but_keeps_a_given_one`,
  `::test_validation_errors_name_nested_fields_and_messages`.

### R-08 Cacheable reads carry validators and links
- Statement: cacheable reads send `Cache-Control` and a content-hash `ETag`, answer a matching
  `If-None-Match` with 304, keep timing in `Server-Timing`, and carry `_links`; search and benchmark
  reads cache for 5 minutes.
- Source: HO 16; CLAUDE "REST conventions"; README "Search", "Benchmark".
- Code: `backend/rest.py` (`json_response`, `cache_control`, `_etag_matches`, `EncodedJson`,
  `server_timing`, `page_links`); `backend/routers/search.py` (`CACHE_SECONDS`);
  `backend/routers/root.py`.
- Tests: `tests/test_rest.py::test_json_response_sets_validators`,
  `::test_if_none_match_gives_304`, `::test_stale_etag_gets_full_body`, `::test_cache_control_values`,
  `::test_server_timing_format`, `::test_page_links`, `::test_href_and_link`,
  `tests/test_search_api.py::test_search_is_cacheable_with_etag`, `::test_search_links`,
  `tests/test_routers.py::test_benchmark_results_reads_json_and_is_cacheable`,
  `tests/test_mutation_killers_edges.py::test_etag_matching`,
  `::test_json_response_bodies_headers_and_conditional_get`,
  `::test_page_links_are_exact_in_the_middle_and_at_the_edge`.

## Auth and annotation

### R-10 JWT auth
- Statement: a signed token carries the annotator id and an expiry (12 hours by default,
  `AUTH_TOKEN_TTL_MINUTES`); HS256 only; forged, expired or malformed tokens read as no token; no
  server-side session.
- Source: HO 17; README "Accounts and auth"; CLAUDE `tokens.py`.
- Code: `backend/tokens.py` (`issue_token`, `read_token`, `auth_settings`, `AuthSettings`);
  `backend/routers/auth.py` (`create_token`, `get_current_annotator`).
- Tests: `tests/test_tokens.py::test_token_roundtrip_carries_id_and_name`,
  `::test_garbage_tokens_read_as_none`, `::test_other_secret_and_missing_claims_are_rejected`,
  `::test_none_algorithm_is_not_accepted`, `::test_settings_from_env`,
  `::test_default_ttl_and_random_secret_warns`, `::test_short_secret_is_refused`,
  `tests/test_routers.py::test_create_token_and_read_profile`,
  `::test_forged_and_expired_tokens_are_rejected`,
  `tests/test_mutation_killers_auth.py::test_the_bearer_scheme_is_case_insensitive`,
  `::test_authorization_errors_explain_themselves`,
  `::test_a_bearer_value_with_a_space_is_an_invalid_token_not_a_missing_header`.

### R-11 Password rules and hashing
- Statement: passwords are 8 to 128 characters, usernames 3 to 64; hashes are salted PBKDF2-SHA256 at
  600,000 rounds, and old 100,000-round rows still verify.
- Source: HO 18; README "Accounts and auth".
- Code: `backend/routers/auth.py` (`hash_password`, `verify_password`, `_split_salt`, `Credentials`,
  `NewAnnotator`).
- Tests: `tests/test_routers.py::test_password_length_limits`, `::test_hash_password_roundtrip`,
  `::test_password_hashes_use_the_current_cost_and_verify_legacy_rows`,
  `tests/test_mutation_killers_auth.py::test_a_new_salt_carries_its_iteration_count_and_a_random_part`,
  `::test_a_legacy_salt_without_a_count_uses_100000_iterations`,
  `::test_non_ascii_passwords_hash_as_utf8`, `::test_sign_up_length_limits`,
  `::test_sign_in_limits_and_messages`.

### R-12 Sign-in does not reveal which usernames exist
- Statement: an unknown username and a wrong password get the same answer and the same work.
- Source: HO 18; README "Accounts and auth".
- Code: `backend/routers/auth.py` (`create_token`, `verify_password`).
- Tests: `tests/test_routers.py::test_unknown_user_and_wrong_password_look_the_same`.
  Equal timing is not measured by any test (see Open questions).

### R-13 Annotator sign-up and query assignment
- Statement: sign-up returns a token and 2 queries; a query has at most 3 annotators; the least
  assigned queries go first; a lock stops two sign-ups pushing a query past 3; an annotator reads
  only their own profile.
- Source: README "Annotation and agreement" and "Accounts and auth"; CLAUDE `annotation.py` + `auth.py`.
- Code: `backend/routers/auth.py` (`create_annotator`, `auto_assign_queries`, `ANNOTATORS_PER_QUERY`,
  `QUERIES_PER_ANNOTATOR`, `get_annotator`, `get_current_annotator_resource`); `backend/database.py`
  (`ASSIGNMENT_LOCK`).
- Tests: `tests/test_routers.py::test_assignment_cap_per_query`, `::test_assignment_listing`,
  `::test_annotator_by_id_is_self_only`,
  `tests/test_mutation_killers_auth.py::test_queries_go_to_the_least_assigned_first_and_stop_at_three_annotators`,
  `::test_sign_up_answer_in_full`, `::test_profile_bodies_and_the_self_only_rule`,
  `::test_the_profile_is_private_and_revalidated`,
  `tests/test_acid.py::test_concurrent_signups_never_exceed_annotators_per_query`,
  `::test_one_assignment_per_annotator_and_query`.

### R-14 Labels and progress
- Statement: a label is 0, 1 or 2; one label per annotator, hadith and query; sending it again
  replaces it (201 first, 200 after); only assigned queries and pooled hadiths are visible (else 404);
  progress is saved and must fall inside the pool.
- Source: README "Annotation and agreement"; HO 16.
- Code: `backend/routers/annotation.py` (`put_label`, `_upsert_label`, `put_progress`, `get_assignment`,
  `list_assignments`, `_require_query`, `_clamp_index`, `LabelPayload`, `ProgressPayload`).
- Tests: `tests/test_routers.py::test_put_label_is_idempotent_and_reports_created_vs_replaced`,
  `::test_put_label_rejects_invalid_label_and_foreign_hadith`, `::test_put_progress`,
  `::test_assignment_detail_includes_hadith_text_and_links`,
  `::test_unassigned_or_unknown_assignment_is_404`, `::test_assignments_need_a_token`,
  `tests/test_mutation_killers_routers.py::test_clamp_index`,
  `::test_labels_and_progress_are_scoped_to_annotator_and_query`,
  `::test_unassigned_query_404_names_the_problem`,
  `::test_a_stored_progress_past_the_pool_is_shown_as_the_last_item`,
  `::test_assignment_links_are_exact`,
  `tests/test_acid.py::test_concurrent_first_labels_report_one_creation`.

### R-15 Inter-annotator agreement
- Statement: per query, over hadiths every annotator labelled: Cohen's kappa, Spearman and raw
  agreement; needs 2 annotators and 2 common labels; the overall summary averages across queries.
- Source: README "Annotation and agreement"; CLAUDE `services/agreement.py`.
- Code: `backend/services/agreement.py`; `backend/routers/annotation.py` (`get_agreement`,
  `_labels_by_annotator`).
- Tests: `tests/test_services.py::test_summarize_needs_two_annotators`,
  `::test_summarize_needs_two_common_labels`, `::test_summarize_perfect_agreement`,
  `::test_summarize_partial_agreement`, `::test_overall_summary`, `::test_agreement_endpoint`,
  `tests/test_routers.py::test_agreement_shape`,
  `tests/test_mutation_killers.py::test_raw_agreement_is_per_document`,
  `::test_spearman_is_rho_not_p`, `::test_summarize_query_needs_pooled_docs_labelled_by_all`,
  `tests/test_mutation_killers_routers.py::test_agreement_uses_only_the_labels_of_the_same_query`.

### R-16 Benchmark endpoints
- Statement: stored evaluation output is published; a missing file is 404 with a hint; the `mode`
  parameter cannot reach outside the data directory; results are public and cacheable.
- Source: README "Benchmark"; HO 16; CLAUDE `benchmark.py`.
- Code: `backend/routers/benchmark.py` (`benchmark_index`, `benchmark_results`, `benchmark_stats`,
  `benchmark_qrels`, `finetuned_results`, `finetuned_stats`, `comparison_results`, `load_json`).
- Tests: `tests/test_routers.py::test_benchmark_missing_file_is_404`,
  `::test_benchmark_results_reads_json_and_is_cacheable`,
  `::test_benchmark_mode_cannot_escape_the_data_dir`, `::test_benchmark_index_links`,
  `tests/test_mutation_killers_routers.py::test_benchmark_files_missing_answer_404_with_a_message`,
  `::test_benchmark_qrels_lists_every_query_with_empty_grades`,
  `::test_load_json_returns_an_empty_dict_for_a_missing_file`,
  `::test_load_json_reads_utf8_whatever_the_locale`.

### R-17 KV pairs
- Statement: all `/api/v1/kv-pairs` routes need a token; list by status or topic with paging up to
  200; counts by status and topic; mark a pair verified or rejected singly or in a batch; batches skip
  unknown ids.
- Source: README "KV pairs"; HO 16, HO 18.
- Code: `backend/routers/kv_pairs.py` (`list_kv_pairs`, `kv_pairs_statistics`, `update_kv_pair`,
  `update_kv_pairs`, `MAX_PAGE_SIZE`, `StatusUpdate`, `BatchItem`).
- Tests: `tests/test_routers.py::test_kv_pairs_need_a_token`, `::test_kv_list_filters_and_paginates`,
  `::test_kv_list_rejects_bad_paging`, `::test_kv_statistics`, `::test_kv_patch_single`,
  `::test_kv_patch_batch_then_filter_verified`,
  `tests/test_acid.py::test_overlapping_kv_batches_do_not_deadlock`.

### R-18 A KV pair shows the hadith text through `hadith_id`
- Statement: `GET /api/v1/kv-pairs` joins `hadiths`; the English and Arabic text come from the one
  hadith the pair points at.
- Source: HO 28 ("kv_pairs" row and "Behaviour changes").
- Code: `backend/routers/kv_pairs.py` (`_pair_dict`, `list_kv_pairs`); `backend/models/orm.py`
  (`KvPair`).
- Tests: `tests/test_mutation_killers_routers.py::test_kv_pair_shape_and_joined_hadith_text`,
  `tests/test_normalization.py::test_kv_pairs_no_longer_copy_the_hadith_text`,
  `tests/test_normalization.py::test_embeddings_hold_only_the_arabic_vector`,
  `::test_a_kv_pair_needs_an_existing_hadith`.

## Settings, features and lifecycle

### R-19 One Settings class, read once
- Statement: `DATABASE_URL`, `AUTH_SECRET`, TTL, CORS, static dir, model dir and encoder threads come
  from one pydantic `Settings`; `get_settings()` is cached; secrets stay out of `repr`.
- Source: HO 25; CLAUDE "Architecture" (`settings.py`).
- Code: `backend/settings.py` (`Settings`, `get_settings`); `backend/database.py` (`database_url`);
  `backend/tokens.py` (`auth_settings`).
- Tests: `tests/test_settings.py::test_defaults_are_dev`, `::test_reads_the_environment`,
  `::test_secret_and_url_do_not_show_in_repr`, `::test_get_settings_is_built_once`,
  `tests/test_tokens.py::test_settings_from_env`,
  `tests/test_mutation_killers_routers.py::test_database_url_message_shows_an_example`.

### R-20 `APP_ENV` dev, test, prod
- Statement: `dev` is the default; `prod` refuses to start without `AUTH_SECRET` (32+ characters) and
  `CORS_ORIGINS` and hides `/docs`, `/redoc`, `/openapi.json`; `test` behaves like dev; an unknown
  value is refused.
- Source: HO 25; CLAUDE `settings.py`; README "Accounts and auth".
- Code: `backend/settings.py` (`Environment`, `Settings`); `backend/main.py` (`_docs_kwargs`,
  `create_app`).
- Tests: `tests/test_settings.py::test_unknown_environment_is_refused`,
  `::test_prod_needs_a_secret_and_explicit_cors`, `::test_prod_with_everything_set_is_valid`,
  `::test_prod_still_checks_secret_length`, `::test_prod_hides_docs_and_schema`,
  `::test_dev_keeps_docs_and_schema`, `::test_test_environment_behaves_like_dev`.

### R-21 Feature flags and `APP_MODE` presets
- Statement: `FEATURE_<NAME>` beats the `APP_MODE` preset, which beats the default; the
  `annotation` preset loads no search stack; routers are included per flag; the dense flag gates the
  dense methods.
- Source: CLAUDE `features.py`, `main.py`; HO 7; README "Search".
- Code: `backend/features.py` (`Features`, `load_features`, `_parse_bool`); `backend/main.py`
  (`_include_feature_routers`, `create_app`); `backend/startup.py` (`preload_resources`).
- Tests: `tests/test_features.py::test_annotation_preset_disables_search_stack`,
  `::test_research_preset_loads_model_eagerly`, `::test_explicit_flag_beats_preset`,
  `::test_bool_parsing`, `::test_invalid_flag_value_raises`, `::test_is_enabled`,
  `tests/test_app_factory.py::test_annotation_mode_has_no_search_or_benchmark_routes`,
  `::test_router_groups_toggle_independently`, `::test_dense_flag_gates_endpoints`,
  `tests/test_startup.py::test_preload_skipped_when_search_disabled`,
  `::test_model_steps_need_dense_and_eager`,
  `tests/test_mutation_killers.py::test_app_mode_default_and_explicit`.

### R-22 DB lifespan
- Statement: startup creates the schema, and if the database is unreachable stops with a message that
  names `DATABASE_URL`; shutdown always calls `dispose_engines()`, even if startup or the app raised.
- Source: HO 25 "Database lifecycle"; CLAUDE "Architecture" (`startup.py`).
- Code: `backend/main.py` (`create_app` lifespan); `backend/startup.py` (`init_database`,
  `preload_resources`, `check_index`); `backend/database.py` (`init_schema`, `init_schema_sync`,
  `dispose_engines`).
- Tests: `tests/test_app_factory.py::test_lifespan_initialises_db_and_preloads`,
  `::test_lifespan_disposes_engines_on_shutdown`,
  `::test_lifespan_disposes_engines_and_explains_when_db_is_unreachable`,
  `::test_lifespan_disposes_engines_when_the_app_errors`, `::test_lifespan_reports_static_dir`,
  `tests/test_startup.py::test_init_database_creates_tables`,
  `::test_index_step_warns_when_the_index_is_empty`,
  `tests/test_acid.py::test_schema_initialisers_can_run_together`.

### R-45 The app serves the built frontend with a fallback route
- Statement: the SPA is served from `STATIC_DIR`, `../static` or `../frontend/dist`; unknown paths
  fall back to the SPA; the catch-all is registered last.
- Source: CLAUDE `main.py`.
- Code: `backend/main.py` (`resolve_static_dir`, `_mount_frontend`).
- Tests: `tests/test_app_factory.py::test_resolve_static_dir_prefers_env`, `::test_spa_fallback`.

## Errors and load control

### R-23 Users never see status codes or technical error text
- Statement: `errorKey()` turns any failure into a friendly Arabic message (no connection, invalid
  input, session expired, not allowed, not found, conflict, too many attempts, busy, generic); the
  number and the server's `title` and `detail` are never shown.
- Source: HO 26; HO 27 (frontend shows an Arabic busy message).
- Code: `frontend/src/api/errors.ts` (`errorKey`, `statusToErrorKey`, `ensureOk`, `ApiError`);
  `frontend/src/i18n/translations/ar.ts`; `frontend/src/components/ErrorBanner.tsx`.
- Tests: the frontend has no test runner, but `tests/bdd/test_friendly_errors.py` (scenario outline
  "A failure becomes an Arabic message") bundles the real `errors.ts` with the frontend's esbuild, runs
  it in node, and checks each status maps to the key and that `ar.ts` holds the Arabic message for
  that key. What the page then renders (ErrorBanner, the sign-in 401 message, the busy message) is
  tagged `@manual` and skipped. The server side of the wording has tests
  (R-22 unreachable database, R-24 and R-25 busy bodies, `tests/test_mutation_killers_auth.py::test_a_taken_username_is_409_with_a_message`),
  but those do not prove what the UI shows.

### R-24 Search queue in the app
- Statement: at most 8 searches run at once; up to 64 more wait first-in-first-out for at most 5
  seconds; beyond that 503 with `Retry-After: 5` as problem+json; sizes come from
  `SEARCH_MAX_CONCURRENT`, `SEARCH_QUEUE_SIZE`, `SEARCH_QUEUE_TIMEOUT_SECONDS`; a slot is freed when the
  work raises.
- Source: HO 27; README "Load control"; CLAUDE `limiter.py`.
- Code: `backend/limiter.py` (`SearchLimiter`, `Overloaded`, `search_slot`); `backend/settings.py`
  (`DEFAULT_SEARCH_RUNNING`, `DEFAULT_SEARCH_WAITING`, `DEFAULT_SEARCH_TIMEOUT_SECONDS`);
  `backend/main.py` (`app.state.search_limiter`); `backend/routers/search.py`
  (`dependencies=[Depends(search_slot)]`).
- Tests: `tests/test_limiter.py::test_runs_up_to_the_limit_and_queues_the_rest_in_order`,
  `::test_a_full_queue_refuses_at_once`,
  `::test_a_waiter_gives_up_after_the_timeout_and_frees_its_place`,
  `::test_a_slot_is_released_when_the_work_raises`,
  `::test_route_answers_503_with_retry_after_when_the_queue_is_full`,
  `::test_no_limiter_means_no_limit`, `::test_the_search_route_uses_the_limiter`,
  `::test_create_app_builds_the_limiter_from_settings`,
  `tests/test_mutation_killers_edges.py::test_overload_reasons_are_named`,
  `::test_the_503_body_header_and_log_line`.

### R-25 nginx smoothing for search and the sign-in limit
- Statement: per client address, search allows 10 a second, a burst of 20 and at most 8 open
  connections (503 beyond); sign-in and sign-up allow 5 tries then 1 a minute (429 with
  `Retry-After`); both answers are problem+json; uvicorn alone has none of this.
- Source: HO 19, HO 27; README "Load control".
- Code: `nginx/default.conf` (`limit_req_zone`, `limit_conn_zone`, `error_page`), `nginx/proxy_app.conf`.
- Tests: no pytest test. `tools/test-nginx.sh` (run in `.github/workflows/ci.yml`) starts
  nginx with a stub app in Docker and checks the limit, the JSON 429 and the body cap. HO 27 says the
  search part was tested against a stub only. `tests/test_limiter.py` covers the app queue, not nginx.

## Database

### R-26 Foreign keys enforced; corpus rebuild is one transaction
- Statement: links between tables are declared and enforced, with cascades; rebuilding the corpus drops
  with `CASCADE` and restores the `annotations` and `kv_pairs` references in the same transaction, so a
  failed rebuild leaves the old corpus.
- Source: HO "Read this first" 3; HO 20; HO 28 ("Behaviour changes"); ARCH "Alignment Invariant".
- Code: `backend/models/orm.py`; `backend/database.py` (`drop_corpus_tables`,
  `restore_hadith_references`, `insert_hadith_rows`); `backend/scripts/data_creation.py`
  (`create_database`).
- Tests: `tests/test_acid.py::test_foreign_keys_are_enforced`,
  `::test_deleting_an_annotator_cascades`, `::test_corpus_rebuild_failure_restores_the_old_corpus`,
  `::test_username_is_unique`, `::test_same_username_race_gives_one_account_and_one_409`,
  `tests/test_normalization.py::test_an_annotation_needs_an_existing_hadith`,
  `::test_rebuilding_the_corpus_keeps_the_references_checked`,
  `tests/test_mutation_killers_routers.py::test_drop_corpus_tables_inside_a_caller_transaction_can_roll_back`.
  The other `tests/test_acid.py` tests are listed under "Tests that map to no requirement".

### R-27 Third normal form
- Statement: chapter and book facts live in `books` and `chapters`; the six `Preprocessed_*` texts live in
  `hadith_preprocessed`; duplicate same-row columns are gone; `kv_pairs` and `annotations` point at
  `hadiths.id`; sections stay on `hadiths` as a documented exception.
- Source: HO 28; ARCH "Database Schema"; CLAUDE "Data"; CD 6.
- Code: `backend/models/orm.py` (`Book`, `Chapter`, `Hadith`, `HadithPreprocessed`, `KvPair`,
  `Annotation`); `backend/database.py` (`insert_hadith_rows`, `read_hadiths_df`, `get_hadith_row`).
- Tests: `tests/test_normalization.py::test_repeated_columns_are_gone_from_hadiths`,
  `::test_kv_pairs_no_longer_copy_the_hadith_text`,
  `::test_the_split_out_facts_live_in_their_own_tables`,
  `::test_foreign_keys_tie_every_fact_to_its_owner`, `::test_sections_stay_on_hadiths`,
  `::test_database_has_the_new_tables_and_columns`,
  `::test_chapter_titles_are_stored_once_per_chapter`,
  `::test_two_titles_for_one_chapter_are_refused`, `::test_a_hadith_needs_its_chapter`,
  `::test_an_annotation_needs_an_existing_hadith`, `::test_a_kv_pair_needs_an_existing_hadith`,
  `::test_rebuilding_the_corpus_keeps_the_references_checked`,
  `tests/test_database.py::test_get_hadith_row_joins_chapter_and_keeps_the_old_names`,
  `tests/test_mutation_killers_routers.py::test_books_and_the_flat_view_round_trip`,
  `::test_two_titles_error_names_the_chapter`.
  The step 1 and step 2 migration SQL in HO 28 was run on a scratch copy only and has no test.

### R-46 Blue/green rollback
- Statement: `promote` checks the new colour after the switch (nginx serves it, `GET /api/v1/health`
  and one search answer, health stays good) and switches back by itself, exit 1, if not; `rollback` flips
  back through `nginx -t` and a reload, and refuses if the previous colour is not running, healthy
  and answering; `--dry-run` changes nothing; releases carry an id (git sha) and image tags are not
  overwritten; the database is not rolled back and a warning is printed when the owner marks the
  destructive schema step as applied.
- Source: HO 35; HO 21; HO 28 (step 2/2b); README "Deploying".
- Code: `tools/deploy.sh` (`cmd_promote`, `auto_rollback`, `cmd_rollback`, `smoke`, `warn_schema`).
- Tests: `tools/test-rollback.sh` (stub apps and a real nginx, run by hand, not in CI; outside
  pytest); `tests/test_deploy_script.py::test_promote_then_rollback` and the smoke tests
  (`test_smoke_uses_the_health_route_and_refuses_a_bad_answer`,
  `test_smoke_refuses_a_search_that_errors`, `test_smoke_search_busy_is_only_a_warning`,
  `test_smoke_skips_search_when_the_app_has_none`) run in pytest against a fake docker. Not run
  against the real app image.

### R-50 Health route
- Statement: `GET /api/v1/health` answers 200 `{"status":"ok"}` when the app can reach its database
  and 503 `application/problem+json` when it cannot or takes over 3 seconds; it needs no token, uses no
  search-queue slot, is never cached, leaks no database address, exists in every `APP_MODE`, and is
  linked from the API root. The Docker `HEALTHCHECK` and `tools/deploy.sh` use it.
- Source: HO 35 (health route and smoke check); HO 21.
- Code: `backend/routers/root.py` (`health`), `Dockerfile` (`HEALTHCHECK`), `tools/deploy.sh` (`smoke`).
- Tests: `tests/test_health.py::test_health_is_ok_without_a_token_and_never_cached`,
  `::test_health_says_503_when_the_database_is_down`, `::test_health_gives_up_when_the_database_hangs`,
  `::test_health_exists_in_every_mode`, `::test_health_is_listed_on_the_api_root`;
  `tests/bdd/test_health.py` (`docs/behaviours/health.feature`).

### R-51 Image pruning
- Statement: `tools/deploy.sh prune-images [--keep N] [--dry-run]` removes old release images of the
  `hadith-search` repository and keeps the newest N (default 3); it never removes an image a running
  container uses or `deploy/state/releases.env` names, never uses `-f` or `docker image prune`, and
  leaves `:blue`, `:green`, `:latest` and other repositories alone. Automatic after promote only with
  `PRUNE_AFTER_PROMOTE=1`.
- Source: HO 35 (image pruning).
- Code: `tools/deploy.sh` (`cmd_prune_images`).
- Tests: `tests/test_deploy_script.py::test_prune_keeps_the_newest_three_release_images`,
  `::test_prune_dry_run_changes_nothing`, `::test_prune_protects_running_and_recorded_images`,
  `::test_prune_does_nothing_with_too_few_images`, `::test_prune_keep_must_be_a_positive_number`,
  `::test_prune_never_uses_force_or_image_prune`, `::test_prune_after_promote_is_off_unless_asked`;
  `tools/test-rollback.sh` (real docker images in a throwaway repository).

### R-52 Dead code check
- Statement: `tools/dead-code.sh check` runs vulture with the `[tool.vulture]` settings of
  `pyproject.toml`, groups findings by file, and exits 1 on any finding and 0 when clean; `report`
  exits 0 and writes a Markdown report outside the repo by default; `whitelist` prints a candidate
  and writes no file; `--min-confidence N` overrides the config; nothing is ever installed. The
  pre-commit vulture hook calls the script.
- Source: HO 39; CLAUDE "Code-quality tooling".
- Code: `tools/dead-code.sh`, `.pre-commit-config.yaml` (`vulture` hook).
- Tests: `tools/test-dead-code.sh` (throwaway project; run by hand, not in pytest or CI).

### R-53 Best model version is picked by similarity on logged pairs
- Statement: every READY version of a registered MLflow model is scored by mean nDCG@10 of the
  64-d cosine ranking on a labelled pairs file stored in MLflow; the live ONNX model is scored on
  the same pairs. The best version must beat it by `--margin` (0.01), and its ONNX export must
  match the PyTorch vectors (cosine 0.9999) or the run stops before any database write.
- Source: HO 40.
- Code: `backend/scripts/promote_model.py` (`ndcg_at_k`, `score_pairs`, `decide`, `select_best`),
  `backend/scripts/export_onnx.py` (`export(source_dir=...)`).
- Tests: `tests/mlops/test_scoring.py`, `tests/mlops/test_promote.py` (selection, margin pass and
  fail, missing and differing pairs, parity failure), `tests/mlops/test_export_local.py`.

### R-54 Per-release embedding tables
- Statement: `EMBEDDINGS_RELEASE` makes search read `hadith_embeddings_<release>`; unset keeps
  `hadith_embeddings`. A colour whose release table is missing or has fewer vectors than hadiths
  fails at startup, so the health check keeps traffic off it. A corpus rebuild drops release tables.
- Source: HO 40 (storage design).
- Code: `backend/models/embedding_sets.py`, `backend/services/ranking.py`, `backend/startup.py`
  (`check_embeddings_release`), `backend/database.py`, `backend/settings.py`.
- Tests: `tests/test_embedding_releases.py`.

### R-55 Staged, resumable promotion
- Statement: `promote_model.sh run` exports, re-embeds into a new table and stages the settings
  without changing production; `--dry-run` writes nothing; finished steps are skipped on a re-run;
  only `--promote` runs `deploy.sh deploy` and `promote`; `prune` keeps the newest 3 releases and
  any named in `model.env`, `model.env.prev` or `model.staged.env`.
- Source: HO 40.
- Code: `tools/promote_model.sh`, `backend/scripts/promote_model.py`, `tools/deploy.sh`
  (`model.env`), `docker-compose.yml`.
- Tests: `tests/mlops/test_promote.py` (dry run, full run, resume, prune),
  `tests/test_deploy_script.py::test_deploy_reads_the_staged_model_settings`. The wrapper's
  `--promote` branch has no test.

### R-56 CI runner selection
- Statement: `ci.yml` picks the runner in its first job. Self-hosted (label `hadith-ci`) only for
  push, schedule, manual runs and same-repository pull requests, and only when the GitHub API
  shows it online and not busy; fork pull requests, a missing token, an API error or a busy or
  offline runner mean `ubuntu-latest`. Jobs: lint, frontend, test, docker-scan.
- Source: HO 41.
- Code: `.github/workflows/ci.yml`, `tools/pick-runner.sh`, `tools/frontend-lint.sh`,
  `tools/runner/`.
- Tests: `tests/test_pick_runner.py` (fake runners API: idle, busy, offline, no runner, fork,
  untrusted event, missing token, HTTP errors, unreadable answer, unreachable API). The workflow
  itself was checked with actionlint only; no run happened on GitHub or on the self-hosted runner.

### R-28 Schema changes are additive
- Statement: blue and green share one database; the app only adds tables and columns, so starting
  an older release must not remove what a newer one added.
- Source: HO 21 ("The schema rule"); HO "Read this first" 3; README "Deployment".
- Code: `backend/database.py` (`init_schema`, `init_schema_sync`, `create_all`).
- Tests: `tests/test_database.py::test_init_schema_keeps_extra_columns_and_tables`,
  `tests/test_acid.py::test_schema_initialisers_can_run_together`.

### R-29 The DB layer uses the ORM only
- Statement: no raw SQL strings in `backend/` (SQLAlchemy 2.0 ORM).
- Source: HO 1; CLAUDE "Tests" (DB layer paragraph).
- Code: `backend/database.py`, `backend/models/orm.py`, all of `backend/`. One exception is the
  advisory-lock call built with `func.pg_advisory_xact_lock` in `backend/routers/auth.py`, which is
  still SQLAlchemy.
- Tests: `tests/test_database.py::test_no_raw_sql_in_backend`.

## Security and deploy

### R-30 Test isolation
- Statement: every DB test gets its own schema; its connections are tagged; leftover open
  transactions are rolled back and their locks freed before the schema is dropped; other tests running in
  parallel are not touched; settings are cleared around each test and the repo `.env` is ignored.
- Source: HO 26 "Test isolation"; HO 4; CLAUDE "Tests".
- Code: `tests/conftest.py` (`_pg_ready`, `_pg_schema`, `_rollback_open_transactions`,
  `_hermetic_settings`, `_patched_paths`).
- Tests: `tests/test_db_isolation.py::test_open_transaction_is_rolled_back_and_its_lock_released`,
  `::test_other_connections_are_left_alone`, `::test_each_test_gets_its_own_empty_schema`,
  `tests/test_settings.py::test_get_settings_is_built_once`.

### R-31 Blue/green and canary releases
- Statement: `tools/deploy.sh` supports `init`, `status`, `deploy`, `canary N`, `promote`, `rollback`,
  `stop-idle`; traffic moves only to a healthy colour; a failed deploy keeps live traffic; a bad nginx
  config is rolled back; a canary that turns unhealthy falls back by itself.
- Source: HO 21; README "Deploying" and "Deployment".
- Code: `tools/deploy.sh`; `nginx/default.conf`; `docker-compose.yml`; `deploy/state/`.
- Tests: `tests/test_deploy_script.py::test_init_makes_blue_live`, `::test_commands_need_init`,
  `::test_canary_sends_a_share_to_the_idle_colour`, `::test_canary_percent_must_be_1_to_99`,
  `::test_nothing_moves_to_an_unhealthy_colour`,
  `::test_canary_falls_back_when_the_colour_fails_while_watched`, `::test_promote_then_rollback`,
  `::test_rollback_during_a_canary_only_stops_the_canary`,
  `::test_rollback_needs_something_to_go_back_to`, `::test_deploy_is_refused_during_a_canary`,
  `::test_failed_deploy_stops_the_new_colour_and_keeps_traffic`,
  `::test_nginx_rejecting_the_routing_keeps_the_old_file`, `::test_stop_idle_clears_previous`,
  `::test_deploy_passes_the_image_to_the_idle_colour`, `::test_status_reports_both_colours`.
  These run against a fake `docker`; HO 21 says it was not run on a real host.

### R-32 Image, Dockerfile and secret scanning
- Statement: trivy (fixable HIGH or CRITICAL fail) and dockle (WARN and above fail) scan the image on
  pushes, PRs and weekly; hadolint checks the Dockerfile; gitleaks runs on every commit; the image runs
  as uid 10001.
- Source: CLAUDE "Notes", "Pre-commit hooks"; HO 11, 12, 13, 14, 29.
- Code: `tools/scan-image.sh`; `.github/workflows/ci.yml`; `.pre-commit-config.yaml`;
  `.hadolint.yaml`; `Dockerfile`.
- Tests: none. These are CI and hook checks. Nothing in `tests/` asserts them, and HO 29 says the
  image was not rebuilt or rescanned locally.

### R-33 Security hardening
- Statement: static files cannot escape the static dir; every response carries CSP,
  `X-Frame-Options: DENY`, `nosniff` and related headers (`/docs` keeps working); CORS allows only needed
  methods and headers with credentials off; input has size limits; kv-pairs need a login.
- Source: HO 18; HO 25 (CORS); CLAUDE `main.py`.
- Code: `backend/main.py` (`add_security_headers`, `SECURITY_HEADERS`, `CONTENT_SECURITY_POLICY`,
  `cors_settings`, `_mount_frontend`).
- Tests: `tests/test_app_factory.py::test_static_files_cannot_escape_the_static_dir`,
  `::test_security_headers_are_set_and_docs_keep_working`, `::test_cors_settings`,
  `tests/test_routers.py::test_kv_pairs_need_a_token`, `::test_password_length_limits`,
  `tests/test_search_api.py::test_bad_search_requests_are_422`.

## Corpus and build pipeline

### R-34 Corpus build
- Statement: missing text, isnad or matn is rebuilt deterministically; rows without both English and
  Arabic matn are dropped (first stage) with an audit file; grades are normalised to Sahih, Hasan, Da'if,
  Mawdu, Unknown.
- Source: CD 2, 3, 5; ARCH "Build Pipeline Data Flow".
- Code: `backend/scripts/data_creation.py` (`apply_deterministic_reconstruction`,
  `drop_rows_missing_bilingual_matn`, `normalize_grade`, `create_database`); `backend/scripts/profile.py`.
- Tests: `tests/test_data_creation.py::test_text_rebuilt_from_isnad_and_matn`,
  `::test_matn_is_full_minus_isnad`, `::test_isnad_is_full_minus_matn`,
  `::test_chain_of_reconstructions_uses_new_text`,
  `::test_nothing_to_reconstruct_leaves_row_untouched`,
  `::test_prefix_that_does_not_match_is_not_removed`, `::test_drop_rows_missing_bilingual_matn`,
  `::test_has_text`, `::test_summary_keys_order_and_count`,
  `::test_number_labels_keep_ranges_and_drop_float_suffix`,
  `tests/test_profile.py::test_grade_flags`, `::test_unknown_grade_reason`,
  `tests/test_database.py::test_create_database_roundtrip`.
  No test calls `normalize_grade` directly (see Open questions).

### R-35 Preprocessing and the second-stage drop
- Statement: text, isnad and matn are preprocessed separately in six columns; English with NLTK and
  Arabic with CAMeL Tools; rows whose preprocessed matn is empty are dropped from both languages and
  audited.
- Source: CD 4, 6; ARCH "Build Pipeline Data Flow"; README build table (step 3).
- Code: `backend/scripts/preprocess.py` (`preprocess_english`, `preprocess_arabic`,
  `normalize_arabic_text`, `_drop_empty_matn`, `_write_drop_audit`, `_delete_hadiths`, `run`).
- Tests: `tests/test_preprocess.py::test_normalize_arabic_text_unifies_letters`,
  `::test_normalize_arabic_strips_tatweel`, `::test_get_wordnet_pos`,
  `::test_remove_stopwords_english_filters_short_and_stop`,
  `::test_process_arabic_tokens_mocked_disambiguation`, `::test_preprocess_english_real`,
  `tests/test_preprocess_run.py::test_run_drops_rows_with_empty_matn`,
  `::test_drop_empty_matn_noop_when_nothing_empty`, `::test_write_drop_audit_records_counts`,
  `::test_load_drop_audit_default_and_existing`, `::test_build_rows_skips_dropped`,
  `::test_delete_hadiths_batches`, `::test_preprocess_column_dispatches_by_language`,
  `tests/test_database.py::test_preprocess_run_updates_columns`,
  `tests/test_mutation_killers_ranking.py::test_preprocessing_follows_the_language`.

### R-36 Index and embeddings use matn only
- Statement: BM25 postings are built over the preprocessed matn columns only; embeddings cover the
  Arabic matn cleaned with `encoding_text`, no prefix; index and embedding rows are keyed to a hadith
  and are replaced, not duplicated, on a rebuild.
- Source: CD 1; CLAUDE "Indexing vs. embedding text"; ARCH "Alignment Invariant".
- Code: `backend/scripts/build_inverted_index.py` (`build_index_rows`, `write_index`);
  `backend/scripts/build_embeddings.py` (`_passages`, `_encode_and_save`, `run`);
  `backend/scripts/embedding_store.py` (`store_embeddings`); `backend/scripts/arabic_encoder.py`
  (`encoding_text`).
- Tests: `tests/test_build_embeddings.py::test_passages_strip_diacritics_and_add_no_prefix`,
  `::test_passages_reject_missing_matn`, `::test_encode_and_save_stores_arabic_vectors`,
  `::test_rerunning_replaces_rather_than_duplicates`,
  `::test_encode_and_save_detects_count_mismatch`, `::test_load_corpus_rejects_missing_matn`,
  `::test_run_end_to_end_with_fake_model`,
  `tests/test_arabic_encoder.py::test_encoding_text_drops_diacritics_and_collapses_space`,
  `tests/test_ranking.py::test_index_rows_cover_every_hadith`,
  `tests/test_mutation_killers_ranking.py::test_store_embeddings_upserts_in_batches_and_checks_lengths`,
  `tests/test_acid.py::test_index_rewrite_failure_keeps_the_old_index`,
  `::test_embeddings_upsert_is_all_or_nothing`.
  No test shows that the index excludes the isnad (see Open questions).

### R-37 Arabic ONNX encoder
- Statement: queries run through ONNX Runtime with no torch; the model path is `ARABIC_MODEL_DIR` or
  `backend/data/onnx/arabic`, and a missing model says to run `export_onnx`; threads default to 1; the
  encoder and CAMeL model load once even when threads race.
- Source: HO 23, HO 24; CLAUDE "Overview", "Architecture" (`loading.py`); ARCH "Loading and Caching".
- Code: `backend/scripts/arabic_encoder.py` (`load_encoder`, `model_dir`, `serving_threads`,
  `OnnxEncoder`); `backend/scripts/loading.py` (`_load_once`, `get_model`, `get_mle`,
  `get_english_lemmatizer`); `backend/scripts/export_onnx.py`.
- Tests: `tests/test_arabic_encoder.py::test_missing_model_says_how_to_export`,
  `::test_model_dir_can_be_overridden`, `::test_empty_input_gives_empty_matrix`,
  `::test_real_model_vectors_are_unit_length_and_padding_independent`,
  `::test_serving_threads_default_and_override`,
  `::test_encoder_passes_the_thread_count_to_onnx_runtime`,
  `tests/test_loading.py::test_racing_threads_load_only_once`,
  `::test_cache_clear_makes_the_next_call_load_again`,
  `tests/test_mutation_killers_routers.py::test_the_search_context_gives_a_session_and_the_lazy_model_loader`.
  `test_real_model_vectors_are_unit_length_and_padding_independent` needs the exported model and may
  skip. No test runs `export_onnx.py` (HO 29 says the export was not rerun).

### R-38 Build pipeline orchestrator
- Statement: `build_all.py` runs the steps in order, asks before overwriting (or `--force`), stops on
  the first failure and writes `build_manifest.json`; `--skip-embeddings` skips embeddings and pooling.
- Source: README "Build Pipeline".
- Code: `backend/scripts/build_all.py` (`main`, `run_step`, `_make_steps`, `_output_exists`,
  `write_manifest`).
- Tests: `tests/test_database.py::test_build_all_checks`,
  `::test_build_all_index_and_embedding_checks`,
  `::test_build_all_checks_are_false_on_an_empty_database`. These cover the "output exists" checks
  only. Nothing tests `main`, `run_step`, `write_manifest` or the flags.

### R-39 One-time copy from the old SQLite install
- Statement: `migrate_to_postgres.py` copies the old tables, splits an old flat `hadiths` table,
  rebuilds the BM25 index, refuses to run into a used database, and reports a missing SQLite file.
- Source: HO 20 ("Moving an old install"); HO 28 (last bullet); CLAUDE "Architecture".
- Code: `backend/scripts/migrate_to_postgres.py`.
- Tests: `tests/test_migrate_to_postgres.py::test_copies_every_table`,
  `::test_old_flat_hadiths_are_split_into_the_new_tables`,
  `::test_embeddings_and_index_are_built`, `::test_new_rows_get_ids_past_the_copied_ones`,
  `::test_refuses_to_copy_into_a_used_database`, `::test_missing_sqlite_file`,
  `tests/test_acid.py::test_migration_failure_copies_nothing`.

## Evaluation

### R-40 Evaluation metrics and statistics
- Statement: pooling builds candidate pools, annotators and `llm_grader.py` produce qrels, and
  `evaluation.py`, `full_evaluation.py`, `stats_tests.py` compute metrics and significance; dense systems
  are scored on Arabic queries only.
- Source: CLAUDE "Evaluation flow"; HO 5, HO 22, HO 23; README "Benchmark".
- Code: `backend/scripts/evaluation.py`, `eval_pipeline.py`, `full_evaluation.py`, `stats_tests.py`,
  `pooling.py`, `llm_validation.py`, `finetune.py`, `finetune_eval.py`.
- Tests: every test in seven files, listed in full so none is hidden: `tests/test_evaluation.py::test_precision_recall_f1`, `::test_at_k_truncates`, `::test_average_precision_and_map`, `::test_jaccard`, `::test_reciprocal_rank`, `::test_dcg_and_ndcg`, `::test_ndcg_no_relevant_is_zero`, `::test_evaluate_query_columns`; `tests/test_eval_pipeline.py::test_system_block_shape`, `::test_system_block_uses_k_in_ndcg_name`, `::test_stamp_inserts_timestamp_before_extension`, `::test_load_eval_inputs_and_pool`, `::test_simulated_pipeline_rejects_unknown_type`, `::test_save_outputs_writes_and_archives`, `::test_print_best_systems`, `::test_bm25_candidates_are_restricted_to_the_eval_pool`, `::test_every_system_is_registered`; `tests/test_full_evaluation.py::test_load_results_missing_and_present`, `::test_main_stops_without_baseline`, `::test_main_stops_without_qrels`, `::test_main_baseline_only`, `::test_main_full_run`, `::test_mode_deltas_and_format`, `::test_count_comparisons`, `::test_archive_writes_json_and_text`; `tests/test_stats.py::test_paired_t_test_insufficient_data`, `::test_paired_t_test_detects_difference`, `::test_wilcoxon_notes`, `::test_wilcoxon_significant_and_failure`, `::test_test_result_nan_is_never_significant`, `::test_direction`, `::test_bootstrap_ci_degenerate_and_bounds`, `::test_pairwise_against_baseline`, `::test_pairwise_without_baseline_compares_all_pairs`, `::test_pairwise_skips_mismatched_lengths`, `::test_filter_graded_queries_recomputes_mean`, `::test_run_analysis_summary`, `::test_run_analysis_empty`, `::test_latex_table_bolds_best_and_marks_significance`, `::test_significance_marker`, `::test_latex_escapes_underscores`, `::test_cross_config_tests`, `::test_delta_table_and_averages`, `::test_delta_entry_zero_baseline`, `::test_comparison_and_delta_latex`, `::test_delta_latex_empty_when_no_modes`; `tests/test_llm_validation.py::test_extract_pairs_matches_only_common_docs`, `::test_kappa_and_spearman_edge_cases`, `::test_grade_distribution_formats_percentages`, `::test_per_query_stats`, `::test_per_query_constant_ratings_have_no_spearman`, `::test_interpret_kappa`, `::test_validate_report`, `::test_validate_reports_missing_overlap`, `::test_latex_validation_table`; `tests/test_snapshots.py::test_pairwise_snapshot`, `::test_pairwise_no_baseline_snapshot`, `::test_latex_table_snapshot`, `::test_cross_config_snapshot`, `::test_comparison_table_snapshot`, `::test_delta_snapshots`, `::test_run_analysis_snapshot`, `::test_filter_graded_snapshot`, `::test_two_sample_tests_snapshot`, `::test_bootstrap_snapshot`, `::test_llm_report_snapshot`, `::test_llm_latex_snapshot`, `::test_interpret_kappa_boundaries_snapshot`; `tests/test_finetune.py::test_mean_pool_ignores_padding`, `::test_mnrl_loss_prefers_matching_pairs`, `::test_pair_dataset_and_collate`, `::test_early_stopping`, `::test_query_helpers`, `::test_format_passage`, `::test_read_required_json_missing`, `::test_load_triplet_data`, `::test_load_triplet_data_requires_files`, `::test_load_kv_data`, `::test_load_kv_data_without_hadith_ids`, `::test_load_pairs_dispatch_and_error`, `::test_load_combined_concatenates`, `::test_make_loaders_split`, `::test_train_epoch_and_validate_run_on_fake_model`, `::test_fit_saves_best_and_stops_early`, `::test_save_history`.
  `pooling.py`, `llm_grader.py` and `kv_generator.py` have no test file.

### R-41 Recall proxy for the semantic methods
- Statement: two automatic tests (known item, chapter title) measure recall@3 and @8 of the three
  Arabic semantic methods.
- Source: HO 23 ("Recall@k of the new model").
- Code: `backend/scripts/recall_proxy.py`; `docs/recall_proxy.json`.
- Tests: `tests/test_recall_proxy.py::test_ranked_ids_sorts_by_score`,
  `::test_known_item_query_is_first_half_and_counts_copies`,
  `::test_chapter_queries_group_books_and_skip_blank_titles`,
  `::test_print_report_shows_recall_only`,
  `tests/test_mutation_killers_recall.py::test_evaluate_plain_and_capped_recall`,
  `::test_the_known_item_sample_is_seeded_and_limited`,
  `::test_run_builds_the_report_and_writes_it_as_utf8_json`.

## Other

### R-42 Package-level lazy exports
- Statement: code imports from the package (`from models import Hadith`, `from scripts import bm25`);
  `scripts` and `routers` resolve names lazily so importing them stays cheap and annotation mode never
  loads the search stack.
- Source: CLAUDE "Imports"; HO 10.
- Code: `backend/lazy_exports.py`; `backend/models/__init__.py`; `backend/routers/__init__.py`;
  `backend/scripts/__init__.py`.
- Tests: `tests/test_public_exports.py::test_every_name_in_all_resolves`,
  `::test_models_hadith_is_orm_and_schema_is_pydantic`,
  `::test_exports_are_the_same_objects_as_submodules`, `::test_unknown_names_raise_attribute_error`,
  `::test_submodule_imports_still_work`, `::test_import_scripts_and_routers_is_lazy`,
  `tests/test_mutation_killers.py::test_lazy_exports_install_and_getattr`,
  `::test_lazy_install_caches_resolved_value`.

### R-43 Frontend picker lists only methods the server offers
- Statement: both search pages fill the method picker from `/api/v1/search-methods`, show it before
  the first search, re-run on change, and fall back to `bm25-prf` for a method the server does not offer.
- Source: HO 22 (follow-up fix); HO 23 ("Method picker updates as soon as the search language changes").
- Code: `frontend/src/api/useSearchMethods.ts`; `AlgorithmSelect` component; `frontend/src/pages/`.
- Tests: none automated. HO 22 says it was checked once in headless Chromium by hand.
  R-09 covers the endpoint the picker reads.

### R-44 Load-test performance figures
- Statement: 20 users on a 2 vCPU, 4 GB container give about 14 requests a second with p95 0.36 s;
  100 users give about 35 rps with p95 1.9 s.
- Source: HO 24, HO 27.
- Code: `tools/loadtest/` (`locustfile.py`, `run.sh`); the settings in R-37 and R-24.
- Tests: none. These are manual measurements from one run each. `test_racing_threads_load_only_once`
  and the encoder thread tests (R-37) guard two causes of the fix, not the figures.

## Security tests (tests/security)

Source: HO 18, HO 19, HO 25, HO 27, HO 36. `pytest tests/security` runs the real `main.create_app`
(CORS, header middleware, error handlers, the search limiter, every router) over the per-test schema.
A test marked `xfail(strict=True)` is a finding: it states what should hold, fails today, and turns
into an error once the code is fixed, so the marker must then be removed. The reasons are listed in HO 36.

### R-47 Tokens, IDOR, sign-up and sign-in
- Statement: every protected route refuses a missing, malformed, expired, foreign-secret, `alg=none`,
  tampered or HS384 token; one annotator cannot read or write another's assignments, labels, progress
  or profile; sign-up and sign-in enforce their limits; the answers do not tell which names exist
  (sign-in); passwords are never in responses or logs and the stored hash is salted PBKDF2.
- Source: HO 17, HO 18.
- Code: `backend/routers/auth.py`, `backend/routers/annotation.py`, `backend/tokens.py`.
- Tests: `tests/security/test_auth.py`, `tests/security/test_accounts.py`, and the SQL-payload tests
  in `tests/security/test_injection.py`.

### R-48 Validation, error hygiene, injection
- Statement: hostile text in any query, path or body parameter is data, not SQL; NUL characters,
  ids above 2^31-1, an `offset` above 1,000,000 and a kv batch above 100 items are 422 (`backend/inputs.py`);
  odd numbers and types are 4xx `application/problem+json`; no stack trace, SQL or secret in an answer; prod has no
  `/docs`, `/redoc` or `/openapi.json`.
- Source: HO 18, HO 25, README (REST errors).
- Code: `backend/rest.py`, `backend/main.py`, `backend/routers/*`.
- Tests: `tests/security/test_injection.py`, `test_validation.py`, `test_error_hygiene.py`.

### R-49 Headers, CORS, settings, abuse, secrets
- Statement: the header set and CORS rules `backend/main.py` defines hold on every kind of answer; the
  nginx limits and body cap are in the config; prod refuses a short or missing `AUTH_SECRET`, a
  missing `CORS_ORIGINS` and `CORS_ORIGINS=*`, and `docker-compose.yml` starts the app with
  `APP_ENV=prod`; secrets stay out of repr and logs; the search queue answers 503 with
  `Retry-After`; no secret file or key is tracked.
- Source: HO 18, HO 19, HO 25, HO 27.
- Code: `backend/main.py`, `backend/settings.py`, `backend/limiter.py`, `nginx/default.conf`.
- Tests: `tests/security/test_headers_cors.py`, `test_settings.py`, `test_resource_abuse.py`,
  `test_secrets.py` (also runs the pre-commit gitleaks binary when it is installed).

## Behaviour tests (tests/bdd)

The Gherkin files in `docs/behaviours/` run with pytest-bdd (`pytest tests/bdd`, 86 scenarios and
examples: 81 pass, 5 are skipped on purpose). They use the same fixtures as the rest of `tests/`, so they need the
PostgreSQL test database too. Scenarios tagged `@nginx` (2) need the nginx image, and `tools/test-nginx.sh` covers
them against a stub app. Scenarios tagged `@manual` (3) need a browser. The skip reason is printed
by pytest. They add a second, readable check of requirements that already have unit tests; they do
not replace those tests.

| Feature file | Requirements | Runs | Skipped |
| --- | --- | --- | --- |
| `search.feature` | R-01, R-03, R-05, R-07, R-08, R-09, R-21 | 21 | none |
| `spike-queue.feature` | R-19, R-24, R-25 | 7 | `@nginx` 60-search burst, `@manual` busy message (R-23, R-25) |
| `auth.feature` | R-07, R-10, R-12, R-13 | 14 | `@nginx` sign-in limit (R-25), `@manual` friendly sign-in message (R-23) |
| `annotation.feature` | R-14, R-15 | 9 | none |
| `kv-pairs.feature` | R-17 | 11 | none |
| `friendly-errors.feature` | R-07, R-23 | 11 | `@manual` wrong password message (R-23) |
| `prod-mode.feature` | R-19, R-20 | 8 | none |

Steps for `friendly-errors.feature` need node and `frontend/node_modules` (`npm ci` in `frontend/`);
without them those scenarios skip with that reason.

## Requirements with no test

| Id | Requirement | Why there is none |
| --- | --- | --- |
| R-23 | No status codes or technical text in the UI (partly) | The status-to-message map runs in node (`tests/bdd`), but nothing renders the page; those scenarios are `@manual`. |
| R-32 | Image, Dockerfile and secret scanning | Enforced by CI and pre-commit hooks, not by `tests/`. |
| R-43 | Frontend method picker | Checked by hand once (HO 22). |
| R-44 | Load-test figures | Manual measurement (HO 24, HO 27). |
| R-46 | Rollback (partly) | The nginx switch and the automatic rollback after the switch are checked only by `tools/test-rollback.sh`, which is outside pytest and uses stub apps; the smoke check and state handling run in pytest. |
| R-25 | nginx limits (partly) | Only `tools/test-nginx.sh`, which is outside pytest and uses a stub app. |

Partial gaps inside requirements that have tests:

- R-12: no test measures equal timing for unknown user and wrong password.
- R-34: `normalize_grade` has no direct test; `drop_rows_missing_bilingual_matn` writes an audit file
  (`dropped_lk_rows.json`) that no test reads.
- R-36: nothing asserts the index excludes the isnad.
- R-37: `export_onnx.py` is run only on a tiny local model (`tests/mlops/test_export_local.py`), not on the real Fada model.
- R-38: `build_all.main`, `run_step`, `write_manifest` and `--force` are not tested.
- R-27: HO 28's two-step production migration SQL is not tested.
- R-40: `pooling.py`, `llm_grader.py` and `kv_generator.py` have no test file.
- R-31: the real `deploy.sh` with real containers has not been run (HO 21).
- No CI job runs the PostgreSQL tests (HO "Still open"), so no test above runs automatically on
  push.

## Tests that map to no requirement

Generated by listing every `def test_` in `tests/` (429 names; a parametrized test counts once) and
removing those cited above. 56 are left. Most are fine-grained tests of code that does belong to a
mapped requirement (message wording, default values, helper behaviour), but no doc states that detail,
so they are not attached to a requirement. Two groups are worth a look: the `tests/test_acid.py` pair
(Open question 1) and the `test_mutation_killers*` tests, which were written to kill mutants and were
not derived from a requirement.

- `tests/test_acid.py`: `test_invalid_values_are_rejected`, `test_signup_failure_after_insert_leaves_nothing`
- `tests/test_build_embeddings.py`: `test_has_text_and_clean_text`
- `tests/test_database.py`: `test_get_hadith_row`, `test_hadith_records_convert_nan_and_ints`, `test_read_hadiths_df_all_and_subset`
- `tests/test_mutation_killers.py`: `test_build_results_default_top_k`, `test_build_results_filters_skip_only_the_failing_row`, `test_build_results_skips_missing_but_keeps_going`, `test_features_defaults`, `test_features_error_message_lists_valid_values`, `test_finite_drops_none_and_nan`, `test_load_features_reads_os_environ`, `test_overall_summary_rounding_keys`, `test_pairwise_means_values`, `test_raw_agreement_uses_first_annotator_row`, `test_run_search_reports_counts`, `test_summarize_needs_more_than_min_common`, `test_summarize_query_entry_keys_and_types`, `test_summarize_query_too_few_returns_base_keys`, `test_to_hadith_defaults`, `test_to_hadith_maps_every_column`
- `tests/test_mutation_killers_auth.py`: `test_an_assignment_for_a_query_without_text_has_an_empty_query`
- `tests/test_mutation_killers_edges.py`: `test_empty_index_warning_text_has_no_newline`, `test_page_links_with_a_zero_limit_points_last_at_offset_zero`, `test_preload_messages_and_arguments_are_exact`, `test_run_step_prints_label_then_done_on_one_line`, `test_step_labels_are_exact`
- `tests/test_mutation_killers_ranking.py`: `test_corpus_stats_count_documents_and_average_the_language_length`, `test_every_ranking_stays_inside_its_language`, `test_restrict_and_limit_apply_to_every_lexical_ranking`
- `tests/test_mutation_killers_recall.py`: `test_chapter_queries_use_the_encoder_cleanup`, `test_evaluate_asks_for_arabic_queries`, `test_main_help_names_the_script`, `test_main_passes_its_options_to_run`, `test_print_report_counts_each_test_with_its_own_query_total`, `test_run_hands_every_system_a_live_context_and_reports_timings`, `test_the_known_item_sample_uses_the_given_seed`
- `tests/test_mutation_killers_routers.py`: `test_annotation_model_is_importable_for_the_label_checks`, `test_get_hadith_texts_joins_the_chapter_and_blanks_unknown_ids`, `test_hadith_text_entry_has_every_key_and_blanks_missing_values`, `test_now_iso_is_utc`, `test_sessions_keep_objects_usable_after_commit`, `test_the_flat_view_orders_by_id_and_honours_an_explicit_order`
- `tests/test_preprocess.py`: `test_has_text`
- `tests/test_preprocess_run.py`: `test_arabic_texts_fall_back_to_sequential`, `test_arabic_texts_parallel`, `test_empty_ids_and_drop_rows`, `test_english_texts_skip_empty`, `test_report_drops_prints_samples`
- `tests/test_profile.py`: `test_coverage_and_length_rows`
- `tests/test_ranking.py`: `test_empty_and_unknown_queries`, `test_restrict_limits_both_rankings`
- `tests/test_startup.py`: `test_index_step_is_quiet_when_the_index_is_built`, `test_preload_runs_every_step`, `test_run_step_prints_label`

## Open questions

1. ACID. No doc states "ACID" as a requirement. HO 28 only states the single-transaction rebuild and
   enforced foreign keys, which are mapped to R-26. `tests/test_acid.py` also asserts atomic index
   rewrite, all-or-nothing embedding upsert, sign-up rollback, a migration that copies nothing on
   failure, constraint checks and four concurrency cases. Does the owner want those written down as a
   stated requirement? Until then they are listed as unmapped.
2. Sections. HO 28 leaves open what identifies a section, so `Section_*` columns stay on `hadiths`.
   R-27 is "3NF except sections". Whether that exception counts as meeting the 3NF requirement is the
   owner's call.
3. Embedding model: resolved 2026-10-04. The current model is `masterofaudio2077/Fada_ar_embedding` at
   64 dimensions (HO 33). HO 23 describes the first Arabic model and is marked superseded. R-37 still
   states only the behaviour, which did not change.
4. Corpus size: resolved 2026-10-04. The loaded `hadiths` table has 33,064 rows; 33,491 is the size
   after the first-stage drop, before the second (CD, ARCH, WIKI now say so). No requirement or test
   depends on the number.
5. `docs/ARCHITECTURE.md`: resolved 2026-10-04. It now names `services/retrieval.py` and
   `services/ranking.py`, reads the index from the `terms` and `postings` tables, and shows the current
   tables (HO 33). R-01 already followed CLAUDE and HO.
6. README "Search results are cacheable for 5 minutes" and "the evaluation uses a stratified sample of
   2000 hadiths": the first is in code (`CACHE_SECONDS = 300`), the second is only a text string in
   `backend/routers/benchmark.py`. No test checks the 2000 figure and no doc says where the sample is
   built, so it is not a requirement here.
7. The README "Business requirements" section was added while this file was being written and may
   change. Section names above are quoted as they were on 2026-10-04.
8. R-13 and R-14 rely on `queries.json`, which HO says is missing from the repo; the tests supply
   their own queries. Whether production needs a documented way to get it is not stated.

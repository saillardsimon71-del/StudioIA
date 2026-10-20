# Studio IA V2 social automation implementation plan

> Execution: native in this session, as requested with "Lance". Implement with superpowers:executing-plans and test each task before shipping.

Goal: Turn approved V2 exports into a durable X queue and a consent-based formation funnel.

Architecture: V2 SQLite remains the episode source. A locked, atomically written data/x_queue.json holds publishing state. One scheduler scans episodes, publishes via the configured transport, and delivers opted-in DMs. A separate public funnel app exposes no Studio administration endpoints. Kit handles confirmed subscriber sequencing.

Tech stack: Python, existing FastAPI/Pydantic, SQLite, requests-oauthlib, FFmpeg, ReportLab, plain HTML/CSS.

Spec: User request "AUTOMATION X + FUNNEL FORMATION STUDIO IA V2" dated 2026-10-20.

## Global constraints

- Modify V2 and the new GitHub workflow only. Leave V1 untouched.
- Four original posts per approved export; standard X length, no invented analytics or prices.
- Slots 09:00, 14:00, 18:00 Europe/Paris; persisted jitter 5-15 minutes; at most four actual posts per local date.
- No real fixture publication, no fixture DM to a customer, no GPU generation in tests.
- Persist a publishing intent before transmission; ambiguous responses require reconciliation, never blind retries.
- Credentials, queues, contacts, browser sessions and generated user media never enter Git.
- API is the supported transport. Browser transport is explicit opt-in, contrary to current X automation rules, and never an automatic API-error fallback.
- Automated creation/publication does not qualify for Original Content Rewards under current X policy. No monetization or shadowban guarantee.
- Repeated keyword CTAs are disabled by default. A dedicated opt-in campaign may receive STUDIO replies, with one delivery per user/campaign and STOP support.
- Public subscription requires consent and an operational email provider. No success response for missing provider credentials.
- Course price is EUR 197; checkout must be explicitly configured, not redirected to the unrelated closed 19-35 EUR offers.
- GitHub scheduled workflows execute from the default branch only. Self-hosted execution must point to persistent V2 data; hosted runners cannot read a local v2.db.

## Review focus

- DST and a fourth generated post crossing midnight.
- Repeated scheduler executions, concurrent writers and uncertain network results.
- An episode whose latest export no longer corresponds to current approved takes.
- Duplicate opt-ins, STOP, unsubscribed addresses and incomplete provider setup.
- Expired media paths, failed uploads and public traversal attempts.

## Tasks

- [x] Queue and generation: social/common.py and social/x_automation.py. Tests assert four unique posts, approval gating, source costs, no analytics invention, local schedule and atomic/idempotent enqueue.
- [x] Transports and scheduler: social/x_client.py and social/x_scheduler.py. Tests use a local HTTP server and a real queue to exercise API payloads, media processing, daily cap, duplicate prevention, uncertain outcomes and today_posts.txt.
- [x] Funnel: social/funnel.py, social/funnel_server.py, social/funnel.json and web/funnel/. Tests exercise exact STUDIO consent, one DM, STOP, subscription provider errors and safe file delivery.
- [x] Lead magnet: creative-guides/00_showrunner.md and social/build_lead_magnet.py. Generate the four-guide PDF and visually inspect its rendering.
- [x] Integration: enqueue after approved review/export; separate public application; API endpoints never publish a synthetic fixture.
- [x] Deployment: .github/workflows/x_post.yml, self-hosted cron installer and deployment instructions. Test the same command used by the scheduler. Never depend on hosted artifact storage for private queue/lead state.
- [ ] Run the complete V2 suite with isolated Modal credentials, check V1 diff, review, commit and push arena/studio-ia-v2-rebuild.

## Baseline

19 tests collected: 18 passed; test_check_modal_no_gpu_invocation assumes no Modal credentials and fails on this authenticated host. Run the suite with a separate empty MODAL_CONFIG_PATH to keep the baseline offline. No production change is justified by that environment-dependent assertion.

## Verification before final review

53/53 V2 tests passed with isolated Modal credentials. Real FFmpeg fixture, loopback X API, Kit HTTP transport, PowerShell AST, YAML and Python compilation checked. PDF six pages rendered and inspected. Desktop/mobile landing inspected with Chromium; no horizontal overflow or JavaScript errors. Live posting, actual DM delivery, registration, hosting and checkout cannot be activated without the absent credentials/provider/checkout.

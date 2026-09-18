# Prompt 19 — Research reports and optional local dashboard

Read the shared agent instructions, architecture, contracts, evaluation policy and current status before editing. Implement this milestone in the existing repository; run its tests and update the ledger.


Implement useful offline JSON, CSV, Markdown and HTML reports over authoritative run/artifact data. No external tracker account is required. Reports show model/size/parameters, exact mixture, unique/repeated exposure, optimized loss and independent CE/BPB, benchmark components/coverage, compute/memory, seed results, failures, comparison eligibility and uncertainty. Do not rank incomplete, incompatible or synthetic fixture scores as real research winners.

Add data reports: source admission/probe state, retained/rejected documents and bytes, duplicate/lineage summaries, quality distributions, source/token mixture drift, repetition risk, storage use and blocked components. Dataset/sample previews must escape content, redact secrets and never include sealed text or label-bearing exclusion details. Static reports should remain readable after moving them with their small assets.

Implement `xlm report --run|--campaign`, `xlm runs list`, and an optional lightweight local read-only dashboard, preferably one Python UI rather than a second React/backend application. The dashboard reuses existing query/report services and cannot mutate experiment configs or launch final evaluation. Bind to loopback by default; refuse public binding without an explicit authenticated deployment configuration. No mandatory network telemetry.

Show learning curves against tokens, bytes and training time in separate views; distinguish raw points from smoothing. Display interrupted/failed runs rather than hiding them. Mark metric provenance, task split, evaluator hash, limits and whether an outcome is development-exposed. Separate operation-count estimates from measured hardware time.

Acceptance: generate a readable report from the actual offline demo and toy campaign; HTML-injection fixture is escaped; missing metrics do not become zeroes; partial suite labels remain visible; comparison violations/seed counts are visible; no protected fields in exports; dashboard optional dependencies do not break headless training. Add screenshot/manual rendering checks where the environment supports them and identify any UI checks NOT RUN.


Finish with actual commands/results, artifact paths, blockers and the next prompt. Never label mocks, skipped tests or unrun research campaigns as verified.

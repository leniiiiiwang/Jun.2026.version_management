# Evidence, risk, and bounded collection

## Scope and query matrix

Before any network/login action, Checkpoint 1 confirms scope, budget, destination, platform, named profile, and approvals. Build six queries: **exact role**, **interview**, **salary**, **experience**, **cohort**, and **aliases** (company/brand/role variants). The matrix should cover role content, selection process, compensation, work experience, and recruiting cohort/city.

Search defaults: 6 keywords × 10 results in 3 batches × 2. Use one MCP/browser session per batch, wait >=12 seconds between queries, and >=180 seconds/3 min between batches. The scripts enforce only within-batch minimums; interbatch orchestration is instruction-level. ID dedupe first, then normalized-title dedupe. Filter marketing, adjacent roles, low-information notes, empty comments, and unverifiable image claims. High engagement is relevance, not truth. Sparse searches do not automatically retry.

## Details, risk stops, and images

At Checkpoint 2, present the candidate sample before details. Detail defaults are <=18 selected notes, 3 batches × <=6, `max_comments 10`, >=20 seconds between details, and >=300 seconds/5 min between batches. Use one MCP/browser session per batch and explicit headless search/detail collection; no visible fallback.

For `search_timeout`, save the failure, use no retry, and continue with the remaining items. `captcha_detected`, `search_blocked`, and `risk_cooldown_active` are hard stops: retain the current record, stop the batch, and report the code. Headed login/manual repair only occurs outside an active batch after user approval. Download images at >=2 seconds apart.

For Zhihu, run `login_zhihu` visibly only when needed, then `search_zhihu` and `get_zhihu_question` in one persistent session per batch. Use the same checkpoints, same budgets, no-retry behavior, and evidence rules. A platform risk/verification/login restriction stops collection; never bypass it. Xiaohongshu hard-stop codes are not asserted to apply identically to Zhihu. Normalize saved Zhihu sources for the document/source index. The image downloader only applies to successful Xiaohongshu detail envelopes.

## Evidence grades and permitted claims

Use A/B/C as claim strength, not author credibility.

| Grade | Evidence | Permitted wording |
| --- | --- | --- |
| A | Direct, attributable post text, screenshot visually checked, or author statement tied to the claim | “来源称/作者表示/截图显示”；state source wording and scope. |
| B | Consistent independent reports or a clear but indirect account | “多个公开样本显示/可作为准备线索”；do not state a universal rule. |
| C | Thin, ambiguous, or single unverified indication | “仅见个别样本/待核验”；do not use as a conclusion. |

Keep post text, OCR, author statement, and agent inference separate in the source note. OCR must be marked distinct from source text; uncertain OCR requires a visual check before using material numbers. Empty comments are no evidence. Work experience must be attributed to the author/sample—never to the employer as a fact.

For salary, record dimensions separately: month/pay period, months, bonus, equity, subsidy, city, cohort, role, business line, and exact source wording. Describe it as a public, nonofficial sample with possibly inconsistent scope; it cannot represent the target role or company overall. Education observations are a competition signal, not an official threshold absent an official JD.

## Stopping criteria and writing

Stop when the approved search/detail budget is used, coverage is sufficient for each topic, or a risk code occurs; never chase platform-wide exhaustiveness. At Checkpoint 3, obtain A/B/C retention, filename, and the same-name append/new decision. Retain only approved evidence in the final note and list omissions/limitations.

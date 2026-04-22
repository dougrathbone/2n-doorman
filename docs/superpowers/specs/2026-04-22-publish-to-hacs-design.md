# Publish Doorman to HACS + Home Assistant Brands

**Date:** 2026-04-22
**Status:** Design approved, ready for implementation plan
**Scope:** Get `dougrathbone/2n-doorman` into the HACS default store *and* submit integration brand assets to `home-assistant/brands`.

## Goal

End state: a user running Home Assistant with HACS installed can open HACS → Integrations, search "Doorman", see the correct icon + logo rendered, and install with one click — without the current "Custom repositories" step documented in `README.md`.

## Gap analysis

Current repo state vs. requirements for the HACS default store + brands repo:

| Item | State | Fix |
|---|---|---|
| `custom_components/doorman/` layout | ✅ | — |
| `hacs.json` at repo root | ✅ | — |
| GitHub releases with `doorman.zip` asset | ✅ (v0.2.5) | new release after fixes |
| Repo description + topics | ✅ | — |
| CI passes `hacs/action@main` | ✅ | re-verify after fixes |
| `LICENSE` file at repo root | ❌ missing | add `LICENSE` (MIT, matches README) |
| `manifest.json` `codeowners` | ❌ `[]` | set to `["@dougrathbone"]` |
| `brand/icon.png` (256×256) | ✅ | — |
| `brand/icon@2x.png` (512×512) | ❌ missing | generate from `logo.svg` |
| `brand/logo.png` (landscape) | ❌ non-standard `logo_120.png` | generate landscape PNG from SVG |
| `brand/logo@2x.png` (landscape, 2x) | ❌ missing | generate landscape 2x PNG from SVG |

## Approach

**Logo scope:** icons + landscape logos (not icons-only). The SVG already exists, so generating landscape PNGs is cheap and gives a more polished HACS/HA integrations card.

**Submission order:** brand PR first → wait for merge → HACS default PR. HACS reads icons from the brands repo, so landing the brand first ensures the listing renders correctly from day one.

**Repo workflow:** direct-to-`main` commits, matching the existing pattern in `git log`. No feature branch for Phase 1. External PRs (Phases 2 and 3) pause for user confirmation before `gh pr create`.

## Implementation phases

### Phase 1 — local repo fixes (commits to `main`)

1. **Add `LICENSE` file** — standard MIT license text, copyright holder "Doug Rathbone", year 2026. Matches the existing `License: MIT` claim in `README.md`.
2. **Update `manifest.json`** — set `"codeowners": ["@dougrathbone"]`. Leave other fields unchanged.
3. **Regenerate brand assets from `logo.svg`:**
   - `brand/icon.png` — 256×256, square, transparent background (already exists; regenerate for consistency)
   - `brand/icon@2x.png` — 512×512, square, transparent background
   - `brand/logo.png` — landscape (target 256×128, adjust to SVG aspect ratio), transparent background
   - `brand/logo@2x.png` — landscape (target 512×256), transparent background
   - Delete `brand/logo_120.png` (non-standard, unused after this)
   - All PNGs: lossless-compressed, trimmed to minimize empty space
4. **Bump version to `0.2.6`** in `custom_components/doorman/manifest.json`.
5. **Commit to `main`** with a single commit covering 1–4 (per existing commit style — no Claude attribution).
6. **Tag `v0.2.6` and push** — triggers the existing `release.yml` workflow which produces `doorman.zip`.
7. **Verify** — CI green on `main` after push, `hacs/action` job passes, release created with `doorman.zip` attached.

### Phase 2 — brand PR to `home-assistant/brands`

**Pause point: confirm with user before opening PR.**

1. Fork `home-assistant/brands` to `dougrathbone/brands` via `gh repo fork`.
2. Clone the fork to a temp working directory (keep out of the `ha2n-entry-controls` tree).
3. Create branch `add-doorman-integration`.
4. Add files:
   - `custom_integrations/doorman/icon.png`
   - `custom_integrations/doorman/icon@2x.png`
   - `custom_integrations/doorman/logo.png`
   - `custom_integrations/doorman/logo@2x.png`
5. Commit, push branch.
6. Open PR to `home-assistant/brands:master` with title `Add Doorman custom integration` and body linking to `https://github.com/dougrathbone/2n-doorman`.
7. Report PR URL to user.

### Phase 3 — HACS default store PR

**Pause points: (a) wait for Phase 2 to merge before starting; (b) confirm with user before opening PR.**

1. Fork `hacs/default` to `dougrathbone/default`.
2. Clone the fork to a temp working directory.
3. Create branch `add-2n-doorman`.
4. Append `dougrathbone/2n-doorman` to the `integration` file (alphabetically sorted if the file uses sort order — otherwise end of file).
5. Commit, push branch.
6. Open PR to `hacs/default:main` with title `Add dougrathbone/2n-doorman` and body describing the integration briefly with a link to the repo.
7. Report PR URL to user.

## Verification

After each phase, verify before moving on:

- **Phase 1:** CI run on the `v0.2.6` tag commit is fully green; GitHub release exists with `doorman.zip`; repo now shows MIT license badge auto-detected by GitHub.
- **Phase 2:** Brand PR is merged (user-driven wait — reviewers merge on their schedule).
- **Phase 3:** HACS PR passes its own validation bot; merged (user-driven wait).

## Out of scope

- Bundling brand icons inside `custom_components/doorman/` (HA 2026.3.0+ feature). May do later but not required for this submission.
- Submitting to Home Assistant core as a built-in integration (different, much longer process).
- Any changes to integration functionality, tests, or docs beyond what's strictly needed for the submission.
- Renaming the domain or repo.

## Risks

- **Brand PR rejection** — reviewer may ask for logo tweaks (aspect ratio, padding, visual noise). Low risk since the existing SVG is clean.
- **HACS default PR queue** — can take days/weeks for human review. Nothing to do but wait.
- **Codeowner commitment** — `@dougrathbone` becomes the GitHub-notified maintainer. Already the case de facto; making it explicit.

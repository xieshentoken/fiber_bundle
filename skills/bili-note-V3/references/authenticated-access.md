# Authenticated Bilibili and YouTube Access

## Objective

Use the user's existing Bilibili or YouTube entitlement without exposing reusable authentication secrets to the skill, agent context, logs, or material package.

## Allowed Flow

1. Open the exact video URL in the current Browser, Chrome, or Edge integration.
2. Let the user complete login, membership, CAPTCHA, or other account interaction in the browser when required.
3. Verify whether the page itself can play the requested part and quality.
4. Classify access with `set_access_state.py`. `public_verified`, `auth_required`, `authenticated`, `member_authorized`, `denied`, and `drm_blocked` are Skill process states, not watcher task statuses.
5. Let the authenticated page request its own subtitle and playback information.
6. Pass only non-secret response data or a local, job-owned media copy needed for the current job to local processing. A connector may use a short-lived reference internally, but it must not be written to the job package or logs.
7. Keep temporary signed media references out of the long-term job package and logs.

## Forbidden Flow

- Do not inspect or copy browser cookies, local storage, profiles, passwords, or session databases.
- Do not accept cookie values, `SESSDATA`, authorization headers, or passwords as script arguments.
- Do not write signed media URLs into Markdown, audit files, screenshots, or committed source files.
- Do not use another account, proxy, downloader exploit, screen recording, or DRM circumvention to obtain inaccessible content.

## Edge and Chrome handoff contract

V3 does not read Edge or Chrome profiles. A browser connector/extension keeps the
login session in the browser and returns only:

- the backend name (`edge`, `chrome`, or `generic_browser`);
- visible playback checks (timestamps and an optional near-end check);
- the access classification; and
- a local media copy staged inside the job, with its measured duration and SHA-256.

The local bridge can create a request with either supported desktop browser:

```bash
python3 scripts/browser_handoff.py request JOB --backend edge
```

```bash
python3 scripts/browser_handoff.py request JOB --backend chrome
```

`msedge`, `microsoft-edge`, `google-chrome`, and `generic` are accepted aliases. The
`generic_browser` backend means the active connector chooses Edge or Chrome; it does not
mean that V3 reads whichever browser profile happens to exist on disk.

and record the non-secret handoff with `browser_handoff.py record`. It accepts a
local `--media-path`, never a cookie, profile, authorization header, password, or
remote/signed media URL. If the browser can verify entitlement but the connector
cannot stage safe local media, record `handoff_state=unavailable` and wait for a
user action or a supported connector; do not downgrade to public extraction.

Before reusing a previous job, run `scripts/job_gate.py`. A missing coverage report, legacy
schema, preview media, or failed final audit forces an access/resume path. If the executor
does not expose an Edge or Chrome connector, `scripts/access_preflight.py` must return
`waiting_user_action`; a logged-in desktop browser alone is not an executor capability.

## Access States

- `public_verified`: Available without login. `acquire_public.py` may use `yt-dlp` without cookies and always limits acquisition to one video.
- `auth_required`: The public route returned only a preview or otherwise needs a browser session.
- `authenticated`: The user's logged-in account can view it.
- `member_authorized`: The user's current membership can view it.
- `denied`: The page denies access. Stop.
- `drm_blocked`: The browser can play it, but the approved runtime cannot produce processable local media without bypassing protection. Stop.

## Runtime Limitation

Authenticated media acquisition depends on the active browser integration. If the runtime can verify playback but cannot safely hand off local media or selected frames without exposing credentials, record the limitation and end the extraction route. Never silently fall back to raw cookie extraction.

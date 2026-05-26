# Bambu Cloud Plugin — Discovery Notes

Resolutions for the four open questions in `docs/superpowers/specs/2026-05-26-bambu-cloud-plugin-design.md` §11.

Source tree probed: `/Users/leolobato/Documents/Projetos/Personal/3d/OrcaSlicer-bambulab/` (read-only).

## Q11.1 — OAuth redirect URI

**Finding:** The `redirect_uri` is not hardcoded to a single value but is always a loopback URL of the form
`http://localhost:<port>/callback`. The port is selected at runtime by `choose_loopback_port()`: it defaults to
`41172` (the `auth_constants::LOOPBACK_PORT` constant), scans `41172–41174` for a free port, and can be overridden
via the `ORCA_LOOPBACK_PORT` environment variable. This URI is composed in `OrcaCloudServiceAgent::update_redirect_uri()`
and passed verbatim in both the authorization-code request and the token-exchange POST. Bambu's OAuth server
clearly accepts arbitrary `http://localhost:*` loopback URIs (RFC 8252 loopback redirect) — but nothing in the
source suggests it accepts non-localhost origins. Registering an external gateway URL like
`https://gateway.host/api/cloud/auth/callback` as the redirect URI would require Bambu's backend to whitelist it;
there is no evidence that is possible without Bambu's cooperation.

**Citations:**
- `src/slic3r/Utils/OrcaCloudServiceAgent.hpp:24` — `constexpr int LOOPBACK_PORT = 41172;`
- `src/slic3r/Utils/OrcaCloudServiceAgent.hpp:25` — `constexpr const char* LOOPBACK_PATH = "/callback";`
- `src/slic3r/Utils/OrcaCloudServiceAgent.cpp:1383-1385` — `int selected_port = choose_loopback_port(); pkce_bundle.loopback_port = selected_port; pkce_bundle.redirect = "http://localhost:" + std::to_string(selected_port) + auth_constants::LOOPBACK_PATH;`
- `src/slic3r/Utils/OrcaCloudServiceAgent.cpp:226-234` — `if (const char* env_port = std::getenv("ORCA_LOOPBACK_PORT")) { ... base_port = parsed; }`
- `src/slic3r/GUI/Jobs/OAuthJob.cpp:76` — `.form_add("redirect_uri", _data.params.callback_url)`

**Implication for bambu-gateway:**
- The primary OAuth callback path (registering our gateway's `/api/cloud/auth/callback`) is **not viable** without
  Bambu's explicit backend cooperation to whitelist a non-localhost redirect URI. The loopback pattern is structurally
  hardcoded into OrcaSlicer's approach, and there is no evidence Bambu allows arbitrary origins.
- Fallback A (loopback intercept) is exactly what OrcaSlicer implements. For bambu-gateway this is only workable
  if the gateway runs on the same machine as the user's browser (e.g., local-only deployments). For remote-server
  deployments (e.g., the production server at 10.0.1.9), `http://localhost:<port>/callback` would resolve on the
  *user's* browser machine, not the server — so we cannot receive the callback server-side.
- Fallback B (paste) remains functional for all deployment topologies.

**Resulting plan for §6.2 of the spec:**
- **BLOCKER for primary OAuth callback path.** We cannot register a remote gateway URL as `redirect_uri` without
  Bambu's cooperation; the loopback-only pattern is enforced by OrcaSlicer's implementation and almost certainly
  by Bambu's backend whitelist.
- For Phase 4 (auth), commit to **Fallback B (paste fallback)** as the safe baseline for remote deployments,
  with an optional **Fallback A (loopback intercept)** mode for users who run the gateway on their local machine.
  The login UI should instruct the user to complete sign-in in their browser and paste the post-redirect URL back
  into the gateway, from which the gateway extracts the `code` and `state` parameters and completes the token exchange.

## Q11.2 — Two-3MF export from orcaslicer-headless

(unanswered — see Task 0.3)

## Q11.3 — Plugin CDN endpoint

(unanswered — see Task 0.4)

## Q11.4 — change_user() payload shape

(unanswered — see Task 0.5)

## Verified CDN reachability

(unanswered — see Task 0.6)

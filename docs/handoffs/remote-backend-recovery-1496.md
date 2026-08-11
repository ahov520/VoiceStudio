# Remote backend recovery handoff (#1496)

This draft PR is a handoff for the remote-backend recovery work described in
[#1496](https://github.com/debpalash/VoiceStudio/issues/1496). It is based on
`main`; PR #1495 adds the remote-worker control plane involved in the report.

## Observed failure

Saving `https://192.168.0.110:7443` as the Remote backend makes the desktop
WebView request `/model/status` and `/setup/status` from that URL. The WebView
rejects the self-signed certificate, and the app can fall back into the local
model setup path even though the configured backend is remote.

## Important distinction

- `7443` is the remote-worker gRPC/TLS control-plane port.
- The Remote backend setting expects the HTTP VoiceStudio API, normally on
  `3900` (or another explicitly configured HTTP API port).
- Certificate verification must remain enabled. The fix should explain a
  certificate/transport failure rather than weakening TLS validation.

## Intended implementation

1. Add a bounded remote-backend probe that classifies certificate, network,
   CORS, wrong-port/service, and HTTP failures.
2. If `ov_backend_url` is configured, never route `/setup/status` failure into
   the local `SetupWizard`; remote model installation belongs to the remote
   host.
3. Add a recoverable connection screen with `Retry`, `Use local backend` /
   `Disable remote backend`, and a Settings link.
4. Clearing the remote backend must remove `ov_backend_url`, clear its API key
   when appropriate, and reload into the local backend without reinstalling.
5. Add frontend regressions for the setup gate, TLS/transport classification,
   recovery action, and all 21 locale translations.

## Verification evidence

The PR #1495 desktop session was run with the local ports moved to avoid an
existing VoiceStudio instance: frontend `3911`, backend `3910`. Setting
`OMNIVOICE_UI_PORT=3911` fixed local CORS. The remaining failure was the
remote target's rejected TLS certificate on `192.168.0.110:7443`.

This PR intentionally contains the investigation and acceptance contract; a
contributor can implement the UI and gate changes directly on this branch.
